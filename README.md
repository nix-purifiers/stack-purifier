# stack-purifier

Locked, Nix-resolved dependencies for Stack.

## Quickstart

From a project with `stack.yaml`:

```sh
nix shell 'github:nix-purifiers/stack-purifier'
stack-purifier lock
stack-purifier prefetch
stack build
stack test
stack repl
```

Commit `stack.yaml.lock` and `stack-purifier.lock.json`; ignore
`.stack-purifier/` and `.stack-work/`. The shell supplies Stack and GHC.
You can install the wrapper through your usual Nix configuration.

Without a companion lock, `stack` warns on standard error and runs ordinary,
impure Stack with its normal configuration. Invalid or stale locks fail
with a repair instruction.

## Lock and update

```sh
stack-purifier lock
stack-purifier update split
stack-purifier update
```

Native Stack resolves the graph, including test and benchmark dependencies,
package flags, exact Cabal revisions and Pantry tree fingerprints. Locking
does not compile dependencies. Nix fetches the pinned Hackage archives and
Cabal revisions, and Stack builds the dependency snapshot in the sandbox.
Normal development commands use that immutable snapshot with a private
native cache and a writable project build directory.

`lock` retains native selections. `update NAME` refreshes that explicit
Hackage dependency's revision; `update` also refreshes snapshot completions.
Version requirements and the chosen snapshot remain controlled by
`stack.yaml`. Change those declarations to select different versions.

Profiles are captured per platform. Run `lock` on each platform you use;
compatible profiles are retained in the companion. Editing application
sources does not rebuild dependencies. Changes to Cabal descriptions or
`stack.yaml` require relocking.

## External locks

```sh
stack-purifier \
  --project ./app \
  --lockfile ./locks/companion.json \
  --tool-lockfile ./locks/stack.yaml.lock \
  lock

export STACK_PURIFIER_LOCKFILE="$PWD/locks/companion.json"
export STACK_PURIFIER_TOOL_LOCKFILE="$PWD/locks/stack.yaml.lock"
cd app
stack build
```

The working tree's native lock remains untouched. `--native-lock` and
`STACK_PURIFIER_NATIVE_LOCKFILE` are equivalent native-lock selectors.

## Nix builds

The overlay exposes `pkgs.stack-purifier.buildStackProject`:

```nix
pkgs.stack-purifier.buildStackProject {
  src = ./.;
}
```

It returns `packages`, `tests`, `release` and `fetches`.
`packages.environment` builds the dependency snapshot; `packages.project`
builds/tests the project and installs its executables. `lockFile`,
`stackLock`, `nativeBuildInputs`, `fetchers`, `srcFilter` and `buildCommand`
customize the build. Output names are available during evaluation without
building dependencies.

### C ABI libraries for Python and Java

A Cabal `foreign-library` built by native Stack can be selected explicitly:

```nix
pkgs.stack-purifier.buildStackProject {
  src = ./.;
  nativeProducts.answer = {
    selector = "my-package:flib:answer";
    abi = "c";
    exports = [ "purifier_answer" "purifier_init" "purifier_term" ];
    runtime = {
      kind = "ghc";
      lifecycle = "process-once";
      threading = "single-foreign-thread";
      init = "purifier_init";
      term = "purifier_term";
    };
  };
}
```

This adds `packages.native` and `projections.native`, `projections.python-ffi`
and `projections.java-ffi`. Python's `purifier_native.load("answer")` loads the
selected library; Java's projection supplies `java.args` for the native library
path. CFFI can open the library recorded in the native projection catalog.
The native output includes the declared installed C headers and matching GHC
`HsFFI.h`. `native-products.json` records native component ownership, compiler,
installed library hashes and resolved Haskell runtime dependencies.

The library must provide its declared initialization and termination functions.
Initialize once, call from one foreign thread, then terminate once before the
process exits. Never reinitialize the GHC runtime, call after termination, or
mix these libraries with a different GHC runtime in one process. Keep the native
Nix closure available; these projections do not make a portable standalone DLL.

The initial contract supports one unversioned shared foreign library on native
Linux with `Simple` local Cabal packages. Declare its public headers with Cabal `install-includes`. Stack's own
configured Cabal component and installer determine its files and dependencies.
Ordinary Haskell libraries, executables, static archives and runtime plugins do
not become C ABI libraries automatically. Cross compilation is rejected.

Native Stack's dependency listing can omit dependencies used exclusively by a
foreign-library-only package. Keep non-SDK dependencies in an ordinary local
Haskell library used by the foreign component, so native Stack captures their
graph (as in `tests/fixtures/foreign-library`). The actual native local-package
query captures the foreign component's root and Cabal manifest. Capture each
platform profile with `stack-purifier lock`; CI captures and tests both Linux
architectures independently.

## Releases

```sh
stack-purifier publish \
  --repo OWNER/REPOSITORY \
  --tag v0.1.0
```

This creates or updates a GitHub release with a deterministic Nix-built
source archive containing both locks. It uses normal `gh` authentication.

## Scope

Supports Hackage dependencies, native snapshots selecting GHC 9.10.3, and
the supplied x86_64-linux/aarch64-linux SDK. Local project packages must
have explicit paths inside the working tree. Hpack descriptions are
supported; generated Cabal descriptions are included in the lock inputs.
Local snapshot files, Git/archive extra dependencies, custom dependency
configure options and alternate compilers are not yet supported.

Record dependency flags and compiler options in `stack.yaml`.
Dependency-changing command-line overrides are rejected in locked sessions.
Commands that initialize projects, update/install toolchains or unpack
unlocked dependencies are also guarded. Use the lock helper for updates
and source releases. Native build, test, benchmark, run, repl, exec and
inspection commands use the prepared project session.

## Credits

The purifier idea was inspired by nixcloud's
[cargo (libnix)](https://github.com/nixcloud/cargo).
