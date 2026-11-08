// src/dsv41/ds4_q5k.cuh - one value of a ggml Q5_K superblock (256 values, 176 bytes: d F16, dmin F16, 12 packed
// scale/min bytes [get_scale_min_k4], 32 qh bytes carrying the 5th bit transposed (byte = place in the 32-value
// sub-block, bit = the sub-block index), 128 qs bytes with the low nibbles), as ggml's dequantize_row_q5_K computes it:
// (d * scale) * q - dmin * min, the two products in that order in F32 - the same bits (the Engram tables unsloth's
// UD-IQ2_XXS set holds in Q5_K, whose head_dim of 256 makes a table row one whole superblock).
//
// The arithmetic order matches this engine's Q5_K GEMV (ds4_iq.cu) and its Metal twin (metal/src/engine/msl/ds4_init.metal
// ds4_dq_q5k), so the Engram dequant decodes to the same values this engine already produces for Q5_K dense weights.
#pragma once

#include <cuda_fp16.h>
#include <cstdint>

namespace strata::ds4 {

constexpr int kQ5kBytes = 176;   // (ggml block_q5_K: 2 + 2 + 12 + 32 + 128 = 176 bytes for 256 values)

__device__ __forceinline__ float q5k_value(const uint8_t* __restrict__ b, int i) {
    const int ib = i >> 5, l = i & 31;   // (ggml's loop: the 32-value sub-block, the value within it)
    int sc, m;                            // (ggml's get_scale_min_k4 over the 12-byte scales region at b + 4)
    if (ib < 4) {
        sc = b[4 + ib] & 63;
        m = b[8 + ib] & 63;
    } else {
        sc = (b[8 + ib] & 0xF) | ((b[ib] >> 6) << 4);
        m = (b[8 + ib] >> 4) | ((b[4 + ib] >> 6) << 4);
    }
    const int sh = 4 * (ib & 1);
    const int q = ((b[48 + 32 * (ib >> 1) + l] >> sh) & 0xF) | (((b[16 + l] >> ib) & 1) << 4);
    const float d = __half2float(__ushort_as_half((unsigned short) (b[0] | (b[1] << 8))));
    const float dmin = __half2float(__ushort_as_half((unsigned short) (b[2] | (b[3] << 8))));
    const float dl = __fmul_rn(d, (float) sc), dm = __fmul_rn(dmin, (float) m);
    return __fsub_rn(__fmul_rn(dl, (float) q), dm);
}

}  // namespace strata::ds4
