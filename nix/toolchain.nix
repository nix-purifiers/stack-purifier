# SPDX-License-Identifier: MIT
{
  buildEnv,
  nativeStack,
  haskellPackages,
  stdenv,
  gnumake,
  pkg-config,
  git,
  coreutils,
  zlib,
}:
buildEnv {
  name = "stack-purifier-toolchain";
  paths = [
    nativeStack
    haskellPackages.ghc
    stdenv.cc
    gnumake
    pkg-config
    git
    coreutils
    zlib
  ];
}
