# DIVERGENCE — how DeepStrata differs from Strata, and how we stay current

DeepStrata is a fork of [Niko1221/Strata](https://github.com/Niko1221/Strata).
This file is the map of everything we changed on top of the original, and the
rules for merging the original in without losing our work. Read it before a
sync merge, and update it in the same merge that changes a divergence.

The fork works in fork mode: no pull requests or issues to the original
repository. Our plan and our engine specs live here, in `roadmap/`.

## Remotes and sync mechanics

| Remote | Repository | Use |
|---|---|---|
| `strata` | `https://github.com/Niko1221/Strata` | fetch only — the original |
| `deepstrata` | `git@github.com:ilfarpro/DeepStrata.git` | our public repository, fetch + push |

A sync, roughly once per upstream release:

```sh
git fetch strata
git switch main
git merge strata/main          # one merge commit per sync
# resolve conflicts against the list below, run the tests, then:
git push deepstrata main
git tag sync-<version>         # e.g. sync-0.2.10, where upstream we stand
```

Rules that keep the merges mechanical:

- Work happens in feature branches, merged into `main`. `main` never takes a
  direct commit. Then every conflict is "our features vs their upstream",
  never "whatever vs everything".
- Merge, never rebase: our commits stay as they were tested.
- Our engine changes are byte-isolated: they live in branches the upstream
  code does not touch, so an upstream line stays byte-identical unless we
  chose to change exactly that line.
- After every sync: update the "Last synced" line below, add or fix an entry
  if the merge changed a divergence zone, and run the tests named there.

**Last synced:** `strata/main` at `146df3c4` (upstream 0.2.12 line), 2026-11-08.
Our `main` ahead by 38 commits.

## Divergence zones (what a sync must protect)

### 1. The served web page is gone; `strata ui` brings the App Host's page

The biggest structural difference. Commit `remove-serve-web-terminal-ui`:

- `serve/web/` (the page, its locales, its vendored KaTeX), `serve/webfiles.py`,
  `sycl/serve/web/` (the Intel switcher), the page's tests (`serve/test_web_*`),
  `tools/webtest/` and `tools/web_i18n.py` are **deleted**.
- The address serves a plain API index; every JSON endpoint keeps working, and
  `serve` stays runnable without the Terminal packages (as
  `.github/workflows/terminal-minimal-server.yml` runs it).
- `strata ui` (new: `strata_cli/ui_cmd.py`, tested in `tests/terminal/test_ui_cmd.py`)
  starts the built App Host with `STRATA_UI_MODE=browser` and `STRATA_SERVER_URL`,
  prints and opens `http://127.0.0.1:8891` (the port moved with zone 11); `--stop`
  ends the page's host and never the model. It finds Node and a built host and
  downloads nothing.
- The page's figures for the README draw from `docs/media/assets/` (the sprite,
  Outfit and OFL moved there from `serve/web/`).
- Text touched by this: `SECURITY.md`, `docs/DETAILS.md`, `docs/TERMINAL_LAUNCH.md`,
  `docs/INSTALL.md`, `docs/MAC.md`, `docs/INTEL.md`, `docs/MCP_SERVER.md`,
  `docs/MCP_IMAGES.md`, `docs/AI_SETUP.md`, `AGENTS.md`.

**Sync rule:** if upstream touches `serve/web/`, `serve/webfiles.py`,
`sycl/serve/web/`, `tools/webtest/` or the web tests, we keep our deletion and
carry whatever the upstream change genuinely needed into the App Host's page or
the Terminal instead. Expect this zone to conflict on most syncs.

### 2. `strata stop` and `strata start`

`stop` and `start` are plain-word aliases: `start` = `launch` (every flag the
same), `stop` = `launch --stop`. Code: `strata_cli/cli.py`.
Tests: `tests/terminal/test_stop_start_aliases.py`. Docs: `AGENTS.md`,
`docs/TERMINAL_LAUNCH.md`, `docs/TERMINAL.md`.

### 3. `profile --help`, `profile delete`, the launch-profile mark

Code: `strata_control/profiles.py`. `profile delete NAME` removes a saved
profile and refuses while it serves (exit 6); `profile list` marks a `launch-*`
profile whose preset was deleted with the command that removes it (the JSON
list gains `launch_preset_saved` only for `launch-*` ids — every other line
stays byte-identical). Tests: `tests/terminal/test_profiles.py`.

### 4. `presets --help` description

The `presets` parser carries a plain-word description of what a tuning preset
keeps, the identity rule, apply/undo/off, and the three stores. The human line
in `strata --help` is unchanged. Tests: `tests/terminal/test_flow2_022.py`.

