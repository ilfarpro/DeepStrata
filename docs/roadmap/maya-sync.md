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
| Upstream now | **v1.1.63** (`6651b5b`, 2026-11-14), and `main` after it (`7e68a30`, 2026-11-15) |
| Delta | **98 commits** touching `src/ds4` from `v1.1.42` to the v1.1.63 tag; the later main commits this page names are taken one by one |

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
| `e9b5df4` (#369) | prompt sparse attention on tensor cores, sm_80+ (`sparse_attn_mma_kernel`); opt-in here (`STRATA_DS4_ATTN_MMA=1`), on by default in Maya | twin of the gfx12 WMMA path (our R9700 measurement: +14.7% prompt) | done |
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
| `18ada27`, `6b1119f`, `f692477` | `q8_mv` (Q8_0 GEMV) on the tensor cores for 3 to 16 columns (`STRATA_DS4_Q8MV_TC`), same bits, sm_80+ CUDA | opt-in; no default speed claim | done |
| `b4f78bb` | indexer slab rows from this prompt's keys, not `--max-context` (`STRATA_DS4_AI_RUN`); Maya says the same answers | VRAM only (128 rows) | done, opt-in; our 128K run moves the logits (see `Hardware run`), so it is quality-gated until the no-cache run says otherwise |
| `335f4f1`, `027992c` | load reads the shards round-robin (`STRATA_DS4_LOAD_INTERLEAVE`, on), `STRATA_DS4_LOAD_THREADS`, `STRATA_DS4_LOAD_DROP_CACHE` | EXL3 2.9: 109 -> 75 s load | done |
| `50a3313` (#639) | one-launch attention for a step of several requests (`MAYA_DS4_MULTI_ATTN`), same bits, on | 43.9 -> 49.3 tok/s aggregate (8 GPUs, `--parallel 2`) | planned |
| `7f431b6` | `STRATA_DS4_READ_ORDER=arrival`: prompts read in arrival order, answers write between pieces | agent p95 first token 13.5 s against 29.1 s (3 GPUs) | done |
| `807f3f0` (#772) | `ignore_eos` (run to `max_tokens`) and `logit_bias` on DeepSeek | 4-token answer -> 200 of 200 | done |
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
| `78228a0` | every parallel conversation is set aside when the engine ends, not only the bound one | done |
| `1b262d5`, `d08cbd8` | failed allocation / zeroing names its KiB size, the driver's reason and the free VRAM | done |
| `eab2eea` | a start says when another program holds a GPU's memory; the expert arena retries | done |
| `c1e95b2` | `STRATA_DS4_RAM_MARGIN_GB` for the memory planning (default 8 GiB) | done |
| `e0c6d59` | the Engram `mlock` error names the memlock limit and the `limits.d` fix | planned |
| `772f483` | EXL3 packs: an F16 dense matrix loads as BF16 | done (model load maps F16 to BF16; such a pack still to be run) |
| `68ef483` | the packer takes a BF16 head, INT4 Engram tables, type-ordered shards | planned (a packing tool, not the engine) |
| `2d30b9d` | a request with pictures, logprobs or ablation runs on one GPU instead of being refused | only with the mixed helper; skip |
| `afcbcad` | LF line endings (no content change) | skip |
| `206f800` | Windows build of the two-GPU helper | no helper here; skip |

### 4. Not ported in this branch: the list to keep

Everything here is deliberately left out of `f-maya-sync`. It is not forgotten and not judged
unimportant: each row says what it would take, so a later branch can pick it up without rediscovering
why it stopped. The next group is the work that can be taken in any order.

#### 4.1 Deferred, with what each needs

| Maya | What | Why not here | The first step next time |
|---|---|---|---|
| `50a3313`, `edd7aee`, `1b05d50`, `f0baf58`, `401fc17`, `4fa8fb6` | the tensor-parallel rework: AR2 (one-launch exact TP2 all-reduce), AR_P2P (buffers in the first GPU's VRAM), peer-VRAM all-reduce, uneven parts, host-free TP, DSpark spread | our `ArGroup` (src/dsv41/ds4_kernels.cuh) is host-staged: every member's partial goes through one host-mapped row, and the sums are a kernel over it. Maya's commits replace that transport (`ar2_kernel`, peer buffers, calibration, `DS4_AR2`/`DS4_AR_P2P`/`DS4_HIP_PEER_AR`) and need two or more NVIDIA GPUs to validate. A half-port would be untested code on the one path that cannot be checked here | branch `f-maya-tp` on a 2-GPU box. Start with AR2 exact mode for `nm == 2` (the prompt chunks), keep the host path as the fallback, gate bit-identity with `--multi-check` and the score set, then AR_P2P behind its ReBAR check, then peer AR |
| `50a3313` | MULTI_ATTN: a multi step's attention for all its rows in one launch (`ds4_multi_attn.hpp`, the selection/attention kernels, shared scratch) | our `--parallel` step launches attention per conversation; the one-launch version is a rework of the step's buffers and kernels, and the gain was measured with 8 GPUs (`--parallel 2`) | port with the same shape, measure with `--multi-check` (rows against alone) and `--parallel 2` on two cards |
| `50a3313` | `logprobs` on DeepSeek (engine side: `ds4_logprobs.hpp`, the `LP` lines, top-k/alternative lists) | our engine has no logprobs at all; the server's LP path was written for the Qwen engine. It is a feature of its own, not a sync fix | take it with a client that needs it: sampler plumbing first (top-k ids and log-probs per row), then the `LP` protocol, then the server's `LogprobEntries` |
| `8c34c00` | END: a request that leaves a GPU in a fault prints `END`, and the next request starts a fresh engine | our fatal path already exits 3 (`die_gpu_fatal`), and the server restarts the engine; a partial END without `Model::broken()` (the stream-query check) could hide a fault instead of reporting it | add `Model::broken()` (query every device's stream), call it after a request and at `STOP`, print `END` before `DONE`, and test the server's restart with a forced fault |
| `c25b1db`, `7f469f8` | LENT_RAM: pinned-RAM copies of experts a prompt's buffers lie over, so the CPU lane does not read them back from disk | needs the pinned store and the borrow path, a memory subsystem of its own | measure the disk read-back first; port only if the gain repeats here |
| `7f1b17d`, `7a4a96f` | indexer scores on the matrix cores (`INDEX_TC`) | gfx11's default and an NVIDIA opt-in in Maya; no NVIDIA measurement was published, and our indexer is scalar | port behind `STRATA_DS4_INDEX_TC`, gate with the score set on NVIDIA |
| `8896cac`, `387f824` | `HC_FUSE` / `ROUTE_FUSE` (small launches fused) and `DECODE_TRIM` (four more exact trims), checked at load | exact but hot: each needs its load-time shape check, the device suite, and a per-kernel test; no gain measured on our cards yet | one commit each: fuse, then check shapes at load, then `ds4_gguf_test` and a dump comparison |
| `42a4178`, `204299e` | prompt chunk from the share of experts on the CPU lane; 16384 with borrowed buffers | touches the prompt path's planning and the borrow rules; measured on 2 V100 with a RAM store | take after LENT_RAM or its measurement |
| `e0c6d59` | the Engram `mlock` error names the memlock limit and the `limits.d` fix | a message only; our Engram mlock path is not touched by this branch | one small commit when the Engram lock is next changed |
| `68ef483` | the packer takes a BF16 head, INT4 Engram tables, type-ordered shards | a packing tool, not the engine | take in `tools/dsv41` when a pack needs it |
| `6227775` | PDL (dependent launch) for the hot kernels | no NVIDIA measurement, and it changes the launch sequence everywhere | measure the launch gap first; likely never worth it on our cards |
| `450827f`, `41a630c` | lane parity (the PCIe lane's kernels judged over whole steps) | correctness/tooling for a lane we keep opt-in; no answer moved in the runs we have | take with the next lane change |

#### 4.2 Not for Strata

| Maya | Why |
|---|---|
| Multi-PC pool (`f422779`, `62a07e9`, `272b645`, `38e5d9a`, `dbf982e`, `398b2f0`, `6fe4422`, `fecc381`, and `ds4_part_net`) | a different deployment model (two PCs); not our release surface |
| AMD + NVIDIA helper (`e259267`, `0ff7fcb`, `695070a`, `ee61f9a`, `21e57fb`, `dcaa04a`, `f80a1be`) | DeepStrata does not support mixing card makes; experimental and large |
| AMD-only: gfx11 index default (`7a4a96f`), router BF16 (`3ab0908`), prompt F16 on RDNA 2 (`1244438`), expert MMQ (`468d055`), EXL3 lend/in-place (`99d912a`, `80c1a29`), gfx906/MI50 (`2a620cd`), ppc64le (`da3fafa`), wave64 (`2b2522d`) | no AMD engine in the release; source-build only. `INDEX_TC`'s gfx11 default is in 4.1 if an AMD build needs it |
| `--ablate`, `--suffix-draft`, `--ppl`, `--profile-out`, `--marks-check` | tools and niche; not user-facing speed |
| Engine watchdog (`MAYA_DS4_WATCHDOG_S`) | our server already watches a silent engine |
| `afcbcad` | LF line endings (no content change) |
| `206f800` | Windows build of the two-GPU helper | no helper here |

## How to test this branch on real hardware

Nothing in this branch was run on a GPU here (no NVIDIA driver). The commands below are the path a
machine with a card and a DeepSeek pack takes; run them on the target card, not on a CPU-only box.

`PACK` is the model pack's folder (the one with `tokenizer/` and the GGUF/EXL3 files); `EXE` is the
engine you build. `IDS` is a comma-separated token-id list (the engine's `--ids`; a 2,000-token prompt
is enough for the prompt path, and a short one for a smoke test).

### 1. Build

```sh
cmake -S . -B build-dsv41 -DCMAKE_BUILD_TYPE=Release \
      -DSTRATA_BUILD_DSV41=ON -DSTRATA_ENABLE_CUDA=ON \
      -DCMAKE_CUDA_ARCHITECTURES="89;120"
cmake --build build-dsv41 --target strata-dsv41 -j
```

For AMD: `ROCM_ROOT=/opt/rocm tools/dsv41/build_hip_linux.sh` (it builds `strata-dsv41` and the kernel
tests). Our new kernels are CUDA-only guards; the HIP build should be green, but that is the check to run.

### 2. Cheap gates, in order

```sh
EXE=$PWD/build-dsv41/strata-dsv41
$EXE --pack-info --pack "$PACK"                    # every file present, the pack's description
python tools/dsv41/smoke.py --exe "$EXE" --pack "$PACK" --gpus 0        # 16 tokens, the text must name Paris
./build-dsv41/ds4_gguf_test                        # the device suite (all cards; --mixed-cpu for the CPU lane)
./build-dsv41/ds4_gguf_test --rows                 # --parallel's multi rows, bit for bit
python tests/dsv41/short_prompt_check.py "$EXE" "$PACK" ids.txt --gpus 0   # the windows path, 3 runs
python -m pytest tests/serve tests/tools           # the server and setup tests (no GPU needed)
```

`tests/dsv41/*_test.cpp` (marks, slots, experts rule, pick rule, path policy, ...) are host tests too;
build them with their own scripts or the CMake test targets. `tests/dsv41/build_dq_test.sh` builds the
dequant/GEMM kernel tests by hand for CUDA or HIP.

### 3. The exactness gate for a "same bits" feature

The opt-in features that claim the same numbers (`STRATA_DS4_Q8MV_TC`, `STRATA_DS4_LOAD_*`,
`STRATA_DS4_RAM_MARGIN_GB`, `logit_bias`; `AI_RUN` is under the quality gate until the no-cache run
below says otherwise) are checked with the score tool: the same
segments through the engine with and without the switch. Top-1 agreement and a KL of 0 are the pass
condition. Two things make a run comparable with the next one, and the first hardware run missed both:

- **Pin the expert profile.** The pack's `expert-profile.bin` decides which experts sit in VRAM and which
  on the CPU lane, and the file is shared with the live server. Copy it once and pass
  `--expert-profile COPY --profile-readonly` to every run, so the placement cannot move between them.
- **Keep the window the same.** `--score` feeds 16-token windows by default: the old windows path. A
  switch that lives in the chunked prompt path (AI_RUN's slabs, ATTN_MMA) is not reached at all there.
  `--window 128` takes the chunked path for every run of a comparison. Different windows are not
  comparable with each other either (the prompt chunk size changes the last bits, as the Intel notes say).
- **Turn the expert cache off.** With the cache on, the expert that computes a token can move between the
  GPU and the CPU lane from one run to the next, and the two paths round differently: two identical runs
  of this build came out 99.33% top-1, KL 4e-4 apart. `--no-expert-cache` keeps the profile's VRAM set
  fixed and is the only mode where `cmp` means anything here.

AI_RUN needs a context long enough for its shorter slab to differ: on this pack `--max-context 16384`
already gives the 128-row slab, so the switch is a no-op. At `--max-context 131072` the context-wide slab
is 96 rows and AI_RUN's 128 rows are the change being tested.

```sh
python tools/quant_quality.py ids --tokenizer "$PACK/tokenizer" \
    --set data/quant_quality/v1 --seg-tokens 512 --out /tmp/dsv41.ids.json
cp "$PACK/expert-profile.bin" /tmp/profile.fixed
COMMON=(--pack "$PACK" --gpus 0,1 --tp 2 --max-context 131072 --window 128 \
        --expert-profile /tmp/profile.fixed --profile-readonly --reserve-mib 200 --no-expert-cache)

$EXE "${COMMON[@]}" --score /tmp/dsv41.ids.json --score-out /tmp/base.tsv
$EXE "${COMMON[@]}" --score /tmp/dsv41.ids.json --score-out /tmp/base2.tsv
cmp /tmp/base.tsv /tmp/base2.tsv && echo "deterministic"
STRATA_DS4_AI_RUN=1 $EXE "${COMMON[@]}" --score /tmp/dsv41.ids.json --score-out /tmp/ai_run.tsv
cmp /tmp/base.tsv /tmp/ai_run.tsv && echo "AI_RUN: identical"
```

The first pair is not ceremony: if two runs of the same build differ, nothing below them means anything.

For a quick prompt-only check, `--dump-logits` writes every prompt position's logits (F32, the
vocabulary per row); the two files must be `cmp`-identical for a feature that claims the same bits:

```sh
$EXE "${COMMON[@]}" --ids "$IDS" --n 0 --dump-logits /tmp/base.f32
STRATA_DS4_AI_RUN=1 $EXE "${COMMON[@]}" --ids "$IDS" --n 0 --dump-logits /tmp/ai.f32
cmp /tmp/base.f32 /tmp/ai.f32
```

Two caveats: `--dump-logits` covers the prompt, not the decode rows; and the Q8_0 tensor-core path
(`STRATA_DS4_Q8MV_TC=1`) only runs for 3 to 16 columns, which one-token decode never reaches. Exercise it
with `STRATA_DS4_Q8MV_TC=1 ./build-dsv41/ds4_gguf_test --rows` and with `--parallel` (two or more
conversations in a step), and compare with the scalar run.

### 4. Quality gate for a feature that moves the last bits

`STRATA_DS4_ATTN_MMA=1` uses BF16 products: within rounding of the scalar kernels, not the same bits
(the header of the kernel says so). It is selected only for a **chunked** prompt (`gen > 0`) of 16 rows or
more: `--window 128` in the commands above is what reaches it, and the "prompt attention on the tensor
cores" line at load only says the card is eligible, not that a kernel ran. Run the score set with and
without it and read top-1, KL and perplexity; do not require a byte-identical dump.

```sh
STRATA_DS4_ATTN_MMA=1 $EXE "${COMMON[@]}" --score /tmp/dsv41.ids.json --score-out /tmp/mma.tsv
python tools/quant_quality.py compare /tmp/base.tsv /tmp/mma.tsv
```

Record the numbers with the card, the pack and the context. A text check on a coding-agent prompt is
worth adding: the answer must stay coherent, and the first tokens should not drift.

### 5. Server features: `ignore_eos`, `logit_bias`, `--parallel`

```sh
# the engine alone: GEN carries the ids on its own line, the keys before them
{ printf 'GEN 8 logit_bias=1:-100 %s\nQUIT\n' "$IDS"; } | \
    $EXE --serve --pack "$PACK" --gpus 0 --max-context 16384
# the HTTP path (the engine under the server): max_tokens 8 with ignore_eos true must give 8 tokens
curl -s http://127.0.0.1:PORT/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4.1-flash","messages":[{"role":"user","content":"Count: one, two,"}],"max_tokens":8,"ignore_eos":true}'
```

`tests/serve/test_logit_bias.py` covers the validation (`ignore_eos` without a cap, an engine that does
not report `logit_bias=1`, the ban of the end ids). On a server, `--parallel 4` with four requests at
once, and the same prompts one at a time, must give the same text (greedy); `--multi-check` does that
inside the engine for `--parallel`'s rows.

### 6. Speed A/B

Use the same pack, context, card and settings for both runs; medians of three or more. The engine's own
`decode:` and `prompt:` lines are enough for a first look:

```sh
$EXE --pack "$PACK" --gpus 0 --max-context 16384 --ids "$IDS" --n 128
STRATA_DS4_ATTN_MMA=1 $EXE --pack "$PACK" --gpus 0 --max-context 16384 --ids "$IDS" --n 128
# read "prompt: ... tok/s" and "decode: ... tok/s" from stderr
```

For the server path, `python tools/strata_bench.py --runs 3` (or `--url` for a running server; the
launch's `--set STRATA_DS4_ATTN_MMA=1` puts the switch into its config). Do not compare a `--parallel`
run with a single-request one, and do not mix packs: the pack's last path part is in the report.

For two cards, `--tp 2 --gpus 0,1` runs the tensor-parallel path; `strata_control/dsv41_fit.py` writes
that split for two or more cards. The TP rework is not in this branch (4.1), so a TP A/B here measures
the old path only.

### 7. What to report

Say what passed, what failed and what was not run, kept apart; name the card, the driver, the pack, the
context and the engine build; and state that numbers from Maya's commits are his, not yours. A result
from a card we do not have is welcome in the issue with the commands above.

## Port log in this branch

| Commit | What | Validation |
|---|---|---|
| (this page) | the map above | read against Maya `6651b5b` and `7e68a30` |
| `q8_mv` tensor cores | `STRATA_DS4_Q8MV_TC` (off): 3..16 columns, same bits, sm_80+ | device suite and `--rows` pass with 0, 1, 2 on sm_89 (RTX 4080 SUPER); the sm_120 card passes the suite too |
| prompt attention on tensor cores | `STRATA_DS4_ATTN_MMA` (off): sm_80+, the gfx12 path's twin | prompt 37.03 -> 37.58 tok/s and decode unchanged (3 pairs, 512-token prompt, TP2); on the chunked path top-1 94.48%, KL 0.0236 (BF16 rounding, as the kernel says), so it needs the quality gate, not `cmp` |
| prompt read order | `STRATA_DS4_READ_ORDER=arrival` (off): a prompt keeps its place in line; pieces beside a running answer | `strata-dsv41` builds; server-side A/B with `--parallel` still to run |
| indexer slabs by run keys | `STRATA_DS4_AI_RUN` (off): fewer slabs for a short prompt | with the expert cache off and identical runs as the control, the switch still moves the logits: top-1 94.21%, KL 0.0181. Not a same-bits feature on this pack at 128K (its GEMM M changes 96 -> 128); opt-in, kept off, quality-gated |
| faster loads | `STRATA_DS4_LOAD_INTERLEAVE` (on), `STRATA_DS4_LOAD_THREADS` (12), `STRATA_DS4_LOAD_DROP_CACHE` (on) | with the expert cache off the rollback is **identical** (top-1 100.00%, KL 0); the earlier 0.00368 was placement noise, not the switch. Loads ran on both cards (copy into RAM 103-146 s) |
| `ignore_eos` and `logit_bias` | engine reports `INFO logit_bias=1`; the server turns `ignore_eos: true` into a ban of the end tokens | `tests/serve/test_logit_bias.py`: 16 pass, including the HTTP path |
| RAM margin | `STRATA_DS4_RAM_MARGIN_GB` (8): planning headroom, read once | `strata-dsv41` builds |
| EXL3 F16 dense | an F16 matrix loads as BF16 at model load | `strata-dsv41` builds; a pack with an F16 `engram.wkv` still to be run |
| end of a parallel engine | every conversation is set aside, not only the bound one | `strata-dsv41` builds; a `--parallel` run still to check |
| diagnostics | a full GPU at start, a failed allocation or zeroing, an arena that retries | `strata-dsv41` builds; messages read through, no GPU here to trigger them |

The host-only side ran here: `python -m pytest tests/serve` (1437 passed, 12 skipped; the five picture
tests need Pillow, installed for a second run where they pass), `tests/tools` (1718 passed, 6 skipped),
and `tests/serve/test_logit_bias.py` (17 passed, the HTTP path included). Ruff was not installed in this
environment.

#### Hardware run, 2026-11-15

RTX 4080 SUPER (sm_89) + RTX 5060 Ti (sm_120), driver 615.71.09, pack
`deepseek-v4.1-flash-ud-q2_k_xl`, TP2, `--max-context 16384`, engine build from this branch.

Passed: `--pack-info` on the real pack; `ds4_gguf_test` `ALL OK (0 failed)` on both cards; `--rows` with
`STRATA_DS4_Q8MV_TC=0/1/2` on sm_89; `short_prompt_check.py` (`ok`, 3 requests, 16 tokens each);
`INFO logit_bias=1` from the engine; the build's `--serve` protocol. ATTN_MMA's speed pair: prompt
37.03, 37.08, 37.03 -> 37.61, 37.58, 37.55 tok/s (+1.5%), decode inside the run-to-run noise.

Not valid: the first score comparison. The default run (`base.tsv`) differed from the next three, while
`STRATA_DS4_AI_RUN=1`, the load rollback and `STRATA_DS4_ATTN_MMA=1` came out **byte-identical to each
other**. That is the signature of the three later runs all taking the same path (the score's 16-token
windows do not reach ATTN_MMA, and AI_RUN's slab change is a no-op at 16K), with the first run different
for a reason the run did not isolate (the pack's shared `expert-profile.bin` and a cold first load are the
candidates). The comparisons are therefore void, not evidence for or against the switches. The corrected
recipe above pins the profile, holds the window at 128, and puts AI_RUN in a context where its slab
differs; the rerun is the gate.

The logit_bias probe written in the first message was also wrong: the engine takes `GEN <max_new>
[keys] <ids>` on one line, so the ids line was answered with `ERR unsupported command`. The corrected
form works: with token 223 banned the first token was 28 and the request ran its 8 tokens; the engine's
`INFO logit_bias=1` and the server-side test stand.

Second run (pinned profile, `--profile-readonly`, `--window 128`, `--max-context 131072`, TP2):

| Run | top-1 vs det1 | KL | KL p99 |
|---|---|---|---|
| det1 vs det2 (same settings) | 99.33% | 0.00040 | 0.0047 |
| det1 vs writable profile (same settings) | 99.10% | 0.00085 | 0.0117 |
| det1 vs load rollback | 97.57% | 0.00368 | 0.0406 |
| det1 vs `AI_RUN=1` | 95.26% | 0.01762 | 0.1700 |
| det1 vs `ATTN_MMA=1` | 94.99% | 0.01911 | 0.1732 |

The two identical-settings pairs differ, so **this TP2 configuration is not bit-reproducible** with the
expert cache on: the first divergence moved in each run (line 385, line 257), and the gap is small but
real (99.1-99.3% top-1). `--score` does not write the profile: both copies keep their hash. Against that
noise, `AI_RUN=1` and `ATTN_MMA=1` move the logits by 20-40x the noise; the load rollback sits at 4x and
is the one result that must be repeated before it is called. The next run switches the expert cache off
(`--no-expert-cache`) so the CPU/GPU placement cannot move, and repeats det1, det2, AI_RUN, the load
rollback and ATTN_MMA; that run decides whether `cmp` can be the gate at all.

With `--no-expert-cache` the same five variants came out:

| Run | top-1 vs det1 | KL |
|---|---|---|
| det1 vs det2 | **100.00%** | **0** |
| det1 vs load rollback | **100.00%** | **0** |
| det1 vs `AI_RUN=1` | 94.21% | 0.0181 |
| det1 vs `ATTN_MMA=1` | 94.48% | 0.0236 |

So the gate works once the expert cache is off: two identical runs are byte-identical, and the load
switches are exact. AI_RUN's "same answers" does not hold for logits at 128K as ported (its GEMM M
changes 96 -> 128), and ATTN_MMA moves them, as its kernel says it may. Both stay opt-in and off; neither
is a default. The run-to-run noise with the cache on is a property of the TP2 expert split, not of these
switches, and it is worth knowing before anyone reads a `cmp` on a live configuration.
