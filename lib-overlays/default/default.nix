# SPDX-License-Identifier: MIT
{ lib }:
{
  readLock = import ./read-lock.nix { inherit lib; };
}
