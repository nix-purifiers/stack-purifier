{
  pkgs,
  src ? ./fixtures/foreign-library,
  competitorSrc ? ./fixtures/foreign-library99,
}:
let
  args = {
    inherit src;
    nativeProducts.answer = {
      abi = "c";
      selector = "stack-foreign-proof:flib:answer";
      exports = [
        "purifier_answer"
        "purifier_init"
        "purifier_term"
      ];
      runtime = {
        kind = "ghc";
        lifecycle = "process-once";
        threading = "single-foreign-thread";
        init = "purifier_init";
        term = "purifier_term";
      };
    };
  };
  built = pkgs.stack-purifier.buildStackProject args;
  competitor = pkgs.stack-purifier.buildStackProject (args // { src = competitorSrc; });
  product = args.nativeProducts.answer;
  invalid = [
    (product // { abi = "haskell"; })
    (product // { selector = "stack-foreign-proof:exe:answer"; })
    (product // { exports = [ ]; })
    (product // { exports = product.exports ++ [ "purifier_answer" ]; })
    (
      product
      // {
        runtime = product.runtime // {
          lifecycle = "automatic";
        };
      }
    )
    (
      product
      // {
        runtime = product.runtime // {
          threading = "concurrent";
        };
      }
    )
    (
      product
      // {
        runtime = product.runtime // {
          init = "purifier_term";
        };
      }
    )
    (
      product
      // {
        runtime = product.runtime // {
          term = "absent";
        };
      }
    )
  ];
  rejected =
    p:
    !(builtins.tryEval
      (pkgs.stack-purifier.buildStackProject (args // { nativeProducts.answer = p; }))
      .projections.native.drvPath
    ).success;
  default = pkgs.stack-purifier.buildStackProject (removeAttrs args [ "nativeProducts" ]);
  crossPkgs =
    if pkgs.stdenv.buildPlatform.isAarch64 then
      pkgs.pkgsCross.gnu64
    else
      pkgs.pkgsCross.aarch64-multiplatform;
  crossBuild = pkgs.stack-purifier.buildStackProject.override {
    stdenv = crossPkgs.stdenv;
    callPackage = crossPkgs.callPackage;
  };
in
assert builtins.all rejected invalid;
assert default.projections == { };
assert !(builtins.tryEval (crossBuild args).projections.native.drvPath).success;
pkgs.runCommand "stack-native-products-consumers"
  {
    nativeBuildInputs = [
      pkgs.gcc
      pkgs.jdk25
      pkgs.patchelf
      (pkgs.python3.withPackages (p: [ p.cffi ]))
    ];
  }
  ''
    set -euo pipefail
    mkdir -p "$out"
    export PYTHONPATH=${built.projections.python-ffi}
    prefix=$(python3 -c 'import json; print(json.load(open("${built.projections.native}/catalog.json"))["prefix"])')
    competitor=$(python3 -c 'import json; print(json.load(open("${competitor.projections.native}/catalog.json"))["prefix"])')
    library="$prefix/lib/libanswer.so"
    gcc -I "$prefix/include" -I "$prefix/include/ghc" ${./native/consumer.c} -ldl -o consumer
    timeout 25s ./consumer "$library" > "$out/c.txt"
    timeout 25s python3 ${./native/ctypes-consumer.py} "$library" > "$out/ctypes.json"
    timeout 25s python3 ${./native/cffi-consumer.py} "$library" > "$out/cffi.json"
    cp ${./native/Consumer.java} Consumer.java
    javac Consumer.java
    timeout 25s java @${built.projections.java-ffi}/java.args --enable-native-access=ALL-UNNAMED Consumer "$library" > "$out/java.txt"
    python3 ${./native/runtime-controls.py} "$prefix" "$competitor" "$out"
    cp "$prefix/native-products.json" "$out/native-products.json"
  ''
