# Syncing the DeepSeek engine with Project Maya

DeepStrata's DeepSeek-V4.1 engine (`src/dsv41`, `strata-dsv41`) is a port of Project Maya's
`maya-ds4` (`src/ds4`), MIT, credited in [third_party/project-maya/NOTICE.md](../../third_party/project-maya/NOTICE.md).
Maya keeps improving it after every release. This page is the map of that upstream work: what we have
taken, what is worth taking, what we deliberately leave out, and how a port is done. Read it before a
sync, and update it in the same commit that lands a port.

## Where we stand

| | |
|---|---|
| Imported | Maya **v1.1.17** (`8d9a9b5d`), 2026-10-20 |
| Ported since | v1.1.18 to v1.1.42 (staged merges: PCIe lane, one-GPU stream, restore points, `--parallel`, prompt path rules, thread-pool fence and the small fixes; see NOTICE.md) |
| Last one-off port | the v1.1.45 fix for a long prompt on one GPU (`c60833cc`, from Maya `c968b94`) |
| Upstream now | **v1.1.63** (`6651b5b`, 2026-11-14) |
| Delta | **98 commits** touching `src/ds4` after `v1.1.42` |

The trees have diverged a lot on purpose. `src/dsv41` is 33.8k lines and `src/ds4` 51.2k; the core
files differ almost everywhere (`ds4_model.cu` 15.1k against 23.6k lines, `ds4_main.cpp` 2.9k against
4.2k, `ds4_kernels.cu` 2.7k against 5.3k). Maya also grew whole subsystems we do not have: a multi-PC
pool (`ds4_part_net`, `ds4_place`, `ds4_ram_slice`), an AMD + NVIDIA helper (`ds4_rhelper`), peer
all-reduce (`ds4_peer_*`), lane parity, a watchdog and dependent launches.

DeepStrata carries work Maya does not have. A wholesale copy would lose or have to redo it:
the Metal/macOS engine (`ds4_os_apple.h`, `STRATA_METAL`, the Metal vision tower), the Windows RAM
expert store, IQ1_M and the mixed MXFP4-`down` layers, the TQ (trellis) kernels, split-KV decode
attention, the vision tower in RAM, and the whole server side (restore-point keys, slots, pictures,
`--parallel`, the `END` line).

**Decision: no wholesale copy.** Port feature by feature, with the smallest useful diff, and keep the
engine we can test.

## Rules for a port

- One feature per commit (and per pull request). Lead with the problem and the result, then the approach,
  then the validation and the limits, as in [CONTRIBUTING.md](../../CONTRIBUTING.md).
- Port from the Maya commit or release that carries it, and name that commit in the port commit and in
  `NOTICE.md`.
- Rename the environment to Strata's: `MAYA_DS4_*` and `DS4_*` become `STRATA_DS4_*` (a few keep a
  `DS4_` spelling inside comments only). User-facing `maya` strings stay in the engine's log only where
  they identify the protocol.
- New behavior is **opt-in**, and the old path stays byte-identical until a measurement on our hardware
  says otherwise (`AGENTS.md`, `CONTRIBUTING.md`).
- Prefer the changes that keep the answers bit for bit ("the same bits", "the same tokens"). A change
  that moves the last bits needs a quality gate (KL / ids, as `DS4_FAST_ATTN` had) and stays opt-in.
- Every port builds for CUDA and HIP where the files allow it. The release carries CUDA 13 only; HIP is
  a source build, so AMD-only changes are lower priority.
- Add or update the tests it can run without a model: `tests/dsv41/` for kernels and rules,
  `tests/serve/` for the server, `tests/tools/` for setup. State what was not run.
- Bump the engine version (`CMakeLists.txt`, `project(strata VERSION ...)`) and the `min_engine` gates
  when a port ships in a release.

## The delta, by area

Status: **done** in this branch, **planned** next, **later** (worth taking, not yet), **skip** (not for us).

