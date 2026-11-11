# Updates: the signed feed and the component updater

This page is for developers who build, sign and publish releases. Users update with [INSTALL.md](../docs/INSTALL.md#updating).
It covers the two updated parts, the feed format, what a PC checks, and the release steps.

Everything that downloads software on a user's PC sits behind one explicit step: `strata update` in the Terminal, or
"Update engine" or "Update Strata" in the page's Settings. Starting a model and serving never download anything
(`STRATA_NO_ACQUIRE` stays on for them; the update step is a separate Control process that is allowed to). Engines are
separate from the Terminal and ship more often. An engine update downloads only the package for that PC's GPU.
**Models and saved tuning presets are never downloaded, replaced or deleted by any update.**

## The two things that are updated

| What | How | Signed with |
|---|---|---|
| Engine package (per platform / backend / variant) | Control's component updater: download with resume, verify, install beside the old one, test-start, switch | the feed key (Ed25519) |
| Control (Python runtime, `strata_control`, CLI, server) | the same updater: staged beside the running one, run once, then `components/control/active.json` points at it | the feed key |

One private key, kept by the owner, not in git, not the same as Apple code signing:

* **feed key**: Ed25519, signs `strata-feed.json` and every engine package manifest.

<sup>The desktop app and its Tauri updater are removed; the page rides inside the Terminal, which the Control entry
updates. Feeds published up to 0.2.12 still carry an `app` member; a current Terminal verifies such a feed and ignores
the member.</sup>

<a id="the-feed"></a>
## The feed (`strata-feed.json` + `strata-feed.json.sig`)

`strata-feed.json.sig` is `{"alg": "ed25519", "key_id": "<16 hex>", "value": "<base64>"}` over the **exact bytes** of
`strata-feed.json` (no canonicalisation). The annotated example below illustrates the format; it is not a current signed feed.
Comments are for this document only; actual feed files must be strict JSON.

```jsonc
{
  "schema_version": 1, "feed": "strata-updates", "channel": "stable",
  "sequence": 12,                           // goes up with every feed; an older one than the last accepted is refused
  "generated_at": "2026-10-20T10:00:00Z", "expires_at": "2027-10-20T10:00:00Z",   // expires_at may be null
  "control": {"version": "0.1.50", "packages": [{"platform": "linux-x86_64", "format": "tar.gz", "url": "control/...tar.gz",
                                                  "size": 0, "sha256": "...", "installed_bytes": 0, "entry": "strata.py"}]},
  "engine": {"packages": [{
    "version": "0.1.50", "platform": "linux-x86_64", "backend": "cuda", "variant": "cuda13",
    "archs": ["sm_75", "sm_86", "sm_89", "sm_120", "compute_120"], "min_driver": {"nvidia": "580.65"}, "min_runtime": null,
    "build_id": "be885026f96a-cuda13.0", "format": "tar.gz", "url": "engine/strata-engine-0.1.50-linux-x86_64-cuda-cuda13.tar.gz",
    "size": 505568970,                      // bytes of the archive
    "sha256": "...",                        // of the archive
    "manifest_sha256": "...",               // of the package's engine-component.json (it holds every file's hash)
    "installed_bytes": 840893419, "notes": ""}]},
  "dsv41": {"packages": []}                 // optional: the separate DeepSeek engine packages, same entry shape as "engine"
  "drafters": {"packages": [{               // optional: DFlash2 drafter files Strata hosts (one pinned file each, not an archive)
    "name": "qwen3.8-flash-next-dflash2.strata1.q4_0-selq4.pack", "version": "1", "url": "qwen3.8-flash-next-dflash2.strata1.q4_0-selq4.pack",
    "size": 339694592, "sha256": "4df2e5ab...", "backends": ["metal"], "base": "incoai/Qwen3.8-Flash-Next-DFlash2@a22a1744", "licence": "Apache-2.0"}]}
}
```

The DeepSeek V4.1-Flash engine `strata-dsv41` reaches a PC in one of two ways. The engine package carries it beside `strata`
(`package_engine.py --require-dsv41`), or it is a separate package in the feed's `dsv41` member, packed with
`pack-engine --prefix strata-dsv41`. The separate package installs in `<data>/engine-dsv41/` beside the engine, never inside it
(`strata_control/dsv41_engine.py`), and is fetched when DeepSeek is chosen. `STRATA_DSV41_EXE` points setup at a build of your own.

The `drafters` member lists the tuned DFlash2 drafter (`strata_control/drafting.py` TUNED_NAME: Strata's fine-tuned copy of incoai/Qwen3.8-Flash-Next-DFlash2, Apache-2.0,
modified by Strata). Control fetches it for the Mac sets that have a measured entry (`SET_DEFAULTS`: UD-Q4_K_XL and UD-IQ4_XS-v31) during setup, `strata launch` and
`strata drafting fetch`: it reads this signed feed, takes the entry named TUNED_NAME that lists `metal`, and downloads `url` (resumable) into `<data>/dflash2/`. The size and SHA-256
are pinned in the code too, so the feed can move the file but a feed that lists other bytes is ignored. No feed answer (offline, host down) or no such entry: nothing is said and the
model keeps the MTP. Make the entry with `feed_tool.py pack-drafter PACK --out RELEASE/drafters --version 1 --backends metal [--url-prefix ""]` (copies the file flat and writes
`<name>.entry.json`) and pass the folder to `build-feed --drafters RELEASE/drafters`; upload the pack beside the feed in the same release. Older Controls ignore the member.

The member holds two entries (one `pack-drafter` call each, both folders' `.entry.json` files in the same `--drafters` folder): `qwen3.8-flash-next-dflash2.strata1.q4_0-selq4.pack` (339,694,592 bytes) with
`--backends metal,cuda,hip`, and `qwen3.8-flash-next-dflash2.strata1.q4_0-bf16sel.pack` (522,458,112 bytes, sha256 34ca17fe...) with `--backends sycl` for the Intel Arc engine, which reads only bf16
selector codebooks. Control asks the feed for the file name its table entry (`SET_DEFAULTS`) names and for its own backend, so no platform fetches the other's file.

`url` is relative to the feed's own location (or an absolute `https://` address; a loopback `http://` is allowed so a local test
feed works; a local feed folder may only name files inside itself).
For a GitHub release the assets are flat: pack with `--url-prefix ""` and upload every file beside the feed.

### What a PC checks, in order

1. the signature of the feed (key ids it trusts: `strata_control/update_keys.json`, plus `STRATA_UPDATE_TRUSTED_KEYS` for a test setup, which
   the CLI announces);
2. `channel`, `expires_at`, and `sequence` not lower than the last accepted feed (the downgrade guard for a replayed old feed);
3. the engine package that fits **this** PC (`feed.select_engine`): platform, backend (CUDA / HIP / SYCL / Metal as detected),
   CUDA `archs` against the card (a `compute_NN` PTX entry covers newer cards), HIP `archs` against the card's gfx name,
   `min_driver` against the NVIDIA driver (this is how CUDA 13 beats CUDA 12 on a new driver and CUDA 12 is chosen on an old one),
   `min_runtime` against Control; newest version wins, and equal versions prefer the build that needs the newer driver;
4. never older than what is installed (an update never downgrades; only `strata components use` / `rollback` go back, to an engine that is already installed).

### What an engine update does

download (resume, progress) -> SHA-256 of the archive = the feed's -> unpack into `components/.s/<4 hex>/` (short on purpose: Windows paths are limited to 259 characters unless long paths are on; files are written through extended-length paths) (names and links checked,
total size limited to the feed's `installed_bytes`) -> the manifest is the one the feed names (`manifest_sha256`) and says what the feed says
-> the manifest's own signature verifies -> every file's hash -> fits this PC -> **test-start from the staging folder through the real start
path** (isolated owner runtime and profile, `component:strata-engine`, an installed model, one-token probe, stop) -> only then rename
into `components/engine/` and re-resolve to make sure a start now picks it. Any failure leaves `engine/` exactly as it was.
Every older engine is kept until the person deletes it (Settings > Engine, `strata components remove`); `state.json` `engine.previous` is the one the last
switch left. A new engine becomes the one in use and clears any pin. With no model installed yet (first run)
the test is `--version` only, and the report says so.

Not while an engine is serving: the check uses the activity lease `strata serve` and the owner runtime already hold. `--wait SECONDS` waits;
a terminal asks whether to wait; scripts get exit 7. The download itself may run first; the check is repeated right before the test start and
before the switch.

Saved tuning presets: when the engine identity changed (version, build id, manifest hash, backend) `tuning/recheck.json` gets a
`pending` entry (`engine_changed`, from/to identities). Presets are not opened. The tuner offers the ~2 minute check for them and calls
`preset_marks.clear(id)`; rolling back to the engine they were measured on clears the debt again.

### Commands

    strata update [--check] [--no-engine] [--no-control] [--feed URL|DIR] [--wait S] [--force] [--model-config RUN.json]
    strata components update            # the same
    strata components rollback          # back to the previous engine (an engine switch; nothing is downloaded)
    strata components engines [--output json]   # every installed engine (Qwen and DeepSeek families), sizes, which one is in use: what Settings > Engine shows
    strata components use <version|folder|newest>   # switch to an installed engine; `newest` goes back to following updates (no pin)
    strata components remove <version|folder>       # delete an installed engine; never the one in use or one a running program uses
    strata components engine            # shows "update available (...)" from the last check, no network

An update whose engine is already installed (the person switched to an older one) downloads nothing and makes it the engine in use again.
A version the person switched away from is never hidden from the update. The DeepSeek engine (`engine-dsv41/`) has its own list and its own choice
(the `.chosen` file there, read by the page, `strata launch` and setup); an update to a newer DeepSeek engine clears it. The two families never touch
each other's files or state (`strata_control/engine_library.py`).

Exit codes: 0 done or nothing to do, 5 refused (signature, hash, downgrade, no trusted key, acquiring forbidden),
6 failed with the old version still in use (network, test start), 7 an engine is serving, 130 stopped.
`--output json` / `--output jsonl` (progress events, last line is the result) are what the page's host uses.

## How the owner makes the real key (once)

On an offline machine, outside any git checkout:

    python packaging/feed_tool.py keygen --out D:\strata-keys          # feed-key.json (SECRET) + feed-key.pub.json

Back it up (a lost feed key means every installed PC needs a build with a new key). Then, in git, once, in the commit that
ships the first signed release (the fork's release key id is `9badd1d2b37076b5`):

* the `keys` entry from `feed-key.pub.json` into `strata_control/update_keys.json`.

Only release keys live in `update_keys.json`. A tester or local-test key is never added there: it is passed with `STRATA_UPDATE_TRUSTED_KEYS`
(a file of extra public keys; `packaging/make_local_feed.py` writes one as `test-trust.json`), so private tester builds keep working
without their key being part of a release build. A build whose `update_keys.json` is empty (a source checkout from before the keys) refuses every
feed with "this build trusts no update key".
A key rotation is a release that carries both public keys; the old private key signs that release, the new one signs the next.

## Releasing

The feed key lives only on the release PC. Every build box builds WITHOUT a key and makes the same archive bytes.
All signing happens on the release PC, on the finished files.

The release version is the upstream line plus our own number (`0.2.12.1` is our first release on upstream 0.2.12; tag `ds-0.2.12.1`,
[DIVERGENCE.md](../DIVERGENCE.md)). The tag and the GitHub release carry that number; the files and the signed feed carry the
component versions, because the updater compares those: the page reports its `apps/host/package.json` version and the Terminal its
`PRODUCT_VERSION` (both 0.2.12 today), and Control and the engines carry the CMake project version (0.1.60).

**What is built (per OS)**

* *The Terminal archive*, one per OS (`strata-terminal-<version>-<target>`, built by `packaging/package_terminal.py`). It carries
  the Terminal, the page (`host/`) and the Node that runs it (`node/`) — see [README.md](README.md#the-page-and-its-node-inside-the-terminal-archive) —
  and no engine: the right engine depends on the card, and engines come per card from the signed feed
  (`feed.select_engine`: platform, backend, CUDA `archs` and `min_driver`, HIP `archs`; `tests/terminal/test_release_matrix.py`).
  The same archive is the first download and what `install.sh` / `install.ps1` unpack; a person who prefers the terminal never
  opens a browser, and one who wants the page runs `deepstrata ui` after it.

**The release tool**

`packaging/release.py` is the release-side orchestrator. It builds what the release PC can build natively (the Linux parts),
imports what a Windows PC built, signs everything with the feed key, writes one signed feed, checks it the way a user's PC
would, and only then creates the GitHub release:

    python packaging/release.py plan --require-merged <branch>   # checks, tests, writes RELEASE/<version>/release.json
    python packaging/release.py build --windows-parts <folder>   # App Host, Terminal/Control, Qwen cuda13, dsv41 cuda13
    python packaging/release.py assemble                         # sign, pack, write the signed feed and installers
    python packaging/release.py verify                           # feed, hashes, selection and a real Terminal run
    python packaging/release.py publish                          # tag, GitHub release, upload, install.sh from latest

With no `--target`, `plan` releases both platforms (`linux-x86_64` and `windows-x86_64`); `--target linux-x86_64` (or
`--target windows-x86_64`) narrows it to one. The journal records the platforms and every later step follows them, so a
Linux-only release never asks for Windows parts. A journal written before Windows was a target has no list and stays
Linux-only.

**The Windows parts.** `packaging/build_windows.ps1` runs on a Windows x64 PC with the CUDA 13 toolkit, Visual Studio 2022
with the C++ workload, CMake, Ninja, Node 24.12+ and Python 3.10+. It writes an unsigned parts folder: the Terminal archive
(zip), the Qwen CUDA 13 engine, the `strata-dsv41` engine and `windows-parts.json` (commit, versions, toolchain, every
hash), and it runs the Terminal smoke test on that PC. `build --windows-parts <folder or .zip>` imports the folder here and
refuses parts built from another commit or a dirty tree, another component version, a CUDA 12 build, a missing or failed
smoke report, or a file whose hash changed after the manifest was written. Signing still happens only on the release PC,
with the feed key: a Windows build machine never sees it, and no GPU is needed to build or to run the smoke test (the mock
engine is used).

`.github/workflows/windows-parts.yml` runs that script on a self-hosted Windows runner that carries the `cuda13` label. It
is started by hand (`workflow_dispatch`), so no pull request depends on that machine. The parts folder is left on the runner
(`build-windows-parts`); copy it to the release PC, or set `upload_parts` to keep it as a workflow artifact (hundreds of MB),
then pass the folder to `build --windows-parts`.

Run it with the checkout's Python (the `.venv` that `setup.py`/`setup.sh` made, or another interpreter with
`requirements.txt`, `requirements-monitor.txt` and `pytest`): `plan` runs pytest in that same interpreter, and a bare
system Python without `textual`, `regex` or `numpy` stops at collection. The commands read the release journal in
`RELEASE/<version>/`; a rerun continues where the last command stopped.
`plan` stops on a dirty tree, a branch that is not `main`, a branch that is not merged, a tag that already exists, a key
that is not the one shipped in `strata_control/update_keys.json`, a feed sequence that would go down, failing tests
(`pytest tests/terminal tests/serve tests/tools` and `npm test` in `apps/host`), or missing build tools. It reads the
published feed and `origin/main` from GitHub unless `--offline --sequence N` is given (a local dry run). `--skip-tests`
skips the test gate; every skipped or failed gate is recorded in the journal.

`build` runs `npm ci`/`npm run build` when the App Host is not built, then `package_terminal.py`,
`packaging/build_cuda_linux.sh` and `packaging/build_dsv41_cuda.sh`, and verifies every engine package with
`package_engine.py --verify`. It reuses a build only for the commit in the journal; `--rebuild` throws the selected parts
away. `assemble` copies the engine packages before signing them (the build output stays unsigned), signs
`engine-component.json` with the feed key, packs the archives (tar.gz on Linux, zip on Windows), writes
`install-manifest.txt` and the signed `strata-feed.json` + `.sig`, and copies `install.sh` and `install.ps1` unchanged.
`verify` parses the signed feed with the shipped key, re-hashes every archive against the feed, checks the installed Control
manifest against `install-manifest.txt`, verifies every signed engine manifest, runs `packaging/smoke_terminal.py` on the
Linux Terminal archive, and asks `feed.select_engine` what a PC of each released platform gets. The Windows Terminal is not
run again here: its smoke report came with the parts, and its archive is re-hashed. A Windows-only release needs no CUDA
toolkit on the release PC at all: the engine comes in with the parts.

`publish` creates the annotated tag, pushes it over HTTPS with the token (asked from the environment, never printed),
creates a normal latest release with `notes.md`, uploads every file in `RELEASE/<version>/feed/` flat, and then installs
the published release with `install.sh` from `latest/download` and starts it once (that last check is skipped when the
release has no Linux Terminal). `--resume` continues an interrupted upload; `--replace` replaces an asset whose content
differs; neither deletes a release.

The private key is read from `--key`, `STRATA_FEED_KEY` or `~/deepstrata-keys/feed-key.json`, and must be outside this git
checkout with mode 0600. The GitHub token is read from `GH_TOKEN`/`GITHUB_TOKEN` or the `gh` config's
`github.com oauth_token`. Nothing under `RELEASE/<version>/` is committed (it is in `.gitignore`).

`status` prints the journal and the next step; `next` prints the next version, tag and component version without changing
anything. A release must be built from a clean `main` commit; the journal names that commit and every later step refuses a
different checkout, so a half-built release cannot be signed from another tree.

**What the automated release carries today.** `linux-x86_64` and `windows-x86_64`: for each, the Terminal archive (and the
same bundle packed as the Control package that `install.sh` / `install.ps1` and the updater use), the Qwen CUDA 13 engine and
the DeepSeek `strata-dsv41` CUDA 13 engine, all at the CMake version (0.1.60 today). The Windows binaries are not
Authenticode-signed: SmartScreen may ask once before the Terminal runs, and the generated release notes say so. macOS, HIP and
`linux-aarch64` builds are made on their own machines and still need the steps below; a release that must serve those
platforms is not complete until their Control and engine packages are on the same release page (one Control package per
platform; the feed lists them all). The release version (`0.2.12.2`) is the tag and the GitHub release; the files and the
feed carry the component versions, because the updater compares those.

**Steps for the platforms the release tool does not build (macOS, HIP, linux-aarch64)**

    python packaging/feed_tool.py sign-package <engine package folder> --key <feed-key.json>        # every engine package
    python packaging/feed_tool.py pack-engine  <engine package folder> --out RELEASE/engine --url-prefix=
    python packaging/feed_tool.py pack-control <control folder> --version 0.1.60 --platform P --out RELEASE/control --url-prefix=   # --format zip for windows-x86_64
    # --url-prefix= (not --url-prefix "") because PowerShell 5.1 drops an empty argument. The Control version is the VERSION file
    # (0.1.60), not the page version: a Control package newer than the installed one is installed by `strata update`.
    # Pack Linux and Mac packages on Linux / the Mac or on Windows with this tool: it sets the execute bit from the file header either way.
    python packaging/feed_tool.py build-feed --out RELEASE --sequence N --key <feed-key.json> --engine RELEASE/engine --control RELEASE/control [--dsv41 RELEASE/dsv41] [--drafters RELEASE/drafters]
    python packaging/feed_tool.py verify-feed RELEASE --pub <feed-key.pub.json>

### The Mac archive

`packaging/mac/sign-and-notarize.sh` still signs and notarises Mac artifacts: `--terminal <archive>` for the Terminal
archive, `--engine` for an engine package, and its `.app`/dmg paths for whatever desktop artifact an old release still
points at. **Until the fork holds its own Apple certificate** its Mac builds go out without a Developer ID and without
notarisation, with `--dry-run` running the checks with an ad-hoc signature and touching no Apple account; a downloaded
archive may warn on the first open and [docs/TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md) teaches the bypass. With a
certificate of our own, run the real procedure and the warning is gone.

`build-feed` also writes `RELEASE/install-manifest.txt`, the file the one-line installers read: one line per Control package,
`platform version url sha256 size`, taken from the `*.entry.json` files in `RELEASE/control`. The Control package is the Terminal that `install.sh` unpacks (it must hold `bin/deepstrata`).
`install.sh` unpacks with `tar -xzf` and `install.ps1` with `Expand-Archive`, so `windows-x86_64` must be packed with `--format zip` and the other platforms as `tar.gz`;
`build-feed` leaves a pack in the wrong format out of the manifest and says so. Pack Control for every platform you want the one-line install to cover (`install.sh` knows `linux-x86_64`, `linux-aarch64` and `macos-arm64`, `install.ps1` `windows-x86_64`);
a platform with no line gets "this release has no Terminal for <platform>". Upload `install.sh` and `install.ps1` from `packaging/` unchanged: their built-in address is the release folder.

**Linux ARM64 (DGX Spark) has its own feed file.** A Control up to 0.1.52 refuses a feed that names a platform it does not know (`the feed's control entry names an unknown platform`), so the `linux-aarch64` entries are not in `strata-feed.json`, which every released client reads. They are in `strata-feed-linux-aarch64.json` (+ `.sig`, same format, same key, its own `sequence`, aarch64 entries only), uploaded to the same release. A Linux ARM64 Terminal reads that file by default, from a folder or a release address (`strata_control.feed.feed_name()`); a source that names its own `.json` file is used as given. Until every client skips unknown platforms, each release must publish both files, and the aarch64 file's sequence must never go down.

The feed is hosted as the files of a GitHub release of `ilfarpro/DeepStrata`. The built-in address is
`https://github.com/ilfarpro/DeepStrata/releases/latest/download` (`strata_cli/update_cmd.py` `DEFAULT_FEED` adds `/strata-feed.json`; `install.sh`
and `install.ps1` use the folder). GitHub assets are flat, so build the packages with `--url-prefix ""` and upload every file of the release folder
as an asset: `strata-feed.json`, `strata-feed.json.sig`, `install-manifest.txt`, `install.sh`, `install.ps1`, the engine and Control archives
(the Control package is the Terminal bundle under its Control name; there is no second Terminal upload). `STRATA_FEED` (installers) and `--feed` / `STRATA_UPDATE_FEED` (`strata update`) override the
address. `feed_tool.py` only writes files; `packaging/release.py publish` is the one step that uploads, with the owner's
GitHub token and only after `verify` passed.

Publish as a normal release (latest): a pre-release is not found by `latest/download`, so the installers and `strata update` would get a 404.
The `latest` release must carry every file listed above, so a later hotfix release repeats them all.

## Testing locally

Everything above runs against a folder or a loopback server: `tests/terminal/test_update_feed.py` (signatures, hashes, downgrades, selection),
`tests/terminal/test_updater.py` (end to end against a loopback HTTP feed with stub engines: update, test-start, switch; a failing test start
keeps the old engine; an interrupted download resumes; rollback; Control), `apps/host/tests/component-updates.test.mjs`
(the Settings buttons). For a real engine on a box, build a package with `packaging/package_engine.py`, serve it from a local feed made by `packaging/make_local_feed.py`, and run `strata update --feed` against it.

## History / older measurements

- **Keys:** the feed key and the Tauri updater key were created once and the feed key's public half was committed in `strata_control/update_keys.json`
  with the first signed release (0.2.0-beta). Terminals from before that build trust no key and refuse every feed.
- **Engine-less installers:** an app update once carried an engine. The Windows and Linux installers dropped `engine-bundle`,
  so one `latest.json` entry per OS served every card variant.
- **The desktop app and its updater:** up to 0.2.12 the feed carried an `app` member (Tauri's static updater JSON) and the shell
  updated itself through a token-protected loopback bridge with the Tauri updater key. The member is gone from the feed format,
  `feed_tool.py` no longer builds or edits it, and `update_keys.json` no longer trusts `app_minisign_public`; a current Terminal
  still verifies and parses an old signed feed and ignores its `app` member. The old `latest.json` and installers stay on their
  release pages for old installs to keep working.
