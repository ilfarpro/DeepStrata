# [Engine] Let a low-bit layer keep an MXFP4 down matrix (opens Unsloth's four 3-bit sets)

> **Historical design note (2026-11-07).** The decision fell on "support", and it shipped in engine
> 0.1.60 (release 0.2.12.2) as `gg::expert_shape_ok`: a layer may keep an MXFP4 `down` beside gate
> and up of one low-bit type, and every other mix is still refused. The code quotes and line
> numbers below describe the tree of that day. What is still open for the sets is in
> [README.md](README.md).

## The problem and the result we want

Unsloth's 3-bit Dynamic sets keep DeepSeek's MXFP4 for the most sensitive expert layers and drop
the rest to 2- and 3-bit forms - and in several layers the choice falls **between the three
matrices**: gate and up end up an i-quant while down stays MXFP4. The engine's layer loader
refuses exactly that shape, so none of these files loads:

```cpp
// src/dsv41/ds4_model.cu:2416-2424
l.lay.fmt = gg::kFmtMXFP4;
if (!gg::mx_experts(l.lay) && (gg::expert_type(l.lay, 0) != gg::expert_type(l.lay, 1) ||
                               gg::expert_type(l.lay, 0) == gg::kTypeMXFP4 ||
                               gg::expert_type(l.lay, 2) == gg::kTypeMXFP4)) {
    err = P + ": gate / up / down of types " + gg::type_name(l.lay.K[0]) + " / " +
          gg::type_name(l.lay.K[1]) + " / " + gg::type_name(l.lay.K[2]) +
          " (gate and up must match; MXFP4 does not mix with the others)";
```

with `mx_experts` requiring all three roles to be MXFP4 (`src/dsv41/ds4_gguf.cuh:45-47`):

```cpp
inline bool mx_experts(const x3::ExpertLayout& l) {
    return expert_type(l, 0) == kTypeMXFP4 && expert_type(l, 1) == kTypeMXFP4 && expert_type(l, 2) == kTypeMXFP4;
}
```

`expert_type` treats K[r] == 0 as MXFP4, so any MXFP4 role in a layer that is not all-MXFP4 is an
error. The header comment states this as a rule, not an oversight (`ds4_gguf.cuh`, above
`mx_experts`): roles within one layer may differ (down may be another type), but "MXFP4 and the
low-bit kinds do not mix". The rule is in the source since the first engine import (`bbd0a2c6`,
Project Maya's v1.1.17) - every shipped engine carries it.

Mixing two low-bit kinds inside one layer is already allowed and ships today (UD-IQ1_S/M run with
IQ1_S gate/up and IQ2_XXS or Q8-family downs; the guard only rejects gate != up and any half-MXFP4
layer). The result we want: a decision - either support an MXFP4 `down` beside low-bit gate/up, or
keep the rule and say so where people will look for these files. If support: a layer whose gate
and up are the same low-bit type and whose down is MXFP4 should load and answer.

## The sets and the exact layers (read from the shards' tensor headers, unconfirmed static analysis)

Unsloth's repository at the pinned revision `8a6ec9cf118e785a362ea94ee123c61c70553be6`, live-checked
2026-11-07 (headers via ranged GET of the first 3 MB of each shard; the byte sums below
cross-check with the file sizes to under 0.1 GB). Each full MXFP4 role matrix is 4,529,848,320
values (2,406,481,920 bytes); a blocked layer's MXFP4 `down` has the same shape as its other roles.

| set | download | routed experts | blocked layers (gate/up -> down MXFP4) | n-gram table |
|---|---:|---|---|---|
| UD-IQ3_XXS | 394.7 GB | 224.3 GB (14.6 MB a piece) | 30 of 40: layers 0-9, 15-34 | Q6_K 161.3 GB, readable |
| UD-IQ3_S | 423.6 GB | 253.4 GB (16.5 MB) | 18 of 40: layers 0-9, 20-24, 35-37 | Q6_K 161.3 GB, readable |
| UD-Q3_K_XL | 485.1 GB | 271.6 GB (17.7 MB) | 4 of 40: layer 0 (IQ3_XXS), layers 2-4 (IQ2_S) | MXFP8 202.8 GB, readable |
| UD-Q2_K_XL | 334.8 GB | 191.1 GB (12.4 MB) | 7 of 40: layers 0-1, 15-19 | Q5_K 135.2 GB, **also needs the companion Q5_K issue** |

Layer composition, gate/up -> down (x layers):

