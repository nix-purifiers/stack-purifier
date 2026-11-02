# SPDX-License-Identifier: MIT
{
  lib,
  stdenvNoCC,
  stdenv,
  makeWrapper,
  python3,
  nix,
  gh,
  nativeStack,
  toolchain,
  cacert,
  nixpkgsSrc,
  purifierSrc,
}:
let
  python = python3.withPackages (p: [ p.pyyaml ]);
in
stdenvNoCC.mkDerivation {
  pname = "stack-purifier";
  version = "0.1.0";
  src = ../.;
  nativeBuildInputs = [ makeWrapper ];
  dontBuild = true;
  installPhase = ''
    mkdir -p "$out/bin" "$out/lib/stack-purifier"
    cp -r stack_purifier nix "$out/lib/stack-purifier/"
    install -m755 bin/stack-purifier "$out/bin/stack-purifier"
    substituteInPlace "$out/lib/stack-purifier/nix/realize.nix" \
      --replace-fail '@nixpkgs@' '${nixpkgsSrc}' \
      --replace-fail '@purifier@' '${purifierSrc}'
    wrapProgram "$out/bin/stack-purifier" \
      --prefix PATH : ${
        lib.makeBinPath [
          python
          nix
          gh
          toolchain
        ]
      } \
      --set-default SSL_CERT_FILE ${cacert}/etc/ssl/certs/ca-bundle.crt \
      --set STACK_PURIFIER_NATIVE ${nativeStack}/bin/stack \
      --set STACK_PURIFIER_SYSTEM ${stdenv.hostPlatform.system} \
      --set STACK_PURIFIER_SDK ${toolchain}
    makeWrapper "$out/bin/stack-purifier" "$out/bin/stack" --add-flags '--native --'
    for program in ${toolchain}/bin/*; do
      name=$(basename "$program")
      if [ ! -e "$out/bin/$name" ]; then ln -s "$program" "$out/bin/$name"; fi
    done
  '';
  meta = {
    description = "Native Stack workflow with locked Nix-resolved dependencies";
    homepage = "https://nix-purifiers.github.io/";
    license = lib.licenses.mit;
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
    ];
    mainProgram = "stack-purifier";
  };
}
