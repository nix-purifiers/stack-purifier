# SPDX-License-Identifier: MIT
"""Materialize native Pantry archives and build a read-only Stack snapshot."""

import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


def prepare(out, metadata, sources):
    profile = metadata["profile"]
    out.mkdir(exist_ok=True)
    (out / "sources").mkdir(exist_ok=True)
    packages = []
    for dep in profile["dependencies"]:
        work = Path.cwd() / ("source-" + dep["name"])
        work.mkdir()
        with tarfile.open(sources / dep["archive"]) as source:
            source.extractall(work, filter="data")
        package = work / (dep["name"] + "-" + dep["version"])
        if not package.is_dir():
            raise ValueError("unexpected native Hackage archive layout")
        shutil.copyfile(sources / dep["cabal"], package / (dep["name"] + ".cabal"))
        archive = out / "sources" / (dep["name"] + ".tar.gz")
        with (
            archive.open("wb") as raw,
            gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as gz,
            tarfile.open(fileobj=gz, mode="w") as tar,
        ):
            for path in sorted(package.rglob("*")):
                if path.is_file():
                    info = tar.gettarinfo(
                        path,
                        arcname=dep["name"]
                        + "-"
                        + dep["version"]
                        + "/"
                        + str(path.relative_to(package)),
                    )
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ""
                    info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                    info.pax_headers = {}
                    with path.open("rb") as source:
                        tar.addfile(info, source)
        packages.append(
            {
                "archive": str(archive),
                "name": dep["name"],
                "version": dep["version"],
                "pantry-tree": dep["location"]["pantry-tree"],
            }
        )
    snapshot = {
        "resolver": "ghc-" + profile["ghc"],
        "packages": [
            package
            for package, dep in zip(packages, profile["dependencies"])
            if dep["fromSnapshot"]
        ],
        "flags": {
            d["name"]: d["flags"] for d in profile["dependencies"] if d["fromSnapshot"]
        },
        "ghc-options": {
            d["name"]: d["ghcOptions"]
            for d in profile["dependencies"]
            if d["fromSnapshot"]
        },
        "hidden": {
            d["name"]: d["hidden"] for d in profile["dependencies"] if d["fromSnapshot"]
        },
    }
    (out / "snapshot.json").write_text(json.dumps(snapshot, sort_keys=True))
    return profile


def configuration(original, environment, projects, profile=None):
    if profile is None:
        profile = json.loads((environment / "metadata.json").read_text())["profile"]
    result = dict(original)
    for key in (
        "resolver",
        "snapshot",
        "compiler",
        "extra-deps",
        "packages",
        "flags",
        "ghc-options",
        "configure-options",
        "package-indices",
        "urls",
        "allow-newer",
        "allow-newer-deps",
        "drop-packages",
    ):
        result.pop(key, None)
    result.update(
        {
            "snapshot": str(environment / "snapshot.json"),
            "packages": projects,
            "system-ghc": True,
            "install-ghc": False,
            "nix": {"enable": False},
            "docker": {"enable": False},
            "casa": {"enable": False},
            "notify-if-nix-on-path": False,
            "notify-if-ghc-untested": False,
        }
    )
    # Keep project-specific flags/options; dependency settings came from Stack's
    # native source map and are captured in the snapshot.
    for key in ("flags", "ghc-options", "configure-options"):
        if key in original:
            result[key] = original[key]
    result["extra-deps"] = [
        {
            "archive": str(environment / "sources" / (dep["name"] + ".tar.gz")),
            "name": dep["name"],
            "version": dep["version"],
            "pantry-tree": dep["location"]["pantry-tree"],
        }
        for dep in profile["dependencies"]
        if not dep["fromSnapshot"]
    ]
    return result


def main():
    import yaml

    out, metadata_file, sources = map(Path, sys.argv[1:4])
    metadata = json.loads(metadata_file.read_text())
    profile = prepare(out, metadata, sources)
    work = Path.cwd() / "project"
    work.mkdir()
    for name, text in metadata["manifests"].items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    original = yaml.safe_load(metadata["manifests"]["stack.yaml"])
    selected = configuration(original, out, profile["projects"], profile)
    (work / "stack.yaml").write_text(json.dumps(selected))
    home = Path.cwd() / "home"
    home.mkdir()
    os.environ.update(
        STACK_ROOT=str(out),
        STACK_PURIFIER_INSTALL_ROOT=str(out / "dependencies"),
        HOME=str(home),
        SOURCE_DATE_EPOCH="1",
    )
    subprocess.run(
        [
            "stack",
            "--no-nix",
            "--stack-yaml",
            str(work / "stack.yaml"),
            "build",
            "--only-dependencies",
            "--test",
            "--bench",
        ],
        check=True,
    )
    (out / "dependencies").mkdir(exist_ok=True)
    for child in out.iterdir():
        if child.name not in ("sources", "snapshot.json", "dependencies"):
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    (out / "metadata.json").write_text(json.dumps(metadata, sort_keys=True))


if __name__ == "__main__":
    main()
