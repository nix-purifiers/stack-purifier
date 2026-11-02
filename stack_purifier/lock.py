# SPDX-License-Identifier: MIT
import base64
import hashlib
import json
import re
from pathlib import Path

SYSTEMS = ("x86_64-linux", "aarch64-linux")
FIX = "; run stack-purifier lock, then stack-purifier prefetch"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def seal(data):
    data = dict(data)
    data.pop("contentHash", None)
    data["contentHash"] = hashlib.sha256(canonical(data).encode()).hexdigest()
    return data


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sri(raw):
    return "sha256-" + base64.b64encode(bytes.fromhex(raw)).decode()


def relative(path):
    return (
        isinstance(path, str)
        and bool(path)
        and not path.startswith("/")
        and all(x not in ("", ".", "..") for x in path.split("/"))
        and "\\" not in path
    )


def inputs(root, paths):
    return [
        {"path": path, "hash": sha(root / path) if (root / path).is_file() else None}
        for path in sorted(set(paths))
    ]


def problems(root, native, data, system=None):
    try:
        if (
            data.get("version") != 1
            or data.get("contentHash") != seal(data)["contentHash"]
        ):
            raise ValueError("invalid companion format or contentHash")
        manifests = data["manifests"]
        paths = [item["path"] for item in data["inputs"]]
        if (
            not paths
            or "stack.yaml" not in paths
            or any(not relative(p) for p in paths)
        ):
            raise ValueError("invalid manifest paths")
        if inputs(root, paths) != data["inputs"]:
            raise ValueError("project manifests changed")
        if set(manifests) != {
            v["path"] for v in data["inputs"] if v["hash"] is not None
        }:
            raise ValueError("incomplete manifest snapshot")
        for item in data["inputs"]:
            if (
                item["hash"] is not None
                and hashlib.sha256(manifests[item["path"]].encode()).hexdigest()
                != item["hash"]
            ):
                raise ValueError("manifest snapshot changed")
        if (
            native.read_text() != data["nativeLock"]
            or sha(native) != data["nativeHash"]
        ):
            raise ValueError("native Stack lock changed")
        profiles = data["profiles"]
        if not profiles or any(s not in SYSTEMS for s in profiles):
            raise ValueError("invalid platform profiles")
        if system and system not in profiles:
            raise ValueError("missing native SDK profile for " + system)
        for platform, profile in profiles.items():
            if profile["system"] != platform or profile["ghc"] != "9.10.3":
                raise ValueError("unsupported native compiler profile")
            entries = profile["entries"]
            by_path = {e["path"]: e for e in entries}
            if len(by_path) != len(entries):
                raise ValueError("duplicate source paths")
            for entry in entries:
                if (
                    not relative(entry["path"])
                    or not entry["path"].startswith(platform + "/")
                    or not re.fullmatch(r"sha256-[A-Za-z0-9+/]{43}=", entry["hash"])
                ):
                    raise ValueError("invalid source hash or path")
                if (
                    entry.get("mirrors") != []
                    or not isinstance(entry["url"], str)
                    or not entry["url"].startswith(
                        "https://hackage-content.haskell.org/package/"
                    )
                ):
                    raise ValueError("unsupported source URL")
            names = set()
            for dep in profile["dependencies"]:
                if not isinstance(dep["fromSnapshot"], bool):
                    raise ValueError("invalid native snapshot classification")
                name = dep["name"]
                version = dep["version"]
                if (
                    not re.fullmatch(r"[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*", name)
                    or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version)
                    or name in names
                ):
                    raise ValueError("invalid native package identity")
                names.add(name)
                for field in ("archive", "cabal"):
                    if dep[field] not in by_path:
                        raise ValueError("missing native source download")
                location = dep["location"]
                if (
                    not location["hackage"].startswith(
                        name + "-" + version + "@sha256:"
                    )
                    or not re.fullmatch(
                        "[a-f0-9]{64}", location["pantry-tree"]["sha256"]
                    )
                    or not isinstance(location["pantry-tree"]["size"], int)
                ):
                    raise ValueError("invalid native Pantry metadata")
                cabal_hash = location["hackage"].split("@sha256:")[1].split(",")[0]
                if by_path[dep["cabal"]]["hash"] != sri(cabal_hash):
                    raise ValueError("native Cabal revision hash changed")
                if (
                    not isinstance(dep["flags"], dict)
                    or any(not isinstance(x, bool) for x in dep["flags"].values())
                    or not all(isinstance(x, str) for x in dep["ghcOptions"])
                ):
                    raise ValueError("invalid native package flags")
                if dep["cabalConfigOpts"]:
                    raise ValueError(
                        "custom dependency configure options are not yet supported"
                    )
            if (
                not isinstance(profile["projects"], list)
                or not profile["projects"]
                or any(p != "." and not relative(p) for p in profile["projects"])
            ):
                raise ValueError("invalid local project paths")
        return []
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
        return [str(error) + FIX]
