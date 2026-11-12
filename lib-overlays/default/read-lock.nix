# SPDX-License-Identifier: MIT
{ lib }:
{
  src,
  lockFile ? src + "/stack-purifier.lock.json",
  stackLock ? src + "/stack.yaml.lock",
}:
let
  exists = builtins.pathExists lockFile && builtins.pathExists stackLock;
  raw = if exists then builtins.fromJSON (builtins.readFile lockFile) else { };
  data = if builtins.isAttrs raw then raw else { };
  relative =
    p:
    builtins.isString p
    && p != ""
    && !(lib.hasPrefix "/" p)
    && builtins.all (v: v != "" && v != "." && v != "..") (lib.splitString "/" p)
    && !(lib.hasInfix "\\" p);
  hashOK = h: builtins.isString h && builtins.match "sha256-[A-Za-z0-9+/]{43}=" h != null;
  inputs = data.inputs or [ ];
  manifests = data.manifests or { };
  profiles = data.profiles or { };
  inputOK =
    i:
    builtins.isAttrs i
    && relative (i.path or null)
    && (i ? hash)
    && (i.hash == null || builtins.isString i.hash)
    && (
      if i.hash == null then
        !(builtins.pathExists (src + "/${i.path}"))
      else
        builtins.pathExists (src + "/${i.path}")
        && builtins.hashFile "sha256" (src + "/${i.path}") == i.hash
        && builtins.isString (manifests.${i.path} or null)
        && builtins.hashString "sha256" manifests.${i.path} == i.hash
    );
  entryOK =
    system: e:
    builtins.isAttrs e
    && relative (e.path or null)
    && lib.hasPrefix "${system}/" e.path
    && hashOK (e.hash or null)
    && (e.mirrors or null) == [ ]
    && builtins.isString (e.url or null)
    && lib.hasPrefix "https://hackage-content.haskell.org/package/" e.url;
  depOK =
    entries: d:
    builtins.isAttrs d
    && builtins.isString (d.name or null)
    && builtins.match "[A-Za-z0-9]+(-[A-Za-z0-9]+)*" d.name != null
    && builtins.isString (d.version or null)
    && builtins.match "[0-9]+(\\.[0-9]+)*" d.version != null
    && builtins.elem (d.archive or null) (map (e: e.path) entries)
    && builtins.elem (d.cabal or null) (map (e: e.path) entries)
    && builtins.isAttrs (d.location or null)
    && builtins.isString (d.location.hackage or null)
    && lib.hasPrefix "${d.name}-${d.version}@sha256:" d.location.hackage
    && builtins.match "[A-Za-z0-9.-]+@sha256:[0-9a-f]{64},[0-9]+" d.location.hackage != null
    && (
      let
        cabal = builtins.head (lib.filter (e: e.path == d.cabal) entries);
        hex = builtins.substring 0 64 (builtins.elemAt (lib.splitString "@sha256:" d.location.hackage) 1);
      in
      cabal.hash == builtins.convertHash {
        hash = hex;
        hashAlgo = "sha256";
        toHashFormat = "sri";
      }
    )
    && builtins.isAttrs (d.location.pantry-tree or null)
    && builtins.isString (d.location.pantry-tree.sha256 or null)
    && builtins.match "[a-f0-9]{64}" d.location.pantry-tree.sha256 != null
    && builtins.isInt (d.location.pantry-tree.size or null)
    && builtins.isAttrs (d.flags or null)
    && builtins.all builtins.isBool (builtins.attrValues d.flags)
    && builtins.isList (d.ghcOptions or null)
    && builtins.all builtins.isString d.ghcOptions
    && (d.cabalConfigOpts or null) == [ ]
    && builtins.isBool (d.hidden or null)
    && builtins.isBool (d.fromSnapshot or null);
  profileOK =
    system: p:
    builtins.elem system [
      "x86_64-linux"
      "aarch64-linux"
    ]
    && builtins.isAttrs p
    && (p.system or null) == system
    && (p.ghc or null) == "9.10.3"
    && builtins.isAttrs (p.toolVersions or null)
    && builtins.isString (p.toolVersions.stack or null)
    && builtins.isList (p.entries or null)
    && builtins.all (entryOK system) p.entries
    && builtins.length (lib.unique (map (e: e.path) p.entries)) == builtins.length p.entries
    && builtins.isList (p.dependencies or null)
    && builtins.all (depOK p.entries) p.dependencies
    && builtins.isList (p.projects or null)
    && p.projects != [ ]
    && builtins.all (v: v == "." || relative v) p.projects;
  problems =
    lib.optionals (!exists) [ "missing lockfiles; run stack-purifier lock" ]
    ++ lib.optionals (
      exists
      && (
        (data.version or null) != 1
        ||
          (data.contentHash or null)
          != builtins.hashString "sha256" (builtins.toJSON (builtins.removeAttrs data [ "contentHash" ]))
      )
    ) [ "invalid companion seal; run stack-purifier lock" ]
    ++ lib.optionals (
      exists && (!builtins.isList inputs || inputs == [ ] || !(builtins.all inputOK inputs))
    ) [ "project manifests changed; run stack-purifier lock" ]
    ++ lib.optionals (
      exists
      && (
        (data.nativeLock or null) != builtins.readFile stackLock
        || (data.nativeHash or null) != builtins.hashFile "sha256" stackLock
      )
    ) [ "native Stack lock changed; run stack-purifier lock" ]
    ++ lib.optionals (
      exists
      && (
        !builtins.isAttrs profiles
        || profiles == { }
        || !(lib.all (s: profileOK s profiles.${s}) (builtins.attrNames profiles))
      )
    ) [ "invalid native compiler profiles; run stack-purifier lock" ];
  entries = lib.concatMap (p: p.entries or [ ]) (builtins.attrValues profiles);
in
{
  purifier = "stack";
  inherit data entries;
  inherit problems;
  outputs = {
    packages = [
      "default"
      "environment"
      "project"
    ];
    tests = [ "default" ];
    release = [ "default" ];
  };
}
