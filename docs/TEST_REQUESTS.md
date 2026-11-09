# Test requests and hardware wanted

Strata is built and measured on a few machines. Results from other hardware are welcome and are used: they tell us
what a setup really does and show where the engine picks something wrong. Write a report as described in
[COMMUNITY_BENCHMARKS.md](COMMUNITY_BENCHMARKS.md): the build, the card and PCIe width, the CPU and RAM speed, the
model file, the exact command, three runs per size, and what you did not test. Post it in an issue, or on the pull
request it is about.

This list is edited by the maintainers. An item stays until a report answers it.

| Wanted | Why | Example of an earlier answer |
|---|---|---|
| Two NVIDIA cards of different sizes (a large and a small one), in both card orders, with `--layer-split auto` | There is no unequal pair here to check the split planner; the card order and the small card's free memory changed the split | #880, #1238 |
| Three or four NVIDIA GPUs | No multi-GPU NVIDIA rig of that size here | #880 |
| Pascal (sm_61: Tesla P40, P100, GTX 10 series) | Older cards run on the fp32 path and can behave differently from newer ones | #875, #876 |
| A Linux host with 32 GB of RAM or less | The expert file tier and the resident mode depend on how much of the model fits in RAM | #1194 |
| A long soak (an hour or more of back-to-back requests) on a split or resident setup | Shows crashes, drift and heat that a short run does not | #848 |
| AMD RX 7000 series (RX 7900 XTX, 7900 XT, 7800 XT and similar, RDNA 3) on Linux or Windows | The RX 7000 builds have not been run on a real RX 7000 card by us; the Docker image for them is also untested on a card | none yet |
| Windows with an AMD card: the app or the Terminal, one model, one picture sent | The Windows AMD build has not been run on a real card, and neither has its picture reader (CPU under 24 GB, GPU from 24 GB) | none yet |
| A Mac with Apple silicon (macOS 15 or newer): the app or the Terminal with a real model | Mac builds have run on very few Macs. Tell us the Mac's memory, the model, the speed, and anything that failed. Pictures on the Mac (GPU reader) were run on one Mac mini M4 only: tell us if they work on yours. The signing script has not been run against Apple's real notary service yet, so a first-launch warning is expected and useful to hear about | none yet |
| Linux/amd64 with an NVIDIA GeForce RTX card: build the DeepSeek-4.1 engine and `tests/dsv41/q5k_engram_test` (`tests/dsv41/build_q5k_test.sh cuda <out>`, `CUDA_ARCH=86/89/120`), run it, then again on the model's own bytes - on the pinned release that table is shard 6 alone, so `q5k_engram_test --file DeepSeek-V4.1-Flash-1017-UD-IQ2_XXS-00006-of-00006.gguf --off 192 --rows 4096` (the tensor-info ends at byte 173, so the Q5_K bytes start at 192) | The Engram's Q5_K table reader (fmt 4, `src/dsv41/ds4_q5k.cuh`), which is what opens the UD-IQ2_XXS set, is new and has never been compiled or run on a card. On a Mac host build the shared decoder matched an independent CPU reference across 10.24M values of the model's real shard-6 bytes (0 mismatches), so the decode maths and the model's own bytes are checked; what has NOT been exercised is the GPU device kernel and the engine end-to-end | none yet |
| An AMD (ROCm, RDNA 3/4) host and a Mac, for the same `q5k_engram_test` / engine build (`build_q5k_test.sh hip`, and the Metal engine which now decodes Q5_K rows too) | The Q5_K change touches the shared CUDA/HIP source and the Metal kernels and host guard alike; no CUDA/HIP/Metal toolchain is present on the machine that wrote it, so those builds are not run here | none yet |

To add a request, open a pull request that adds a row, or tell the maintainers in an issue.
