# Roadmap: Unsloth's five held-out DeepSeek-V4.1-Flash sets

**State: shipped. What is left is measurement, and this page lists it.**

Unsloth's repository carries nine quantizations of DeepSeek-V4.1-Flash. When this page was
written (2026-11-07, against `main` `0405bd77`), Strata offered three of them - UD-Q8_K_XL,
UD-IQ1_S and UD-IQ1_M - and two engine rules held back the other five:

- **A.** the Engram reader knew MXFP8, Q6_K and IQ4_NL, and the two 2-bit sets carry a **Q5_K**
  n-gram table, so neither loaded;
- **B.** a layer's three expert matrices had to be all MXFP4 or all low-bit, and four sets keep an
  **MXFP4 `down`** beside 2- or 3-bit gate and up, so none of them loaded.

Both rules are gone: A shipped in engine 0.1.59 (release 0.2.12.1), B in engine 0.1.60 (release
0.2.12.2). All five sets are on the version list, wired into setup and fit, and released. The
user-facing status of each one, with its sizes read off the shards, is in
[DEEPSEEK41.md](../../DEEPSEEK41.md).

## What is still open

| # | Item | State |
|---|---|---|
| 1 | Serve **UD-Q4_K_XL** (501 GB) on our own hardware | Never run here. Its planner figures are derived from the pinned sizes and say they are not timed. Wanted: one prompt, a tool call, 4k and 8k prompts, tok/s on an RTX 5070 or an R9700 |
| 2 | Serve **UD-IQ2_XXS** (300.5 GB) from a real set | The Q5_K reader is bit-exact against a reference decoder on real shard-6 bytes and on RTX cards. No real set has been served here yet. Wanted: load, one prompt, tok/s |
| 3 | Load **UD-Q2_K_XL / UD-IQ3_XXS / UD-IQ3_S / UD-Q3_K_XL** from a real set | The mixed expert shape passes on both target RTX cards (351 checks each) and no set has been loaded: they need 334.8 to 485.1 GB to download (343 to 493 GB on disk), and the current model occupies the same cards and RAM. Wanted: one prompt each, tok/s |
| 4 | Measure **quality** for all five | Not measured: no KL and no perplexity against the base set. Until a measurement exists, no quality word may appear in a doc or in the menu - for any of them |
| 5 | **Mac and DGX Spark** | The five stay hidden on macOS and on linux-aarch64 (`strata_control/dsv41_fit.py`, `MAC_NOT_YET` and `AARCH64_NOT_YET`) until a build and a run pass there. Drop a name from those tuples only after a run passes |
| 6 | **HIP and Metal** builds of both changes | The Q5_K decoder and the mixed expert layer share the CUDA/HIP source and the Metal kernels; both were built and run on NVIDIA cards only. The open requests, with the exact commands, are in [TEST_REQUESTS.md](../../TEST_REQUESTS.md) |
| 7 | **Speed constants** per set | The planner's per-set figures for the new sets come from `--plan` and headers, not from a real run. Correct them from measured tok/s on the cards that ran |

The exit criterion for the whole page is unchanged: each set either measured on real hardware, or
deliberately not offered, with the reason written down.

## What shipped (the record)

- **Engine 0.1.59 / release 0.2.12.1 - the Q5_K Engram table.** Fourth format `fmt = 4` in the
  reader (`src/dsv41/ds4_model.cu`), the decoder in `src/dsv41/ds4_q5k.cuh`, the same format in
  the Metal kernels. UD-IQ2_XXS wired into `setup.py` (`DSV41_UDIQ2_FILES`), into the fit entries,
  and behind an engine-version gate, so a pack whose table is not Q5_K reaches nothing new.
- **Engine 0.1.60 / release 0.2.12.2 - a mixed expert layer.** `gg::expert_shape_ok` accepts
  exactly one mixed form, gate and up of one low-bit type with an MXFP4 `down`, and still refuses
  every other mix. The other four sets wired the same way (`DSV41_UDQ2K_FILES`,
  `DSV41_UDIQ3X_FILES`, `DSV41_UDIQ3S_FILES`, `DSV41_UDQ3K_FILES`).
- **Tests that need no GPU and no download:** `tests/tools/test_setup_udtable.py`,
  `tests/tools/test_setup_udq4.py`, `tests/tools/test_setup_dsv41.py` and
  `tests/terminal/test_launch_dsv41.py` check the pins, the pack call, the engine gate and the
  launch path.
- **The two design notes** - [task-a-q5k-engram.md](task-a-q5k-engram.md) and
  [task-b-mxfp4-down.md](task-b-mxfp4-down.md) - are kept as written on 2026-11-07. Their code
  quotes and line numbers describe the tree of that day; the changes themselves are in the engine.

## Rules we keep

- **Pins are re-checked before every download.** The pins in this folder are held at revision
  `8a6ec9cf` (2026-11-07, live Hub, SHA-256 per file). How that was checked is recorded in
  [pins/rev-check.txt](pins/rev-check.txt) and [pins/rev-check-iq2.txt](pins/rev-check-iq2.txt);
  re-run it before acting on a pin, because a re-published set breaks the sizes the fit entries use.
- **A finding from reading code is static analysis** until a run on hardware confirms it. Say
  which one it is, every time.
- **No number without a machine that measured it.** Give the card, the set, the version and the
  workload beside the number.
- **Passed, failed and not run stay apart** in the record of each task.
- **Engine changes stay in their own branches**, reachable only from a pack that needs them, so a
  sync with the original repository stays mechanical. See [DIVERGENCE.md](../../../DIVERGENCE.md).

## What is in this folder

| Artifact | File | Use |
|---|---|---|
| This page | `README.md` | the state of the five sets |
| Design note A (Q5_K Engram table) | `task-a-q5k-engram.md` | history: why the reader takes `fmt 4` |
| Design note B (MXFP4 `down`) | `task-b-mxfp4-down.md` | history: why the expert rule takes one mixed form |
| Pins for UD-IQ2_XXS | `pins/iq2-files.json` | SHA-256 and sizes |
| Pins for UD-IQ3_XXS / UD-IQ3_S / UD-Q3_K_XL | `pins/iq3-files.json` | SHA-256 and sizes |
| Pins for UD-Q2_K_XL | `pins/q2k-files.json` | SHA-256 and sizes |
| Hub HEAD-check logs | `pins/rev-check.txt`, `pins/rev-check-iq2.txt` | when each pin was checked |
| Layer composition and bytes per role | `scans/bytes3-out.txt`, `scans/bytes-iq2b.txt`, `scans/bytes-q2k.txt` | the evidence the notes quote |

## History

- **2026-11-07** - written against `main` `0405bd77`, in Russian, as a plan in fork mode: no
  upstream issues or pull requests, and the scope decisions our own.
- **2026-11-09 to 11-13** - both engine changes and the wiring shipped (0.2.12.1 and 0.2.12.2),
  and the plan's phases 1 and 2 were done.
- **2026-11-13** - rewritten in English as the list of what is still open, and moved from the
  repository root to `docs/roadmap/`. The catalog-preparation patch that this page used to carry
  was removed: its content is in `setup.py` and `strata_control/dsv41_fit.py`, and it no longer
  applied to the tree.
