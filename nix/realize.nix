# SPDX-License-Identifier: MIT
{
  project,
  nativeLock,
  companion,
  target,
}:
let
  pkgs = import (/. + "@nixpkgs@") { };
  purifier = import (/. + "@purifier@/nix/lib") { inherit (pkgs) lib; };
  nativeStack = pkgs.callPackage ./stack.nix { };
  toolchain = pkgs.callPackage ./toolchain.nix { inherit nativeStack; };
  args = {
    src = /. + project;
    lockFile = /. + companion;
    stackLock = /. + nativeLock;
  };
  lock = import ./read-lock.nix { inherit (pkgs) lib; } args;
in
if target == "environment" then
  pkgs.callPackage ./environment.nix { inherit purifier nativeStack toolchain; } { inherit lock; }
else if target == "release" then
  (pkgs.callPackage ./build-project.nix { inherit purifier nativeStack toolchain; } args).release
else
  throw "stack-purifier: unknown target"
