# SPDX-License-Identifier: MIT
{
  lib,
  callPackage,
  runCommand,
  fetchurl,
  python3,
  writeText,
  nativeStack,
  toolchain,
  stdenv,
  purifier,
  gnutar,
  gzip,
}:
{
  src,
  lockFile ? src + "/stack-purifier.lock.json",
  stackLock ? src + "/stack.yaml.lock",
  fetchers ? { },
  nativeBuildInputs ? [ ],
  nativeProducts ? { },
  buildCommand ? ''
    stack --no-nix --stack-yaml "$PWD/session/stack.yaml" build --test --bench --no-run-benchmarks
    stack --no-nix --stack-yaml "$PWD/session/stack.yaml" build --copy-bins --local-bin-path "$out/bin"
  '',
  srcFilter ?
    path: type:
    !(builtins.elem (baseNameOf path) [
      ".git"
      ".stack-purifier"
      ".stack-work"
      "result"
      "__pycache__"
    ]),
}:
let
  lock = purifier.checkLock (
    import ./read-lock.nix { inherit lib; } { inherit src lockFile stackLock; }
  );
  environment = import ./environment.nix {
    inherit
      lib
      runCommand
      fetchurl
      python3
      writeText
      nativeStack
      toolchain
      stdenv
      purifier
      ;
  } { inherit lock fetchers nativeBuildInputs; };
  source = lib.cleanSourceWith {
    inherit src;
    filter = srcFilter;
  };
  python = python3.withPackages (p: [ p.pyyaml ]);
  configure = writeText "configure-stack-project.py" ''
    import json,sys
    from pathlib import Path
    import yaml
    sys.path.insert(0, ${builtins.toJSON "${../stack_purifier}"})
    from build import configuration
    root=Path.cwd()/'project';env=Path(sys.argv[1]);session=Path.cwd()/'session';session.mkdir()
    profile=json.loads((env/'metadata.json').read_text())['profile']
    data=configuration(yaml.safe_load((root/'stack.yaml').read_text()),env,[str(root/p) for p in profile['projects']])
    (session/'stack.yaml').write_text(json.dumps(data))
  '';
  rawProject =
    runCommand "purified-stack-project"
      {
        nativeBuildInputs = [
          toolchain
          python
        ]
        ++ nativeBuildInputs;
      }
      ''
        export HOME="$TMPDIR/home" STACK_ROOT="$TMPDIR/stack-root" STACK_PURIFIER_INSTALL_ROOT=${environment}/dependencies
        export PYTHONDONTWRITEBYTECODE=1 SOURCE_DATE_EPOCH=1
        mkdir -p "$HOME" project "$out/bin"
        cp -r ${source}/. project/
        chmod -R u+w project
        python ${configure} ${environment}
        ${buildCommand}
      '';
  native = callPackage ./build-native-products.nix { inherit purifier toolchain; } {
    inherit
      nativeProducts
      source
      environment
      nativeBuildInputs
      ;
    profile = lock.data.profiles.${stdenv.hostPlatform.system};
  };
  projections = if nativeProducts == { } then { } else native.projections;
  project = rawProject // {
    inherit projections;
  };
  release =
    runCommand "stack-project-release"
      {
        nativeBuildInputs = [
          gnutar
          gzip
        ];
      }
      ''
        mkdir source "$out"
        cp -r ${source}/. source/
        chmod -R u+w source
        cp ${lockFile} source/stack-purifier.lock.json
        cp ${stackLock} source/stack.yaml.lock
        tar --sort=name --mtime=@1 --owner=0 --group=0 --numeric-owner -C source -cf - . | gzip -n > "$out/project.tar.gz"
      '';
in
assert nativeProducts == { } || native.validated;
{
  projections = projections;
  packages = {
    inherit environment project;
    default = project;
  }
  // lib.optionalAttrs (nativeProducts != { }) { native = native.producer; };
  tests = project;
  inherit release;
  fetches = lock.entries;
}
