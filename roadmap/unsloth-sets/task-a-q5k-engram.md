# [Engine] Read the n-gram table in Q5_K (opens Unsloth's UD-IQ2_XXS set)

## The problem and the result we want

Unsloth ships this model's 2-bit sets, UD-IQ2_XXS (300.5 GB) and UD-Q2_K_XL (334.8 GB), with the
n-gram table as **Q5_K** (135,172,021,792 bytes each - same size, different bytes: each set carries
its own table). The Engram reader knows three types and stops cleanly at any other
(`src/dsv41/ds4_model.cu:3452-3470`):

```cpp
} else if (tw->dtype == "MXFP8") {
    fmt = 0;
} else if (tw->dtype == "Q6_K") {
    fmt = 1;
} else if (tw->dtype == "IQ4_NL") {
    fmt = 2;
} else if (pk.get("layers." + id + ".engram.embed.scale") != nullptr) {
    fmt = 3;   // (an old pack: FP8 codes in one tensor, their E8M0 scales in another)
} else {
    std::fprintf(stderr,
                 "ds4: Engram layer %s: engram.embed.weight type %s is not one the Engram reader handles "
                 "(MXFP8, Q6_K, IQ4_NL)\n",
                 id.c_str(), tw->dtype.c_str());
    std::exit(2);
}
```

So neither set loads today. We want a fourth format: the same reader and the same row-cache taking a
Q5_K table. That alone makes UD-IQ2_XXS loadable - its routed experts are IQ1_S, IQ2_XXS, IQ2_XS,
IQ2_S, IQ3_XXS and one layer's Q2_K down, every one a type the engine already takes on both lanes
(the same families as the offered UD-IQ1_S and UD-IQ1_M). UD-Q2_K_XL also needs the MXFP4-down rule
of the companion issue before it loads; this issue alone does not open it.

Why it is worth it: UD-IQ2_XXS wants about 165 GB of weights against the offered XL sets' 298 to
306, and its experts - the part that wants RAM - are 157 GB against 289. That fits computers that
cannot hold any offered XL set.

## What the change touches (reading the code - no patch proposed yet)

- the format chain above (`ds4_model.cu:3452`), plus a Q5_K superblock alignment check beside the
  Q6_K one (`eg_hd % 256`, `ds4_model.cu:3473`);
- row bytes and the read planner (`eg_issue`, `ds4_model.cu:7819-7830`: `mx8` / `q6k` / `iq4`
  branches - Q5_K rows are k / 256 * 176 bytes with the scales inside each superblock, like Q6_K);
- the dequant kernel `engram_dequant_tok_kernel` (`ds4_kernels.cu:1967`, launched at `:2331`, with
  the Metal host hook tried first at `:2333`);
- every backend that compiles that kernel; the pack tool already knows Q5_K and slices the tensor
  whole (`tools/dsv41/pack_gguf.py`, `TYPES`: `13: ("Q5_K", 256, 176)`).

Nothing about the offered sets changes: the new branch is reached only by a pack whose table is
Q5_K, and no pack Strata builds today has one.

## How the findings were produced (reproducible, no GPU here)

