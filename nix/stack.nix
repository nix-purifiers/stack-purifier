# SPDX-License-Identifier: MIT
{ haskellPackages }:
haskellPackages.stack.overrideAttrs (old: {
  prePatch =
    (old.prePatch or "")
    + "\nsed -i 's/\\r$//' src/Stack/Types/EnvConfig.hs src/Stack/DependencyGraph.hs\n";
  patches = (old.patches or [ ]) ++ [ ./stack.patch ];
})