### 5. Unsloth's UD-Q4_K_XL for DeepSeek-V4.1-Flash (opt-in)

- `setup.py`: `DSV41_UDQ4_FILES` pins (2026-11-06 from the live Hub), the
  `UD-Q4_K_XL` entry in `MULTI_FILE_FAMILIES["deepseek41"]["models"]`, and the
  fit line in `main()`. Opt-in under `--family deepseek41`; the bare
  `--model ud-q4_k_xl` stays the Qwen file's it always was.
- `strata_control/dsv41_fit.py`: `UD_SETS` now plans both XL sets; the Q4
  figures are derived from the pinned sizes and say they are not timed.
- `docs/DEEPSEEK41.md` says why the other five held-out sets wait.
- Tests: `tools/test_setup_udq4.py`, `tools/test_setup_dsv41.py`,
  `tests/terminal/test_launch_dsv41.py`.

### 6. `roadmap/unsloth-sets/` — our own track (new, ours only)

The plan for the five held-out Unsloth sets in fork mode: README (phases end at
our decision, not a maintainer's), `task-a-q5k-engram.md` and
`task-b-mxfp4-down.md` (the two engine specs), the live-Hub pins with SHA-256
and HEAD-check logs, `catalog-prep-iq3.patch`, the layer-composition scans.
Upstream has nothing here — conflicts are impossible, but keep the pins honest
(re-check before acting on them).

### 7. Telemetry: per-card PCIe fields, temp history, power cap

`serve/telemetry.py` and `serve/server.py` send each card's own PCIe link and
traffic (`pcie_rx_mb`, `pcie_gen`, `pcie_gen_max`, `pcie_width` in `hardware.gpus`),
per-card load/power/PCIe/temp history (`history.gpus`), and each card's power
cap. One card sends exactly what it sent before. Tests in `serve/test_server.py`
(`AmdTelemetry.test_readings`). This came in with the Monitor branch and stays:
it feeds the App Host's Monitor page.

### 8. Closed research scripts removed

`tools/`: the DGX Spark one-off benches (except `dgx_bench.py`, which
`docs/DGX_SPARK_LINUX.md` measured its numbers with), `tools/par/` with its
captured outputs, the conversation-cache investigation with its seven tests,
six one-off probes. `tools/qwen35/` stays (`docs/QWEN36.md` sends people to its
gates); `metal/tools/gen_grids.sh` stays (it regenerates the checked-in ggml
grid header). `metal/tests/serve_probe.py` is gone with them.

**Sync rule:** if a merge re-adds a removed tool, check it is genuinely new
before keeping it; we removed each name only after re-checking that nothing
outside the removed set mentions it.

### 9. Small local fixes

- `setup.py run()`: the app's installer also runs the bundled Mac preparation
  tools in `metal/tools` (`metal_repack.py`, `stream_experts.py`).
- `.gitignore`: `.DS_Store` added.

### 10. The Monitor branch's page work — history only

The GPU-tile work on `f-web-monitor-gpu-tiles` (per-card tiles, split-run
layouts, name-over-number rows — commits `583a1a09`..`256f3704`) is in our
history, but its files (`serve/web/monitor.*`, the `tools/webtest` tests that
drove it) were deleted by zone 1. What survives is the telemetry, the API and
the tests of zone 7. Do not "restore" the page files during a conflict
resolution — the App Host's page is the page now.

### 11. Fork identity: DeepStrata installs beside Strata

Both products on one machine (commits `5afb038d` and the identity commit on
`fork-identity`). Everything the two would fight over carries our own name;
everything that cannot collide keeps the original's. Model files are the one
shared surface on purpose: an installed model is never downloaded twice.

**One module per language** is the only place these values live. Change an
identity value there, nowhere else:

| Module | Carries |
|---|---|
| `strata_control/fork_identity.py` | source of truth: product, command, folders, home store, repo, feed, ports |
| `apps/host/server/fork-identity.mjs` | the host's copy (same values, mirrored in `identity.HOST_PORT` etc.) |
| `apps/desktop/src-tauri/src/identity.rs` | the shell's copy (folders, preferred port, binary name) |

`tests/terminal/test_fork_identity.py` pins the values, diffs the three copies
against the Python module, pins the literals that cannot import it, and scans
product code for original-product values (`Niko1221/Strata`,
`ai.strata.desktop`, `strata-desktop`, `~/.strata`, ports `8791` / `47831`).
If a sync merge reintroduces one, that test fails — resolve toward the module.

| Surface | Original | DeepStrata |
|---|---|---|
| Product name | Strata | DeepStrata |
| Command | `strata` | `deepstrata` (`bin/deepstrata`, `.cmd`, `.ps1`) |
| POSIX state/share/config | `~/.local/*/strata` | `~/.local/*/deepstrata` |
| Windows data / settings | `%LOCALAPPDATA%\Strata`, `%APPDATA%\Strata` | `…\DeepStrata` |
| Home store, host data, session | `~/.strata` | `~/.deepstrata` |
| Documents workspace | `Documents\Strata\Workspace` | `Documents\DeepStrata\Workspace` |
| Models | `Documents\Strata\Models` | **shared, stays** (`MODEL_FOLDER`) |
| Page port (`strata ui`, host default) | 8791 | 8891 (vite dev proxy and `dev.mjs` follow) |
| Desktop shell port | 47831 | 47841 |
| Shell binary | `strata-desktop` | `deepstrata-desktop` (Cargo package = `MAINBINARYNAME`) |
| Tauri identifier | `ai.strata.desktop` | `ai.deepstrata.desktop` |
| Releases / feed / issues / snapshots | `Niko1221/Strata` | `ilfarpro/DeepStrata` (same artifact names) |
| Feed key, app minisign key | upstream's | ours, regenerated; secrets live outside the repo (`~/.deepstrata-signing/`) |
| Docker image labels | upstream | `DeepStrata`, our URLs |

Kept on purpose: `STRATA_*` environment names (one process, no collision); the
words people read (UI sentences, help texts, tray menus keep saying Strata);
engine binaries `strata`, `strata-dsv41`, `strata-vision*` and feed artifacts
(`strata-feed.json`) — each lives inside one product's own component folder or
release; the workspace convention `.strata/checks.json` and walk skip lists (a
project declares its checks once for every tool, like `.git`); `packaging/`
and `docker/` internal paths (`/opt/strata/…`, `strata-wrapper.sh`) that never
leave our image.

Files that carry a literal because they cannot import the module (each pinned
by the test): `setup.py`, `serve/server.py`, `sycl/setup_intel.py`,
`tools/strata_mcp.py`, `strata_cli/handover.py` (its own stdlib copy — it runs
before any package import), `apps/host/vite.config.ts`,
`apps/host/src/components/SnapshotDialog.tsx`, `tauri.conf.json`
(productName/identifier/endpoint/pubkey), `Cargo.toml`/`Cargo.lock`,
`strata_control/update_keys.json`.

Windows details a merge must not undo: the uninstaller's data page deletes
only `$LOCALAPPDATA\DeepStrata`, `$APPDATA\DeepStrata`, `$PROFILE\.deepstrata`
— never `Documents\Strata\Models` (shared), never the original's folders; the
migration macro matches only `${MAINBINARYNAME}.exe`. The stop-before-delete
macro kills by image path under `$INSTDIR`, so it never reaches the other
product's processes. `install.sh` / `install.ps1` unpack into `deepstrata` /
`DeepStrata` folders and link `deepstrata` on PATH.

**Sync rule:** identity lives in code only — never rename a string in a UI
sentence. When upstream adds code that writes a per-user folder, a port or a
download address, route it through the module; when upstream touches any file
above, take their change and re-apply our value from the module. A new
product-side identity value belongs in all three modules (plus the pins) in
the same commit. `tools/trellis/make_corpus.py` and
`apps/host/scripts/agent2-tools-flow.mjs` name the upstream repository on
purpose (a research contact line, a live web probe) and stay excluded.

## Tests a sync merge should run

- `python -m pytest tests/terminal/test_fork_identity.py` (identity: values,
  the three copies, the pins, the purity scan — run this first after a merge)
- `python -m pytest tests/terminal` (Terminal and Control)
- `python -m pytest tests/desktop/test_nsis_layout.py` (installer data safety;
  the migration cases need makensis)
- `python tools/test_setup_udq4.py`, `python tools/test_setup_dsv41.py`, and
  the other `tools/test_setup_*.py` that the merge touched
- `python -m pytest serve/test_server.py serve/test_monitor.py serve/test_prometheus.py`
  (telemetry and the server)
- `npm test` in `apps/host`
- anything else the merge touched, named in its commit message

## Keeping this file true

- A new divergence (a new file, a deletion, a changed user-visible line) gets
  an entry here in the same PR that lands it.
- A divergence an upstream change absorbs gets its entry deleted, and the
  merge message says so.
- `AGENTS.md` carries the fork-mode working rules (no upstream PRs or issues,
  byte-isolated engine branches, pin re-checks, no number without a box that
  measured it). This file carries the map; `AGENTS.md` carries the conduct.