- `per_layer_token_embd.weight`, dims [256, 768022850] = 196,613,849,600 values, type Q5_K (ggml id
  13 in Unsloth's fork) - read from the tensor headers of the last shard of each set by ranged GET
  of the first 3 MB at the pinned revision (`8a6ec9cf118e785a362ea94ee123c61c70553be6`, live-checked
  2026-11-07; every file also HEAD-checked there, sizes match).
- **This is unconfirmed static analysis**: no pack of either set has been made or run on any GPU.

## File pins (live Hub, 2026-11-07)

Both sets, every file, live Hub at the pinned revision (2026-11-07; both table files are
135,172,021,792 bytes with different hashes - each set carries its own table):

UD-IQ2_XXS - 6 files, 300,475,464,795 bytes:
```
DeepSeek-V4.1-Flash-1017-UD-IQ2_XXS-00001-of-00006.gguf        5780923  22ad51c224e499883c1a753ad35719af44c296bbc8bcb9c931de6bdf950fa3aa
DeepSeek-V4.1-Flash-1017-UD-IQ2_XXS-00002-of-00006.gguf    49955895008  6880d0483490504a530b72686d79c92daa46e58ca5b71763bbfa3778d8a6131a
DeepSeek-V4.1-Flash-1017-UD-IQ2_XXS-00003-of-00006.gguf    49950675200  04b3ec7947255efaf789da77557da9180e93128948644828798d07b8a6e7a60a
DeepSeek-V4.1-Flash-1017-UD-IQ2_XXS-00004-of-00006.gguf    48971469632  318762f39cd7ebf03e3d7b30feaf938fab0464a9c2d6c0e0e99fc030bbfcd820
DeepSeek-V4.1-Flash-1017-UD-IQ2_XXS-00005-of-00006.gguf    16419622240  34fa48fbe2237b1b71a4f18a04bbe10946f6b1c76b7bbff21c76da630da677b6
DeepSeek-V4.1-Flash-1017-UD-IQ2_XXS-00006-of-00006.gguf   135172021792  406103485a7a7193337adee38b7178d3b636940aaa3ca033bc91e0d3687e3031
```
UD-Q2_K_XL - 7 files, 334,793,544,836 bytes:
```
DeepSeek-V4.1-Flash-1017-UD-Q2_K_XL-00001-of-00007.gguf        5780868  6b6e9ace54298156974cc3e024e5ba86d50c40c06482da8f64fece77d7f73452
DeepSeek-V4.1-Flash-1017-UD-Q2_K_XL-00002-of-00007.gguf    49290279808  fa56e7cb79f88d992170bbc41b192991bd89b9534c53106e328fc03a5d26afed
DeepSeek-V4.1-Flash-1017-UD-Q2_K_XL-00003-of-00007.gguf    49745070208  b279f9bbb331460d182658a9ec6a49eb232ef7d8c69600a2c53d5d797bf037b4
DeepSeek-V4.1-Flash-1017-UD-Q2_K_XL-00004-of-00007.gguf    48680128800  c7fed7b94a11aeafbf07e164940718fab26e99a5f1d9986af0602c6515d18bff
DeepSeek-V4.1-Flash-1017-UD-Q2_K_XL-00005-of-00007.gguf    49970937696  c8d6bd8063607e788db7daed01487f4e87a9529d5756dd561481bed78fea7d6d
DeepSeek-V4.1-Flash-1017-UD-Q2_K_XL-00006-of-00007.gguf     1929325664  2f62bf418b0f722254728bdd88b4b1c88c1b64a4d664c7b696c08fecd49abd76
DeepSeek-V4.1-Flash-1017-UD-Q2_K_XL-00007-of-00007.gguf   135172021792  a653c5309b7c74a6c41580aaa8c2d2bbdae0342253bca2cd3fc9e69d05f3d0eb
```

## Validation checklist

On CUDA (the RTX 5070 box) and AMD gfx1200 (the R9700 box), after an implementation:

- [ ] engine builds on both backends (and SYCL/Metal compile, the sets stay auto-hidden on Mac)
- [ ] UD-IQ2_XXS downloads (resumable, SHA-256 against the pins above) and packs with the plain
      `pack_gguf --model UD-IQ2_XXS` recipe
- [ ] `strata-dsv41 --pack-info` names the Engram type as Q5_K and lists every file
- [ ] the engine starts: no Engram refusal, the n-gram rows are read (the direct/unbuffered read
      path included)
- [ ] `tools/dsv41/smoke.py` answers (the text names Paris); one tool call round trip
- [ ] a 4,000- and an 8,000-token prompt run through the chunked path
- [ ] a few dozen turns at 16k context: answers look like the model (Engram rows feed the mix, so a
      wrong Q5_K dequant shows up as degraded text - compare against the same prompts on the
      offered UD-IQ1_M set)
- [ ] decode tokens/s recorded (nothing to compare against yet: no set like this has been run here)

Passed / failed / not run are kept apart in the task record, and no speed or quality claim before
these lines have numbers from the box that ran them. A second reason to keep the new branch
isolated: we merge `origin/main` into our main regularly, and a change reached only by a Q5_K
pack keeps that merge mechanical.

## Limits

- Only UD-IQ2_XXS benefits alone; UD-Q2_K_XL additionally hits the experts rule (companion issue).
- Unsloth may re-quantize the tables on any re-publish: the pins above are the bytes this finding
  is about.
