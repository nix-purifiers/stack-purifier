#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Native graph resolution, offline development, external pins and source releases."""

import json, os, shutil, subprocess, sys, tempfile, tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    result = subprocess.run(list(map(str, args)), text=True, **kwargs)
    if result.returncode:
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        result.check_returncode()
    return result


case = sys.argv[1]
with tempfile.TemporaryDirectory(prefix="stack-conformance-") as temporary:
    temp = Path(temporary)
    cli = temp / "cli"
    native = temp / "sdk"
    run(["nix", "build", str(REPO) + "#cli", "--out-link", cli])
    run(["nix", "build", str(REPO) + "#native", "--out-link", native])
    helper = cli / "bin/stack-purifier"
    stack = cli / "bin/stack"
    raw = native / "bin/stack"
    root = temp / "project"
    shutil.copytree(
        REPO / "examples/minimal",
        root,
        ignore=shutil.ignore_patterns(
            ".stack-work", ".stack-purifier", "*.lock", "*.json"
        ),
    )
    if case == "snapshot":
        (root / "stack.yaml").write_text(
            "snapshot: lts-24.24\npackages: [.]\nextra-deps: [safe-0.3.21]\nsystem-ghc: true\ninstall-ghc: false\n"
        )
        recipe = root / "example.cabal"
        recipe.write_text(
            recipe.read_text().replace(
                "split == 0.2.5", "split == 0.2.5, safe == 0.3.21"
            )
        )
    env = {k: v for k, v in os.environ.items() if not k.startswith("STACK_PURIFIER_")}
    env["STACK_ROOT"] = str(temp / "ordinary-root")
    env["PANTRY_ROOT"] = str(temp / "ordinary-pantry")
    args = [helper, "--project", root]
    run([*args, "lock"], env=env)
    companion = root / "stack-purifier.lock.json"
    tool = root / "stack.yaml.lock"
    if case == "snapshot":
        profile = next(iter(json.loads(companion.read_text())["profiles"].values()))
        classes = {d["name"]: d["fromSnapshot"] for d in profile["dependencies"]}
        assert classes["split"] and not classes["safe"], classes
    initial = companion.read_bytes()
    original = tool.read_bytes()
    run([*args, "lock"], env=env)
    assert initial == companion.read_bytes() and original == tool.read_bytes()
    if case == "relock":
        data = json.loads(initial)
        data["contentHash"] = "bad"
        companion.write_text(json.dumps(data))
        run([*args, "lock"], env=env)
        assert tool.read_bytes() == original
        tool.write_text("broken: [")
        run([*args, "lock"], env=env)
        assert tool.read_bytes() == original
    elif case == "update":
        run([*args, "update", "split"], env=env)
        run([*args, "update"], env=env)
        assert "split-0.2.5@sha256:" in tool.read_text()
    elif case == "external":
        outside = temp / "locks"
        outside.mkdir()
        selected = outside / "companion.json"
        selected_tool = outside / "stack.lock"
        args = [
            helper,
            "--project",
            root,
            "--lockfile",
            selected,
            "--tool-lockfile",
            selected_tool,
        ]
        run([*args, "lock"], env=env)
        assert companion.read_bytes() == initial and tool.read_bytes() == original
        companion.unlink()
        tool.write_text("intentionally invalid working native lock\n")
        env.update(
            STACK_PURIFIER_LOCKFILE=str(selected),
            STACK_PURIFIER_TOOL_LOCKFILE=str(selected_tool),
        )
    elif case not in ("native", "snapshot"):
        raise ValueError(case)
    environment = Path(
        run([*args, "prefetch"], env=env, capture_output=True).stdout.strip()
    )
    offline = {
        **env,
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
    }
    run([stack, "build"], cwd=root, env=offline)
    value = run([stack, "run"], cwd=root, env=offline, capture_output=True).stdout
    assert '["locked","Stack"]' in value, value
    installed = Path(
        run(
            [stack, "path", "--snapshot-install-root"],
            cwd=root,
            env=offline,
            capture_output=True,
        ).stdout.strip()
    )
    assert installed == environment / "dependencies", installed
    # The editor/REPL sees the same immutable package database.
    editor = run(
        [stack, "exec", "--", "ghc", "-fno-code", "-package", "split", "Main.hs"],
        cwd=root,
        env=offline,
        capture_output=True,
    )
    assert "Compiling Main" in editor.stdout or "Compiling Main" in editor.stderr
    run([stack, "test"], cwd=root, env=offline)
    assert (
        not Path(env["STACK_ROOT"]).exists() and not Path(env["PANTRY_ROOT"]).exists()
    )
    if case == "external":
        assert tool.read_text() == "intentionally invalid working native lock\n"
    if case == "native":
        repl = run(
            [stack, "repl", "--no-load"],
            cwd=root,
            env=offline,
            input='import Data.List.Split\nprint (splitOn "," "locked,REPL")\n:quit\n',
            capture_output=True,
        )
        assert '["locked","REPL"]' in repl.stdout, (repl.stdout, repl.stderr)
        source = root / "Main.hs"
        source.write_text(source.read_text() + "-- application edit\n")
        assert (
            Path(
                run(
                    [*args, "prefetch"], env=offline, capture_output=True
                ).stdout.strip()
            )
            == environment
        )
        run([stack, "build"], cwd=root, env=offline)
        bad = subprocess.run(
            [str(stack), "build", "--flag", "split:bogus"],
            cwd=root,
            env=offline,
            capture_output=True,
            text=True,
        )
        assert bad.returncode and "configuration overrides" in bad.stderr, bad.stderr
        (root / "example.cabal").write_text(
            (root / "example.cabal").read_text() + "\n-- manifest edit\n"
        )
        stale = subprocess.run(
            [str(stack), "build"], cwd=root, env=offline, capture_output=True, text=True
        )
        assert stale.returncode and "manifests changed" in stale.stderr, stale.stderr
        (root / "example.cabal").write_bytes(
            (REPO / "examples/minimal/example.cabal").read_bytes()
        )
        record = temp / "gh-calls"
        fake = temp / "gh"
        fake.write_text(
            "#!/usr/bin/env python3\nimport sys,json\nfrom pathlib import Path\np=Path("
            + repr(str(record))
            + ')\na=sys.argv[1:]\nwith p.open("a") as f:f.write(json.dumps(a)+"\\n")\nif a[:2]==["release","view"]:sys.exit(0 if len(p.read_text().splitlines())>2 else 1)\n'
        )
        fake.chmod(0o755)
        for _ in range(2):
            run(
                [*args, "publish", "--repo", "fixture/project", "--tag", "v0.1.0"],
                env={**env, "STACK_PURIFIER_GH": str(fake)},
            )
        calls = [json.loads(v) for v in record.read_text().splitlines()]
        assert calls[1][:2] == ["release", "create"] and calls[3][:2] == [
            "release",
            "upload",
        ], calls
        archive = next(v for v in calls[1] if v.endswith("/project.tar.gz"))
        with tarfile.open(archive) as release:
            assert {
                "./stack.yaml",
                "./stack.yaml.lock",
                "./stack-purifier.lock.json",
            } <= set(release.getnames())
        empty = temp / "unlocked"
        empty.mkdir()
        baseline = run(
            [raw, "--numeric-version"], env=env, cwd=empty, capture_output=True
        )
        fallback = run(
            [stack, "--numeric-version"], env=env, cwd=empty, capture_output=True
        )
        assert (
            baseline.stdout == fallback.stdout
            and "ordinary impure stack" in fallback.stderr
        )
    print("Stack native conformance passed:", case)
