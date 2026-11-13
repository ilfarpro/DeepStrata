# Community benchmark: Unsloth UD-IQ2_XXS on NVIDIA GeForce RTX 5060 Ti

Measured on 2026-11-13 with `tools/strata_bench.py` (suite 1, 3 runs per test, medians with ranges). Speeds are the engine's own timings; first-token times are measured by the client over HTTP. Speed only: no correctness check was run.

## Hardware and software

- GPU and VRAM: NVIDIA GeForce RTX 5060 Ti, 16 GiB VRAM (one card, on a PCIe gen4 x4 link)
- CPU: Intel(R) Core(TM) i7-14700KF (20 cores, 28 threads); installed RAM: 188.5 GiB; storage: sda     File-Stor Gadget usb; nvme0n1 ADATA LEGEND 960 nvme
- OS: Linux 7.0.0-43-generic; GPU driver: 615.71.09; CUDA (driver supports up to): 13.4
- Strata: project 0.1.61, commit 6bf57402; engine ds4-0.1
- GPU detail (after the run, near idle): power_limit_w=180.00, power_draw_w=87.20, pcie=gen4 x4, pstate=P1, temp_c=49
- Background workloads: not recorded (GPU power limit/draw read above)

## Model and configuration

- Model and quantization: Unsloth **UD-IQ2_XXS** (`unsloth/DeepSeek-V4.1-Flash-1017-GGUF`, revision `8a6ec9cf`): 165 GB of model in six shards, its own 135 GB n-gram table in Q5_K (the sixth shard) and an 8 GB drafter, 308 GB of disk in all; it needs engine 0.1.59 or newer
- Context: 131072 tokens; parallel slots: 1
- Settings: the install's own configuration, unchanged; thinking off for the suite; temperature 0
- Experts in VRAM at load: 521 slots of 15 360 (the other 14 839 came from RAM); engine 0.1.60
- Why the set is named here: the server reports only the family name (`deepseek-v4.1-flash`), so the benchmark tool could not record which set it measured. The set, the card and the expert counts were attached by hand from the launcher's engine start line for this run (2026-11-13 15:46:44, pack `deepseek-v4.1-flash-ud-iq2_xxs`).

## Method

Run by `python tools/strata_bench.py` against the HTTP API of a Strata server (already running); model loading is not timed. One warm-up chat and a 1K prompt are discarded first. Every prompt starts with a unique tag, so the conversation cache cannot help the fresh-prompt tests.

- short chat: the three questions of `tools/ab_engine.py`, up to 100 new tokens, 3 runs
- 4K / 16K prompts: generated word lists sized by a calibration request, 24 new tokens, 3 fresh prompts each
- agent session: system prompt and four tools, then 5 turns that each add about 1500 words of a project file and continue from the model's own earlier answers (the way agent clients resend a conversation), 64 new tokens per turn, 3 sessions; the cache numbers come from the answer's usage (`cached_tokens`) and timings (`prompt_n`)
- concurrency (when run): N requests sent at the same moment, total tokens over the wall time of the burst, latency over all requests

## Results

| Configuration | Actual prompt tokens | Reused tokens | Generated tokens | Runs | Prompt tok/s median and range | Decode tok/s median and range | TTFT seconds median and range |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| short chat | 32 (29-38) | 0 | 100 (100-100) | 9 | not measured | 17.2 (16.7-18.4) | 0.84 (0.76-1.01) |
| 4K prompt, fresh | 4125 (4125-4125) | 0 (0-0) | 24 cap | 3 | 204.2 (197.3-205.3) | not measured | 20.20 (20.10-20.92) |
| 16K prompt, fresh | 16562 (16562-16562) | 0 (0-0) | 24 cap | 3 | 448.7 (448.1-449.0) | not measured | 36.93 (36.91-36.98) |
| agent session, follow-up turns | 13396 (13379-13415) at the last turn | see below | up to 64 | 3 | see below | 17.8 (16.6-18.5) | 20.03 (18.35-22.30) |

Ranges are the lowest and highest of the runs; `Runs` counts requests that returned a token.

### Agent session (conversation cache)

Cache hit over the follow-up turns: 70.2 % (70.1-70.2); earlier-turn tokens read again per session: 5 (5-5) (earlier turns that the cache did not hold; the 12544 (12544-12546) new tokens of the files are read once); first turn (nothing cached) first token: 14.68 s (14.54-14.96).

| Turn (run 1) | Prompt tokens | Reused | Read | Prompt tok/s | First token (s) | Decode tok/s |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 602 | 0 | 602 | 40.3 | 14.956 | 16.4 |
| 1 | 3951 | 632 | 3319 | 148.9 | 22.298 | 16.6 |
| 2 | 5467 | 4013 | 1454 | 76.6 | 18.99 | 16.6 |
| 3 | 8576 | 5520 | 3056 | 142.1 | 21.52 | 16.9 |
| 4 | 10750 | 8627 | 2123 | 108.7 | 19.542 | 17.1 |
| 5 | 13396 | 10799 | 2597 | 126.4 | 20.567 | 17.3 |

Per-request data: `results.json`. Engine log lines from the start: `engine-log-excerpt.txt` (when this run started the server).

## Correctness and limitations

Not measured: this is a speed suite. The conversation uses the model's own replies, so token counts differ a little between runs. Machine details come from the server, `nvidia-smi`/`rocm-smi` and the OS; background load is not recorded. Total time of the suite: 670 s.
