# SPDX-License-Identifier: MIT
{
  description = "Locked, Nix-resolved dependencies for Stack";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.purifier = {
    url = "github:nix-purifiers/purifier";
    flake = false;
  };
  outputs =
    {
      self,
      nixpkgs,
      purifier,
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAll = nixpkgs.lib.genAttrs systems;
      overlay = final: prev: {
        stack-purifier = final.lib.makeScope final.newScope (scope: {
          nativeStack = scope.callPackage ./nix/stack.nix { };
          toolchain = scope.callPackage ./nix/toolchain.nix { };
          cli = scope.callPackage ./pkgs/stack-purifier/default.nix {
            nixpkgsSrc = nixpkgs;
            purifierSrc = purifier;
          };
          buildStackProject = scope.callPackage ./pkgs/build-stack-project/default.nix {
            purifier = import (purifier + "/nix/lib") { inherit (final) lib; };
          };
        });
      };
      pkgsFor =
        system:
        import nixpkgs {
          inherit system;
          overlays = [ overlay ];
        };
    in
    {
      overlays.default = overlay;
      packages = forAll (system: {
        native = (pkgsFor system).stack-purifier.nativeStack;
        default = (pkgsFor system).stack-purifier.cli;
        cli = self.packages.${system}.default;
      });
      lib.stack-purifier = import ./lib-overlays/default { lib = nixpkgs.lib; };
      apps = forAll (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/stack-purifier";
          meta.description = "Lock and prefetch Stack dependencies";
        };
      });
      checks = forAll (
        system:
        import ./tests {
          pkgs = pkgsFor system;
          inherit self;
        }
      );
      formatter = forAll (system: (pkgsFor system).nixfmt-tree);
    };
}
