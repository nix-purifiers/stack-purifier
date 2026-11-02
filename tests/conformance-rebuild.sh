#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
for check in environment project release; do
  nix build ".#checks.$(nix eval --impure --raw --expr builtins.currentSystem).$check" --no-link
  nix build ".#checks.$(nix eval --impure --raw --expr builtins.currentSystem).$check" --rebuild --no-link
done
