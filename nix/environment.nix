# SPDX-License-Identifier: MIT
{
  lib,
  runCommand,
  fetchurl,
  python3,
  writeText,
  nativeStack,
  toolchain,
  stdenv,
  purifier,
}:
{
  lock,
  fetchers ? { },
  nativeBuildInputs ? [ ],
}:
let
  checked = purifier.checkLock lock;
  profile =
    checked.data.profiles.${stdenv.hostPlatform.system}
      or (throw "stack-purifier: generate a lock profile on ${stdenv.hostPlatform.system}");
  sources = purifier.tree (
    {
      name = "locked-stack-sources";
      entries = profile.entries;
      inherit runCommand fetchurl;
    }
    // fetchers
  );
  metadata = writeText "stack-build-inputs.json" (
    builtins.toJSON {
      inherit profile;
      inherit (checked.data) inputs manifests;
    }
  );
  python = python3.withPackages (p: [ p.pyyaml ]);
in
assert lib.assertMsg (
  profile.toolVersions.stack == nativeStack.version
) "stack-purifier: native SDK changed; run stack-purifier lock";
runCommand "purified-stack-environment"
  {
    nativeBuildInputs = [
      toolchain
      python
    ]
    ++ nativeBuildInputs;
  }
  ''
    export PYTHONDONTWRITEBYTECODE=1
    python ${../stack_purifier/build.py} "$out" ${metadata} ${sources}
    ln -s ${toolchain} "$out/sdk"
    ln -s ${sources} "$out/downloads"
  ''
