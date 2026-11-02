# SPDX-License-Identifier: MIT
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import yaml
from . import lock
from .build import configuration

NATIVE = os.environ.get("STACK_PURIFIER_NATIVE", "stack")
SYSTEM = os.environ.get("STACK_PURIFIER_SYSTEM", "x86_64-linux")
SDK = os.environ.get("STACK_PURIFIER_SDK", "")
HERE = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    result = subprocess.run(list(map(str, args)), text=True, **kwargs)
    if result.returncode and result.stderr:
        print(result.stderr, file=sys.stderr)
    result.check_returncode()
    return result


def clean_environment():
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("STACK_PURIFIER_", "STACK_")) and k != "PANTRY_ROOT"
    }


def versions():
    return {
        "stack": run([NATIVE, "--numeric-version"], capture_output=True).stdout.strip(),
        "ghc": "9.10.3",
        "resolver": hashlib.sha256(
            b"".join(p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py")))
        ).hexdigest(),
    }


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    temporary.write_text(text)
    temporary.replace(path)


def checked(root, native, companion):
    data = json.loads(companion.read_text())
    if problems := lock.problems(root, native, data, SYSTEM):
        raise ValueError("\n".join(problems))
    if data["profiles"][SYSTEM]["toolVersions"]["stack"] != versions()["stack"]:
        raise ValueError("native Stack SDK changed" + lock.FIX)
    return data


def resolve(root, native, companion, update=False, names=()):
    previous = None
    try:
        candidate = json.loads(companion.read_text())
        if candidate.get("contentHash") == lock.seal(candidate)["contentHash"]:
            previous = candidate
        if (
            not update
            and not lock.problems(root, native, candidate, SYSTEM)
            and candidate["profiles"][SYSTEM]["toolVersions"] == versions()
        ):
            print("Existing native Stack selections retained", file=sys.stderr)
            return
    except (OSError, ValueError, KeyError, TypeError):
        pass
    with tempfile.TemporaryDirectory(prefix="stack-purifier-lock-") as temporary:
        temp = Path(temporary)
        stage = temp / "project"
        shutil.copytree(
            root,
            stage,
            ignore=shutil.ignore_patterns(
                ".git",
                ".stack-purifier",
                ".stack-work",
                "result",
                "*.log",
                "dist-newstyle",
            ),
        )
        selected = stage / "stack.yaml.lock"
        if native.is_file():
            try:
                parsed_native = yaml.safe_load(native.read_text())
                if (
                    not isinstance(parsed_native, dict)
                    or not isinstance(parsed_native.get("packages"), list)
                    or not isinstance(parsed_native.get("snapshots"), list)
                ):
                    raise ValueError("invalid native lock structure")
                shutil.copyfile(native, selected)
            except (ValueError, yaml.YAMLError):
                if previous:
                    selected.write_text(previous["nativeLock"])
        elif previous:
            selected.write_text(previous["nativeLock"])
        env = clean_environment()
        cache = (
            Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
            / "stack-purifier"
            / ("resolver-" + versions()["stack"])
        )
        cache.mkdir(parents=True, exist_ok=True)
        env.update(
            STACK_ROOT=str(cache), STACK_PURIFIER_CAPTURE=str(temp / "capture.json")
        )
        # Completed Pantry locations can be satisfied through Casa without ever
        # importing Hackage's security index. Archive hashes and revision URLs
        # must still come from that authenticated native index.
        if not (cache / "pantry/hackage/00-index.tar").is_file():
            run(
                [NATIVE, "--stack-yaml", stage / "stack.yaml", "--no-nix", "update"],
                env=env,
            )
        if update:
            run(
                [NATIVE, "--stack-yaml", stage / "stack.yaml", "--no-nix", "update"],
                env=env,
            )
            if selected.is_file():
                pins = yaml.safe_load(selected.read_text()) or {}
                if names and not all(
                    any(
                        v["original"].get("hackage", "").startswith(n + "-")
                        for v in pins.get("packages", [])
                    )
                    for n in names
                ):
                    raise ValueError(
                        "named updates require locked Hackage package names"
                    )
                pins["packages"] = [
                    v
                    for v in pins.get("packages", [])
                    if names
                    and not any(
                        v["original"].get("hackage", "").startswith(n + "-")
                        for n in names
                    )
                ]
                if not names:
                    pins["snapshots"] = []
                selected.write_text(yaml.safe_dump(pins))
        # Native Stack resolves the graph, effective flags and complete Pantry
        # locations. No dependency is compiled while locking.
        graph = json.loads(
            run(
                [
                    NATIVE,
                    "--stack-yaml",
                    stage / "stack.yaml",
                    "--no-nix",
                    "--system-ghc",
                    "--no-install-ghc",
                    "ls",
                    "dependencies",
                    "json",
                    "--test",
                    "--bench",
                ],
                env=env,
                capture_output=True,
            ).stdout
        )
        captured = json.loads((temp / "capture.json").read_text())
        projects = []
        paths = ["stack.yaml"]
        for node in graph:
            location = node.get("location", {})
            if location.get("type") == "project package":
                local = Path(location["url"].removeprefix("file://")).resolve()
                relative = local.relative_to(stage)
                name = str(relative)
                projects.append(name)
                files = list(local.glob("*.cabal"))
                if not files:
                    raise ValueError("missing native Cabal description")
                for file in files:
                    rel = str(file.relative_to(stage))
                    paths.append(rel)
                    if not (root / rel).exists():
                        shutil.copyfile(file, root / rel)
                package = local / "package.yaml"
                if package.is_file():
                    paths.append(str(package.relative_to(stage)))
        original = yaml.safe_load((root / "stack.yaml").read_text())
        for project in original.get("packages", ["."]):
            if (
                not isinstance(project, str)
                or (project != "." and not lock.relative(project))
                or any(c in project for c in "*?[")
            ):
                raise ValueError(
                    "project paths must be explicit and inside the working tree"
                )
        snapshot = original.get("snapshot", original.get("resolver"))
        if isinstance(snapshot, str) and not snapshot.startswith(
            ("https://", "http://", "lts-", "nightly-", "ghc-")
        ):
            raise ValueError("local snapshot files are not yet supported")
        entries = []
        dependencies = []
        db = sqlite3.connect(
            "file:" + str(cache / "pantry/pantry.sqlite3") + "?mode=ro", uri=True
        )
        for dep in captured:
            location = dep["location"]
            if not isinstance(location, dict) or "hackage" not in location:
                raise ValueError("only Hackage dependencies are supported")
            ref, revision = location["hackage"].split("@sha256:")
            name = dep["name"]
            version = ref[len(name) + 1 :]
            archive = db.execute(
                "select hex(t.sha),t.size from hackage_tarball t join package_name n on t.name=n.id join version v on t.version=v.id where n.name=? and v.version=?",
                (name, version),
            ).fetchone()
            cabal = db.execute(
                "select h.revision,hex(b.sha),b.size from hackage_cabal h join blob b on h.cabal=b.id join package_name n on h.name=n.id join version v on h.version=v.id where n.name=? and v.version=? and lower(hex(b.sha))=?",
                (name, version, revision.split(",")[0]),
            ).fetchone()
            if not archive or not cabal:
                raise ValueError("missing native Hackage security metadata")
            dep.update(
                version=version,
                archive=SYSTEM + "/" + ref + ".tar.gz",
                cabal=SYSTEM + "/" + ref + ".cabal",
            )
            for field, url, sha in [
                ("archive", ref + ".tar.gz", archive[0]),
                ("cabal", ref + "/revision/" + str(cabal[0]) + ".cabal", cabal[1]),
            ]:
                entries.append(
                    {
                        "path": dep[field],
                        "url": "https://hackage-content.haskell.org/package/" + url,
                        "hash": lock.sri(sha),
                        "mirrors": [],
                    }
                )
            dependencies.append(dep)
        db.close()
        declarations = lock.inputs(root, paths)
        profiles = (
            dict(previous["profiles"])
            if previous and previous["inputs"] == declarations
            else {}
        )
        profiles[SYSTEM] = {
            "system": SYSTEM,
            "ghc": "9.10.3",
            "projects": sorted(projects),
            "dependencies": dependencies,
            "entries": sorted(entries, key=lambda e: e["path"]),
            "toolVersions": versions(),
        }
        text = (
            selected.read_text()
            if selected.exists()
            else "packages: []\nsnapshots: []\n"
        )
        data = lock.seal(
            {
                "version": 1,
                "inputs": declarations,
                "manifests": {
                    v["path"]: (root / v["path"]).read_bytes().decode()
                    for v in declarations
                    if v["hash"] is not None
                },
                "profiles": profiles,
                "nativeLock": text,
                "nativeHash": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
        check = temp / "check.lock"
        check.write_text(text)
        if problems := lock.problems(root, check, data, SYSTEM):
            raise ValueError("\n".join(problems))
        write(native, text)
        write(companion, json.dumps(data, sort_keys=True, indent=2) + "\n")
        print("Locked native Stack graph for " + SYSTEM, file=sys.stderr)


def realize(root, native, companion, target="environment"):
    checked(root, native, companion)
    # Nix syntax uses JSON strings/objects through fromJSON, avoiding interpolation
    # of project paths into expressions.
    expression = (
        "import "
        + str(HERE / "nix/realize.nix")
        + " (builtins.fromJSON "
        + json.dumps(
            lock.canonical(
                {
                    "project": str(root),
                    "nativeLock": str(native),
                    "companion": str(companion),
                    "target": target,
                }
            )
        )
        + ")"
    )
    return Path(
        run(
            [
                "nix",
                "build",
                "--impure",
                "--no-link",
                "--print-out-paths",
                "--expr",
                expression,
            ],
            capture_output=True,
        ).stdout.strip()
    )


def prefetch(root, native, companion):
    environment = realize(root, native, companion)
    gc = root / ".stack-purifier/gcroots"
    gc.mkdir(parents=True, exist_ok=True)
    run(
        [
            "nix-store",
            "--add-root",
            gc / environment.name,
            "--indirect",
            "--realise",
            environment,
        ],
        stdout=subprocess.DEVNULL,
    )
    print(environment)
    return environment


def runtime(root, native, companion, arguments):
    data = checked(root, native, companion)
    protected = (
        "--resolver",
        "--snapshot",
        "--compiler",
        "--stack-yaml",
        "--stack-root",
        "--work-dir",
        "--flag",
        "--ghc-options",
        "--configure-option",
        "--docker",
        "--nix",
        "--no-system-ghc",
        "--install-ghc",
        "--profile",
        "--trace",
        "--library-profiling",
        "--no-library-profiling",
        "--executable-profiling",
        "--no-executable-profiling",
        "--ghc-build",
        "--ghc-variant",
        "--compiler-check",
        "--extra-package-db",
        "--with-gcc",
    )
    for argument in (
        arguments[: arguments.index("--")] if "--" in arguments else arguments
    ):
        if any(argument == key or argument.startswith(key + "=") for key in protected):
            raise ValueError(
                "dependency configuration overrides must be recorded in stack.yaml"
                + lock.FIX
            )
    if arguments and arguments[0] in (
        "update",
        "upgrade",
        "setup",
        "init",
        "new",
        "script",
        "unpack",
        "config",
        "purge",
    ):
        raise ValueError(
            "this native mutation command is not supported in a locked session; use stack-purifier"
            + lock.FIX
        )
    environment = realize(root, native, companion)
    profile = data["profiles"][SYSTEM]
    session = root / ".stack-purifier/sessions" / environment.name
    session.mkdir(parents=True, exist_ok=True)
    config = configuration(
        yaml.safe_load((root / "stack.yaml").read_text()),
        environment,
        [str(root / p) for p in profile["projects"]],
    )
    write(session / "stack.yaml", json.dumps(config, sort_keys=True))
    env = clean_environment()
    env.update(
        STACK_ROOT=str(session / "root"),
        STACK_PURIFIER_INSTALL_ROOT=str(environment / "dependencies"),
        XDG_CACHE_HOME=str(session / "cache"),
    )
    os.execve(
        NATIVE,
        [NATIVE, "--stack-yaml", str(session / "stack.yaml"), "--no-nix", *arguments],
        env,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path)
    parser.add_argument("--lockfile", type=Path)
    parser.add_argument("--tool-lockfile", "--native-lock", type=Path)
    parser.add_argument("--native", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("command", nargs="?")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = (args.project or Path.cwd()).resolve()
    if not args.project:
        while not (root / "stack.yaml").is_file() and root != root.parent:
            root = root.parent
    companion = (
        args.lockfile
        or Path(
            os.environ.get("STACK_PURIFIER_LOCKFILE", root / "stack-purifier.lock.json")
        )
    ).resolve()
    native = (
        args.tool_lockfile
        or Path(
            os.environ.get(
                "STACK_PURIFIER_TOOL_LOCKFILE",
                os.environ.get(
                    "STACK_PURIFIER_NATIVE_LOCKFILE", root / "stack.yaml.lock"
                ),
            )
        )
    ).resolve()
    if args.native:
        arguments = ([args.command] if args.command else []) + args.arguments
        if not companion.exists():
            print(
                "stack-purifier: no lockfile; running ordinary impure stack",
                file=sys.stderr,
            )
            os.execve(
                NATIVE,
                [NATIVE, *arguments],
                {
                    k: v
                    for k, v in os.environ.items()
                    if not k.startswith("STACK_PURIFIER_")
                },
            )
        runtime(root, native, companion, arguments)
        return
    if args.command in ("lock", "update"):
        resolve(root, native, companion, args.command == "update", args.arguments)
    elif args.command == "prefetch":
        prefetch(root, native, companion)
    elif args.command == "publish":
        publish = argparse.ArgumentParser()
        publish.add_argument("--repo", required=True)
        publish.add_argument("--tag", required=True)
        selected = publish.parse_args(args.arguments)
        output = realize(root, native, companion, "release")
        gh = os.environ.get("STACK_PURIFIER_GH", "gh")
        common = ["--repo", selected.repo]
        exists = (
            subprocess.run(
                [gh, "release", "view", selected.tag, *common],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
        run(
            [
                gh,
                "release",
                "upload",
                selected.tag,
                output / "project.tar.gz",
                *common,
                "--clobber",
            ]
            if exists
            else [
                gh,
                "release",
                "create",
                selected.tag,
                output / "project.tar.gz",
                *common,
                "--title",
                selected.tag,
                "--notes",
                "Locked Stack project sources.",
            ]
        )
    else:
        parser.error("expected lock, update, prefetch or publish")


def entry():
    try:
        main()
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        yaml.YAMLError,
        subprocess.CalledProcessError,
    ) as error:
        print("stack-purifier: " + str(error), file=sys.stderr)
        raise SystemExit(1)