### 1. NVIDIA prompt reading and tensor parallel (the largest gains)

| Maya | What | Measured by Maya | Status |
|---|---|---|---|
| `e9b5df4` (#369) | prompt sparse attention on tensor cores, sm_80+ (`sparse_attn_mma_kernel`), on by default | twin of the gfx12 WMMA path (our R9700 measurement: +14.7% prompt) | planned |
| `50a3313` (#639) | TP2 prompt all-reduces in one launch (`MAYA_DS4_AR2`), exact; default on NVIDIA since v1.1.55 | +1.5% at 8K, +3% at 32K (3x V100, `--tp 2`) | planned |
| `edd7aee` (#762) | all-reduce buffers in the first GPU's VRAM (`DS4_AR_P2P`), opt-in, needs ReBAR >= 1 GiB | 12K: 1041 -> 1290 tok/s, 24K: 1116 -> 1399, decode 96 -> 103 (6x RTX 3090, TP2) | planned |
| `1b05d50` (#773) | exact peer-VRAM all-reduce over NVLink (`MAYA_DS4_HIP_PEER_AR`, CUDA built with `DS4_CUDA_PEER_AR`; `auto` probes) | ~1.3x long prompts on 8x RTX 3090 | planned |
| `f0baf58` (#766) | TP parts sized from each GPU's own free room | fills every member; text can move (split change) | later |
| `401fc17` (#701) | a TP layer with every expert in VRAM leaves the host out (`MAYA_DS4_HOST_FREE_TP`), exact | decode, no headline number | later |
| `4fa8fb6` | DSpark blocks spread over a TP drafter group (`MAYA_DS4_DRAFT_SPREAD`), placement only | +4 / +2 / +3% prompts (3x V100); answers can move | later |

`strata_control/dsv41_fit.py` already writes `--tp 2` for two or more cards, so these are on our real
multi-GPU path, not a hypothetical one.

### 2. Exact one-GPU optimizations

| Maya | What | Measurement | Status |
|---|---|---|---|
| `18ada27`, `6b1119f`, `f692477` | `q8_mv` (Q8_0 GEMV) on the tensor cores for 3 to 16 columns (`DS4_Q8MV_TC`), same bits, sm_80+ CUDA | opt-in; no default speed claim | planned |
| `b4f78bb` | indexer slab rows from this prompt's keys, not `--max-context` (`DS4_AI_RUN`), same answers | VRAM only (128 rows) | planned |
| `335f4f1`, `027992c` | load reads the shards round-robin (`MAYA_DS4_LOAD_INTERLEAVE`), same bytes, on | EXL3 2.9: 109 -> 75 s load | planned |
| `50a3313` (#639) | one-launch attention for a step of several requests (`MAYA_DS4_MULTI_ATTN`), same bits, on | 43.9 -> 49.3 tok/s aggregate (8 GPUs, `--parallel 2`) | planned |
| `7f431b6` | `MAYA_DS4_READ_ORDER=arrival`: prompts read in arrival order, answers write between pieces | agent p95 first token 13.5 s against 29.1 s (3 GPUs) | planned |
| `807f3f0` (#772) | `ignore_eos` (run to `max_tokens`) and `logit_bias` on DeepSeek | 4-token answer -> 200 of 200 | planned |
| `8896cac` (#511) | `HC_FUSE` / `ROUTE_FUSE`: a token's small launches fused, checked at load | opt-in, exact | later |
| `387f824` (#512) | `DECODE_TRIM`: four more exact trims, checked at load | opt-in, exact | later |
| `7f1b17d`, `7a4a96f` | indexer scores on the matrix cores (`DS4_INDEX_TC`); gfx11 default, NVIDIA opt-in | +3% / +6% / +10% at 8K / 32K / 64K on gfx11 | later |
| `c25b1db`, `7f469f8` | lent experts from pinned RAM (`DS4_LENT_RAM_GB`), same bytes | avoids the disk read-back after a prompt | later |
| `42a4178`, `204299e` | prompt chunk from the share of experts on the CPU lane; 16384 with borrowed buffers | +10 / +9 / +12% at 12K / 20K / 29K (2x V100) | later |

### 3. Fixes, protocol and diagnostics

| Maya | What | Status |
|---|---|---|
| `c968b94` (v1.1.45) | recent-slot fills drained before a prompt borrows the tail (long prompt, one GPU) | done (`c60833cc`) |
| `8c34c00` | a request that leaves a GPU in a fault prints `END`, and the next request starts a fresh engine | planned |
| `78228a0` | every parallel conversation is set aside when the engine ends, not only the bound one | planned |
| `1b262d5`, `d08cbd8` | failed allocation / zeroing names its KiB size, the driver's reason and the free VRAM | planned |
| `eab2eea` | a start says when another program holds a GPU's memory | planned |
| `c1e95b2` | `DS4_RAM_MARGIN_GB` for the memory planning (default 8 GiB) | planned |
| `e0c6d59` | the Engram `mlock` error names the memlock limit and the `limits.d` fix | planned |
| `772f483`, `68ef483` | EXL3 packs: an F16 dense matrix loads as BF16; the packer takes a BF16 head, INT4 Engram tables, type-ordered shards | check whether we read such packs |
| `2d30b9d` | a request with pictures, logprobs or ablation runs on one GPU instead of being refused | only with the mixed helper; skip |
| `afcbcad` | LF line endings (no content change) | skip |
| `206f800` | Windows build of the two-GPU helper | no helper here; skip |

### 4. Deliberately not ported

| Maya | Why |
|---|---|
| Multi-PC pool (`f422779`, `62a07e9`, `272b645`, `38e5d9a`, `dbf982e`, `398b2f0`, `6fe4422`, `fecc381`, and `ds4_part_net`) | a different deployment model (two PCs); not our release surface |
| AMD + NVIDIA helper (`e259267`, `0ff7fcb`, `695070a`, `ee61f9a`, `21e57fb`, `dcaa04a`, `f80a1be`) | DeepStrata does not support mixing card makes; experimental and large |
| AMD-only: gfx11 index default (`7a4a96f`), router BF16 (`3ab0908`), prompt F16 on RDNA 2 (`1244438`), expert MMQ (`468d055`), EXL3 lend/in-place (`99d912a`, `80c1a29`), gfx906/MI50 (`2a620cd`), ppc64le (`da3fafa`), wave64 (`2b2522d`) | no AMD engine in the release; source-build only |
| `--ablate`, `--suffix-draft`, `--ppl`, `--profile-out`, `--marks-check` | tools and niche; not user-facing speed |
| Engine watchdog (`MAYA_DS4_WATCHDOG_S`) | our server already watches a silent engine; revisit with TP hangs |
| `PDL` (`dependent launch`, `6227775`) | touches the hot kernels; no NVIDIA measurement; high risk |
| lane parity (`450827f`, `41a630c`), logprobs engine side (`50a3313`) | correctness/tooling; take only with a task that needs them |

## Tests and measurements a port runs

- `cmake --build build-mixed --target strata-dsv41` (CUDA), and the HIP build where the files change.
- `tests/dsv41/ds4_gguf_test` for kernels and paths (device), `tests/dsv41/*_test.cpp` for rules
  (`marks_test`, `slot_test`, `experts_rule_test`, `pick_rule_test`, `path_policy_test`, ...).
- `python -m pytest tests/serve` for the server side.
- A quality gate for a bits-changing port: `--score` KL and ids against the scalar path, the same way
  `DS4_FAST_ATTN` was gated.
- A speed A/B on the hardware named in the commit: one card (RTX 3060 12 GB / 5070), two cards for TP,
  and the R9700 box for HIP. Say what was not run.

## Port log in this branch

| Commit | What | Validation |
|---|---|---|
| (this page) | the map above | read against Maya `6651b5b` |