- **UD-IQ3_XXS**: IQ3_XXS -> MXFP4 x10, Q3_K -> MXFP4 x10, IQ2_S -> MXFP4 x5, IQ2_XXS -> MXFP4 x3,
  IQ2_XS -> MXFP4 x2 (the 30 blocked); IQ2_S -> Q3_K x5, IQ3_XXS -> IQ3_S x4, Q3_K -> Q3_K x1 (allowed today)
- **UD-IQ3_S**: MXFP4 -> MXFP4 x15 (loads today's way); Q3_K -> MXFP4 x8, IQ2_S -> MXFP4 x5,
  IQ3_XXS -> MXFP4 x5 (the 18 blocked); IQ3_XXS -> Q3_K x5, Q3_K -> Q3_K x1, Q3_K -> IQ3_S x1 (allowed)
- **UD-Q3_K_XL**: full MXFP4 x29; IQ3_XXS -> MXFP4 x1, IQ2_S -> MXFP4 x3 (the 4 blocked);
  IQ3_S -> Q3_K x5, Q3_K -> Q3_K x1, IQ3_XXS -> IQ3_S x1 (allowed)
- **UD-Q2_K_XL**: IQ2_XXS -> IQ3_XXS x10, IQ3_XXS -> IQ3_XXS x10, IQ3_XXS -> IQ3_S x5,
  IQ2_S -> IQ3_S x5, IQ2_XXS -> IQ2_XXS x3 (allowed); IQ2_S -> MXFP4 x5, IQ2_XS -> MXFP4 x2 (the 7 blocked)

The low-bit kinds that are already on both lanes: IQ1_S, IQ2_XXS, IQ2_XS, IQ2_S, IQ3_XXS, IQ3_S,
Q2_K, Q3_K (`ds4_iq.cu` kernels, `type_row_bytes` / `type_on_device`, the CPU rows); MXFP4 down
kernels exist for whole-MXFP4 layers (`cpu_experts_mx`, the mx branch of the moe kernels).

## What support would touch (reading the code - no patch proposed)

Today every path branches on the layer-wide `mx_experts` flag, not per role:
`ds4_gguf.cu:1468, 1509-1510, 1568, 1583, 1658, 1719` (activation quant, gate/up, down, combine)
and the CPU lane at `ds4_model.cu:2472`. A mixed layer needs gate/up through the ds4_iq kernels and
down through the MXFP4 path within one layer - per-role dispatch in both lanes and both device
backends, plus the guard above. The activation quant differs too (`iq_act_quant` vs `q8_quant`), so
the honest version is per-layer selection of a small strategy object, not a condition inside one
kernel.

An alternative worth deciding before code: keep the engine as it is and make the refusal kind and
early - pack-time detection with the layer list and a doc line (`docs/DEEPSEEK41.md` says today
what refuses and why). That is a decision about scope, not a bug: the current message is correct,
and nothing today is broken. In our fork this decision is ours to take.

## Validation checklist (if support is built)

Same boxes as the Q5_K issue - CUDA (RTX 5070) and AMD gfx1200 (R9700):

- [ ] builds on every backend a touched file compiles into (CUDA, HIP; SYCL and Metal where they
      reach this code)
- [ ] UD-Q3_K_XL packs and loads (its table is the readable MXFP8 form, so it isolates this rule:
      4 mixed layers, 29 whole-MXFP4 layers, everything else low-bit)
- [ ] then UD-IQ3_XXS and UD-IQ3_S (Q6_K tables), and UD-Q2_K_XL only with the Q5_K reader too
- [ ] a mixed layer is exercised: with these 4-to-30-layer mixes every card split hits one; the
      CPU lane likewise (a machine whose RAM cannot hold all the experts)
- [ ] `tools/dsv41/smoke.py` (names Paris), a tool call round trip, 4k/8k prompts, a few dozen
      turns; answers spot-compared against the offered UD-IQ1_M set on the same prompts
- [ ] numbers recorded as measured, none predicted

## Limits

- Static analysis: we made no pack of any of these five sets and ran no GPU here (the dev box has
  no supported card). Every load-failure statement is a reading of the quoted rules, not an
  observed run.
- Unsloth may change the files on any re-publish; the findings are for the pinned revision above.
- We checked the 9 set folders the repository carries today (UD-Q8_K_XL, UD-Q4_K_XL, UD-IQ1_S,
  UD-IQ1_M offered; the five in these issues not).
