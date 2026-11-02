#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import copy, json, shutil, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from stack_purifier import lock

with tempfile.TemporaryDirectory(prefix="stack-eval-") as temporary:
    root = Path(temporary) / "project"
    shutil.copytree(
        REPO / "examples/minimal",
        root,
        ignore=shutil.ignore_patterns(".stack-purifier", ".stack-work"),
    )
    companion = root / "stack-purifier.lock.json"
    native = root / "stack.yaml.lock"
    original = json.loads(companion.read_text())
    native_text = native.read_text()
    expression = (
        "let f=builtins.getFlake "
        + json.dumps(str(REPO))
        + "; r=f.lib.stack-purifier.readLock {src=/. + "
        + json.dumps(str(root))
        + ";}; in {inherit (r) problems outputs entries;}"
    )

    def evaluate():
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            text=True,
            capture_output=True,
        )
        result.check_returncode()
        return json.loads(result.stdout)

    assert evaluate()["problems"] == []
    for mutation in (
        "seal",
        "path",
        "hash",
        "flags",
        "tree",
        "cabal",
        "native",
        "missing",
        "manifest",
    ):
        data = copy.deepcopy(original)
        native.write_text(native_text)
        if not (root / "example.cabal").exists():
            shutil.copyfile(
                REPO / "examples/minimal/example.cabal", root / "example.cabal"
            )
        profile = next(iter(data["profiles"].values()))
        if mutation == "seal":
            data["contentHash"] = "bad"
        elif mutation == "path":
            profile["entries"][0]["path"] = "../escape"
        elif mutation == "hash":
            profile["entries"][0]["hash"] = "bad"
        elif mutation == "flags":
            profile["dependencies"][0]["flags"] = {"bogus": "yes"}
        elif mutation == "tree":
            profile["dependencies"][0]["location"]["pantry-tree"]["sha256"] = "bad"
        elif mutation == "cabal":
            path = profile["dependencies"][0]["cabal"]
            next(e for e in profile["entries"] if e["path"] == path)["hash"] = (
                "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
            )
        elif mutation == "native":
            native.write_text("packages: []\nsnapshots: []\n")
        elif mutation == "missing":
            (root / "example.cabal").unlink()
        elif mutation == "manifest":
            (root / "example.cabal").write_text("changed")
        if mutation != "seal":
            data = lock.seal(data)
        companion.write_text(json.dumps(data))
        assert evaluate()["problems"], mutation
        if mutation == "manifest":
            shutil.copyfile(
                REPO / "examples/minimal/example.cabal", root / "example.cabal"
            )
print("Stack evaluation rejects invalid lock declarations")
