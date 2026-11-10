# SPDX-License-Identifier: MIT
{ pkgs, self }:
let
  built = pkgs.stack-purifier.buildStackProject { src = ../examples/minimal; };
in
{
  cli = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
  environment = built.packages.environment;
  project = built.tests;
  release = built.release;
}
//
  pkgs.lib.optionalAttrs
    (builtins.hasAttr pkgs.stdenv.hostPlatform.system (builtins.fromJSON (
      builtins.readFile ./fixtures/foreign-library/stack-purifier.lock.json
    )).profiles)
    {
      native-products = import ./native-products.nix { inherit pkgs; };
    }
