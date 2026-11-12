{
  lib,
  stdenv,
  runCommand,
  writeText,
  python3,
  binutils,
  patchelf,
  purifier,
  toolchain,
}:
{
  nativeProducts,
  source,
  environment,
  profile,
  nativeBuildInputs ? [ ],
}:
let
  keys = builtins.attrNames nativeProducts;
  symbol = s: builtins.isString s && builtins.match "[A-Za-z_][A-Za-z0-9_]*" s != null;
  valid =
    p:
    builtins.isAttrs p
    &&
      builtins.attrNames p == [
        "abi"
        "exports"
        "runtime"
        "selector"
      ]
    && p.abi == "c"
    && builtins.isString p.selector
    && builtins.match "[A-Za-z0-9-]+:flib:[A-Za-z0-9-]+" p.selector != null
    && builtins.isList p.exports
    && p.exports != [ ]
    && lib.unique p.exports == p.exports
    && builtins.all symbol p.exports
    && builtins.isAttrs p.runtime
    &&
      builtins.attrNames p.runtime == [
        "init"
        "kind"
        "lifecycle"
        "term"
        "threading"
      ]
    && p.runtime.kind == "ghc"
    && p.runtime.lifecycle == "process-once"
    && p.runtime.threading == "single-foreign-thread"
    && symbol p.runtime.init
    && symbol p.runtime.term
    && p.runtime.init != p.runtime.term
    && builtins.elem p.runtime.init p.exports
    && builtins.elem p.runtime.term p.exports;
  selected =
    builtins.isAttrs nativeProducts
    && builtins.length keys == 1
    && builtins.match "[A-Za-z_][A-Za-z0-9_-]*" (builtins.head keys) != null
    && valid nativeProducts.${builtins.head keys};
  component = p: builtins.elemAt (lib.splitString ":" p.selector) 2;
  spec = writeText "stack-native-products.json" (
    builtins.toJSON {
      products = nativeProducts;
      system = stdenv.hostPlatform.system;
      inherit profile;
      environment = toString environment;
    }
  );
  python = python3.withPackages (p: [ p.pyyaml ]);
  producer =
    runCommand "stack-native-products"
      {
        nativeBuildInputs = [
          toolchain
          python
          binutils
          patchelf
        ]
        ++ nativeBuildInputs;
      }
      ''
        set -euo pipefail
        export HOME="$TMPDIR/home" STACK_ROOT="$TMPDIR/stack-root" STACK_PURIFIER_INSTALL_ROOT=${environment}/dependencies
        export PYTHONDONTWRITEBYTECODE=1 SOURCE_DATE_EPOCH=1 LANG=C.UTF-8
        mkdir -p "$HOME" "$out"
        cp -r --no-preserve=mode ${source} "$out/project"
        find "$out/project" -type f -exec touch -d @1 {} +
        python3 ${./prepare-native-products.py} "$out" ${environment} ${spec} ${../../stack_purifier}
        cd "$out/project"
        stack --no-nix --stack-yaml "$out/session/stack.yaml" build --test --bench --no-run-benchmarks
        python3 ${./install-native-products.py} "$out" ${spec} ${./inspect-native-products.hs} ${./native-setup.hs} ${lib.getBin stdenv.cc.libc}/bin/ldd
      '';
  projections = purifier.nativeProjections {
    inherit producer runCommand python3;
    system = stdenv.hostPlatform.system;
    libraries = lib.mapAttrs (_: p: "lib/lib${component p}.so") nativeProducts;
    includeDirectories = [
      "include"
      "include/ghc"
    ];
    targetMetadata = lib.mapAttrs (
      _: p:
      p
      // {
        type = "shared-library";
        nativeModel = "${producer}/native-products.json";
      }
    ) nativeProducts;
  };
in
assert lib.assertMsg selected
  "stack-purifier: nativeProducts requires one package:flib:component, explicit C exports and GHC process-once/single-foreign-thread init/term";
assert lib.assertMsg (
  stdenv.hostPlatform.isLinux && stdenv.buildPlatform == stdenv.hostPlatform
) "stack-purifier: nativeProducts currently requires native Linux";
{
  inherit producer projections;
  validated = true;
}
