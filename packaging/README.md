# Strata packaging: targets, recipes and locks

This page is for developers who build the Terminal archive and the engine packages. The chat page ships inside the
Terminal archive: same download, `deepstrata ui` opens it. To install Strata, use [docs/INSTALL.md](../docs/INSTALL.md).
It describes the current release (Terminal 0.2.5, Control and engines 0.1.50).
How packages are signed and published is in [UPDATES.md](UPDATES.md), and the package format is in [ENGINE-COMPONENT.md](ENGINE-COMPONENT.md).

Release assets show what was published; the platform guides and retained reports show what was tested. A published package does not
qualify every GPU, model or feature on its platform.

## Quick start

| I want to | See |
| --- | --- |
| Build the Terminal archive (the page rides along) | [The Terminal archive](#the-terminal-archive) |
| Build an engine package | [ENGINE-COMPONENT.md](ENGINE-COMPONENT.md#building-a-package) |
| Make a release: plan, build, sign, verify, publish | [UPDATES.md](UPDATES.md#releasing) (`python packaging/release.py --help`) |
| Sign and publish a release by hand | [UPDATES.md](UPDATES.md#releasing) |
| Test an update against a local feed | [UPDATES.md](UPDATES.md#testing-locally) |

## The target matrix

Each row is an OS, CPU ABI and backend. A row is `qualified` only after the acceptance checklist
([below](#what-a-platform-must-produce-before-it-is-qualified)) has run on it.

| Target | Terminal artifact | Engine package recipe | Status |
| --- | --- | --- | --- |
| `macos-arm64-metal` | `.tar.gz` (next row) | Metal `strata`, bundled with `strata-vision` (`packaging/build_vision_mac.sh`) | Engine built; the old DMG self-test passed from the mounted image. Inference measurements are in [MAC.md](../docs/MAC.md). The fork's builds go out unsigned until the fork holds its own Developer ID ([mac/README.md](mac/README.md)). |
| `darwin-arm64` Terminal | self-contained `.tar.gz` (page and Node inside) | any | Built and verified. Runs from a moved directory with no system Python; `deepstrata ui` opened the page from the extracted archive. |
| `windows-x86_64-cuda` | `.zip` (page and Node inside) | `cuda13` and `cuda12` | Engines built and tested on a Windows PC with an NVIDIA card: a real model, an agent chat. |
| `windows-x86_64-hip` | `.zip` | `tools/hip/build_windows.bat` | Published. Strix Halo inference is recorded in [STRIX_HALO.md](../docs/STRIX_HALO.md). This does not qualify every discrete Radeon card ([AMD_HIP.md](../docs/AMD_HIP.md)). |
| `linux-x86_64-cuda` | `.tar.gz` | `packaging/build_cuda_linux.sh`, built in a glibc 2.35 chroot | `cuda13` built and tested on RTX 3060 and RTX A4000 (a real model, a picture, the page in the browser). `cuda12` built. |
| `linux-x86_64-hip` | `.tar.gz` | `packaging/build_hip_linux.sh` | `rx9070` (gfx1200 / gfx1201) built and tested on a Radeon AI PRO R9700. `rx7900` (gfx1100 / 1101 / 1102) built, not run on an RX 7000 card. `rx6000` (gfx1030 / 1031) built, not tested on a real RX 6000 card. |
| `linux-aarch64-cuda` | `.tar.gz` | `packaging/build_cuda_linux_aarch64.sh`, built on the DGX Spark (`cuda13`, `sm_121`) | Engine packages (Qwen with `strata-dsv41` inside, and the separate DeepSeek one) and the Terminal archive built and tested on one DGX Spark ([DGX_SPARK_LINUX.md](../docs/DGX_SPARK_LINUX.md)). Not tried on any other arm64 machine. |
| `linux-x86_64-sycl` | `.tar.gz` | `packaging/build_sycl_linux.sh` (one JIT package for Alchemist and Battlemage) | Engine package built and tested on an Arc A750. Battlemage is supported and untested by us. |
| `windows-x86_64-sycl` | none | none | Gated. No native Windows SYCL path exists upstream. |
| `macos-x86_64-*` | none | none | Unsupported. The macOS engine is Apple Silicon only. |

**DeepSeek V4.1-Flash.** The signed feed's `dsv41` member carries the DeepSeek engine `strata-dsv41` as its own package: Windows
NVIDIA `cuda13`, Linux NVIDIA `cuda13`, Linux AMD `rx9070`, and since 0.2.7 Windows AMD `rdna3` (Ryzen AI Max, gfx1151). All other
engine packages carry no DeepSeek engine. Build `strata-dsv41` with `packaging/build_dsv41_cuda.sh` (Linux) or
`packaging/build_dsv41_windows.bat` (Windows), then package it with `package_engine.py --require-dsv41`; or pack it as the separate
download (`feed_tool.py pack-engine --prefix strata-dsv41`, [UPDATES.md](UPDATES.md#the-feed)).

Windows AMD (gfx1151): build `strata-dsv41` with `tools\dsv41\win\build_hip_win.bat` (the ROCm venv, default `gfx1151`) and stage
it with the ROCm runtime the Qwen Windows `rdna3` package already carries: the HIP and C++ runtime DLLs beside the program,
`rocm/bin` (hipBLAS / rocBLAS / hipBLASLt and their imports), `rocm/bin/rocblas/library/gfx1151`,
`rocm/bin/hipblaslt/library/gfx1151`, `rocm/.kpack/blas_lib_gfx1151.kpack`, `rocm/licenses`, and a `BUILD.json` that names
`"lib_dirs": ["rocm/bin"]`. Package it with `package_engine.py --backend hip --platform windows-x86_64 --arch gfx1151 --entrypoint
strata-dsv41.exe --variant rdna3` (about 240 MiB, 280 files). Strata puts the package's `lib_dirs` on the engine's library path
(`strata_control/dsv41_engine.py lib_dirs`), so the HIP runtime is found the same way the Qwen engine finds it.

Cross-compilation is not attempted. Every artifact is built on its own OS; the pinned Python and Node are downloaded per
target at build time and checked against their digests. The Python contracts, the owner transport, the checks and the engine
build recipes are portable.

## The page and its Node, inside the Terminal archive

`package_terminal.py` stages two folders that make the browser page work with no system runtime (see
[The Terminal archive](#the-terminal-archive)):

- `host/` — the App Host: `server/`, the built `dist/` and `package.json` from `apps/host`. Build it first
  (`npm ci && npm run build` in `apps/host`); a real build without `dist/` stops with that command. The host imports only
  its own files and Node's builtins, so `host/` carries no `node_modules`.
- `node/` — one pinned standalone Node 24.12.0 binary (`node/bin/node`, `node/node.exe` on Windows), fetched from
  nodejs.org at build time and checked against the SHA-256 recorded in `NODE_PINS` (the digest of the official
  `SHASUMS256.txt`). To bump the version, change `NODE_VERSION`, add the new digests, and `smoke_terminal.py` runs
  the bundled binary to prove it is new enough. `deepstrata ui` prefers this binary, then `STRATA_NODE`, then the PATH.

## Strata Control and the engine

The Terminal archive fetches nothing at run time; the engine is a separate, explicit download.

**Control** is the same bundle as the Terminal archive: the pinned CPython 3.12.15, the hash-locked wheels,
`strata_control`, `strata_cli` and `serve`, plus a `BUNDLE.json` with the interpreter and lock digests. A page host finds a
Control at `control/` beside its own folder, through `STRATA_CONTROL_DIR`, or as the Control component that
`deepstrata update` installed, and runs it on its own interpreter (`PYTHONNOUSERSITE=1`, no `PYTHONHOME` or
`PYTHONPATH`). It starts a model with `strata profile create|use-config` and `strata runtime start --die-with-pid <host>`,
so the engine ends with the host and the host ends with `deepstrata ui --stop`. The engine then runs under the owner
runtime (loopback TCP and a 256-bit token on Windows, a private socket elsewhere; a Job Object or process group; leases),
and a second client such as the Terminal can attach to the same instance. One host serves one page and can drive several
engines (profiles `app-1`, `app-2`, ...). Every close path is in [docs/CLOSING.md](../docs/CLOSING.md).

**The engine.** The Terminal archive carries no engine. `deepstrata launch` shows the engine for the card with its size
before it downloads, with progress and resume, and can install an engine file the person already has. The engine ends up in
the per-user component folder, after the package is checked against its manifest
(`%LOCALAPPDATA%\Strata\components`, `$XDG_DATA_HOME/strata/components`, or `STRATA_COMPONENTS_DIR`),
where Control finds it like any engine that `strata update` installed:

    components/engine/<version>-<platform>-<backend>[-<variant>]/
        engine-component.json     the manifest (see ENGINE-COMPONENT.md)
        strata[.exe]  *.dll | *.so   the binary and the GPU runtime libraries beside it

A run config names it as `"exe": "component:strata-engine"`. At start, `strata_control/engine_component.py` picks the package for this PC
(platform, then the backend its GPU needs, found with nvidia-smi, rocminfo or sycl-ls, or `STRATA_ENGINE_BACKEND`), checks every
file's size and SHA-256 and puts the package folder first on the library path. A mismatch is `EngineIntegrityError` and nothing starts.

When no package matches, `strata serve` ends with exit 5 and "No engine for this PC was found". It never fetches. Before that point
everything is a warning: `strata launch` says it could not find an engine, asks whether to go on, downloads the model and ends with
the next step (exit 0, nothing started). The page shows the same warning with an "Update engine" button in Settings.
`packaging/package_engine.py` makes a local package from a build folder.

**No acquisition at run time.** The host starts Control with `STRATA_NO_ACQUIRE=1`, which makes
`strata_control/acquisition.py` refuse pip, downloads and compiles. On the host side `server/acquisition.mjs` refuses the llama.cpp
and cloudflared fetches (a connector placed in `components/cloudflared/` is used as it is). Models the user chooses to download are a
separate, explicit action. `apps/host/tests/no-acquire.test.mjs` fails on any outbound request or installer process.

## The Terminal archive

The Terminal bundles a private Python and its dependencies for `windows-x86_64`, `linux-x86_64`, `linux-aarch64` (DGX Spark) and `darwin-arm64`
(`package_terminal.py --target ...`, built on the target OS).

- The interpreter is python-build-standalone CPython 3.12.15, digest-checked at build time.
- Packages come from `packaging/locks/runtime-<target>-cp312.txt` and are installed with
  `pip install --require-hashes --no-deps --only-binary=:all:` at build time only. `lock_wheels.py` makes the lock; review its diff when a pin changes.
- The page and its Node ride along: `host/` and `node/`, [above](#the-page-and-its-node-inside-the-terminal-archive).
- Wrappers: `bin/deepstrata.cmd` and `bin/deepstrata.ps1` on Windows, `bin/deepstrata` on POSIX. `START-HERE.bat`, `SETUP.bat`, `UPDATE.bat`, `setup.sh`
  and `update.sh` use a `python/` folder beside them when there is one.
- `smoke_terminal.py <archive>` extracts an archive into a clean environment (a path with a space, a PATH with no Python) and runs
  `--version`, `doctor`, `profile list`, `install` and `serve --engine mock`; it also runs the bundled Node and checks the staged page.
- Check each archive's `BUNDLE.json` and lock digests. An old archive is not upgraded when the source lock changes.

## What a platform must produce before it is qualified

1. The host serves the page from its own origin, and a browser tab works end to end (`deepstrata ui` from an extracted archive).
2. The owner transport works: one owner per installation, a second client attaches, a stale owner can be recovered, and no client can stop a process it does not own.
3. Real inference on a supported pack, with the engine build hash and the model hashes recorded.
4. `docs/work/QUALIFICATION-KIT.md` run end to end, with raw evidence kept.

## Release checks and gaps

- CI covers scripts, Terminal checks, Metal builds and Docker images ([.github/workflows](../.github/workflows)). It does not replace native
  packaging, signing or real-GPU acceptance on every target. Some recipes still run by hand.
- Signing needs the owner's credentials, which are never in this tree. The feed and every engine package are signed with the feed key
  (`feed_tool.py`). The Mac Terminal archive is signed and notarised on a Mac that holds the Developer ID certificate. No Windows code-signing
  certificate is configured, so Windows may warn about the archive.
- Check the licenses and provenance of bundled fonts and assets when packaging. The early D05 checklist lacked redistribution evidence;
  treat that as an open checklist finding, not an audit of every release asset.

## History / older measurements

- **The desktop app:** up to 0.2.12 the product was a Tauri desktop shell (`apps/desktop`) with per-system installers and its own
  updater key. It is removed; the page it wrapped ships in the Terminal archive and opens with `deepstrata ui`. The old installers
  stay on their release pages.
- **Tauri config:** an early draft used `perMachine`, `oneClick` and `deleteAppDataOnUninstall`. The Tauri schema rejects them; the
  current config uses `installMode: currentUser`.
- **Repository size:** `apps/desktop/src-tauri/target` and the Node sidecar were briefly committed, which left about 324 MB in the git object
  store. They are untracked and ignored now. History was not rewritten.
- **Mac packaging tests:** the first DMG self-test (8/8) and the engine's 17 kernel-parity suites ran on an Apple M4 GPU in a 16 GiB Mac, with service stand-ins for signing.
  Later inference measurements on larger Macs are in [MAC.md](../docs/MAC.md).
- **Intel Arc:** official since 0.1.40.2. The 2026-10 B70 tests used the source build, not the engine package. `sycl/setup_intel.py` (Docker) remains the source-build route.
