# Community benchmark: Unsloth UD-Q2_K_XL on NVIDIA GeForce RTX 4080 SUPER and RTX 5060 Ti (two cards)

Measured on 2026-11-13 with `tools/strata_bench.py` (suite 1, 3 runs per test, medians with ranges). Speeds are the engine's own timings; first-token times are measured by the client over HTTP. Speed only: no correctness check was run.

## Hardware and software

- GPU and VRAM: NVIDIA GeForce RTX 4080 SUPER, 16 GiB VRAM + NVIDIA GeForce RTX 5060 Ti, 16 GiB VRAM (both cards: layers 0-21 on the 4080 SUPER, 22-39 on the 5060 Ti; the `nvidia-smi` detail below was read from the first card only)
- CPU: Intel(R) Core(TM) i7-14700KF (20 cores, 28 threads); installed RAM: 188.5 GiB; storage: sda     File-Stor Gadget usb; nvme0n1 ADATA LEGEND 960 nvme
- OS: Linux 7.0.0-43-generic; GPU driver: 615.71.09; CUDA (driver supports up to): 13.4
- Strata: project 0.1.61, commit 6bf57402; engine ds4-0.1
- GPU detail (after the run, near idle): power_limit_w=320.00, power_draw_w=86.78, pcie=gen4 x16, pstate=P2, temp_c=48
- Background workloads: not recorded (GPU power limit/draw read above)

## Model and configuration

- Model and quantization: Unsloth **UD-Q2_K_XL** (`unsloth/DeepSeek-V4.1-Flash-1017-GGUF`, revision `8a6ec9cf`): 199.6 GB of model in seven shards (seven layers mix an MXFP4 down with a 2-bit IQ2 gate and up), its own 135 GB n-gram table in Q5_K (the seventh shard) and an 8 GB drafter, 343 GB of disk in all; its mixed layers need engine 0.1.60 or newer
- Context: 131072 tokens; parallel slots: 1
- Settings: the install's own configuration, unchanged; thinking off for the suite; temperature 0
- Experts in VRAM at load: 1358 slots of 15 360 (677 on the 4080 SUPER, 681 on the 5060 Ti); the other 14 002 (174.1 GB) were copied into RAM at load, 182 s; engine 0.1.60
- Why the set is named here: the server reports only the family name (`deepseek-v4.1-flash`), so the benchmark tool could not record which set it measured. The set, the cards and the expert counts were attached by hand from the launcher's engine start line for this run (2026-11-13 16:27:56, pack `deepseek-v4.1-flash-ud-q2_k_xl`, `--tp 2`).

## Method

Run by `python tools/strata_bench.py` against the HTTP API of a Strata server (already running); model loading is not timed. One warm-up chat and a 1K prompt are discarded first. Every prompt starts with a unique tag, so the conversation cache cannot help the fresh-prompt tests.

- short chat: the three questions of `tools/ab_engine.py`, up to 100 new tokens, 3 runs
- 4K / 16K prompts: generated word lists sized by a calibration request, 24 new tokens, 3 fresh prompts each
- agent session: system prompt and four tools, then 5 turns that each add about 1500 words of a project file and continue from the model's own earlier answers (the way agent clients resend a conversation), 64 new tokens per turn, 3 sessions; the cache numbers come from the answer's usage (`cached_tokens`) and timings (`prompt_n`)
- concurrency (when run): N requests sent at the same moment, total tokens over the wall time of the burst, latency over all requests

## Results

| Configuration | Actual prompt tokens | Reused tokens | Generated tokens | Runs | Prompt tok/s median and range | Decode tok/s median and range | TTFT seconds median and range |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| short chat | 32 (29-38) | 0 | 100 (100-100) | 9 | not measured | 18.0 (15.0-20.3) | 0.82 (0.75-1.04) |
| 4K prompt, fresh | 4125 (4125-4125) | 0 (0-0) | 24 cap | 3 | 248.0 (239.0-256.3) | not measured | 16.64 (16.10-17.27) |
| 16K prompt, fresh | 16562 (16562-16562) | 0 (0-0) | 24 cap | 3 | 392.8 (384.3-394.0) | not measured | 42.18 (42.05-43.12) |
| agent session, follow-up turns | 13402 (13401-13421) at the last turn | see below | up to 64 | 3 | see below | 17.5 (16.2-18.9) | 17.82 (16.68-19.59) |

Ranges are the lowest and highest of the runs; `Runs` counts requests that returned a token.

### Agent session (conversation cache)

Cache hit over the follow-up turns: 70.2 % (70.2-70.3); earlier-turn tokens read again per session: 5 (5-5) (earlier turns that the cache did not hold; the 12544 (12544-12545) new tokens of the files are read once); first turn (nothing cached) first token: 14.07 s (14.01-14.40).

| Turn (run 1) | Prompt tokens | Reused | Read | Prompt tok/s | First token (s) | Decode tok/s |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 602 | 0 | 602 | 41.8 | 14.397 | 14.2 |
| 1 | 3960 | 641 | 3319 | 169.5 | 19.59 | 16.2 |
| 2 | 5476 | 4022 | 1454 | 83.6 | 17.411 | 16.3 |
| 3 | 8579 | 5523 | 3056 | 162.1 | 18.865 | 16.9 |
| 4 | 10753 | 8630 | 2123 | 119.3 | 17.817 | 17.5 |
| 5 | 13402 | 10805 | 2597 | 143.2 | 18.154 | 17.4 |

Per-request data: `results.json`. Engine log lines from the start: `engine-log-excerpt.txt` (when this run started the server).

## Correctness and limitations

Not measured: this is a speed suite. The conversation uses the model's own replies, so token counts differ a little between runs. Machine details come from the server, `nvidia-smi`/`rocm-smi` and the OS; background load is not recorded. Total time of the suite: 640 s.
