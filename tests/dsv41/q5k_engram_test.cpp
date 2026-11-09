// Strata. The Engram's Q5_K table row (src/dsv41/ds4_q5k.cuh's q5k_value) on the device against a CPU reference.
//
// What it is for: unsloth's UD-IQ2_XXS set of DeepSeek-V4.1-Flash-1017 holds its n-gram (Engram) table in ggml Q5_K, and the
// engine decodes a table row with ds4_q5k.cuh's q5k_value (fmt 4 of engram_dequant_kernel and engram_dequant_tok_kernel).
// A table row is one superblock (head_dim 256), read as `q5k_value(row + (e / 256) * kQ5kBytes, e % 256)` with the row at a
// stride of kQ5kBytes.  This test launches that same shared function that way and checks every value against a CPU decode
// of the same bytes, bit for bit: the scale/min packing (get_scale_min_k4: the 6-bit split at the sub-block 4 boundary), the
// transposed 5th bit (qh), the shared low nibbles (qs), the fp16 d and dmin, and the F32 order (d*sc)*q - dmin*m.  A wrong
// sub-block index or a nibble read from the wrong half shows as a bit mismatch, not a silent near-miss.
//
//   q5k_engram_test [--blocks N] [--seed S] [--file PATH --off START --rows R] [--device D]
//
// Without --file it builds N legal random superblocks (any bit pattern is one once the F16 scales are sane: d and dmin are
// drawn as finite fp16) and checks all of them.  With --file it also decodes R rows of R*176 real bytes read at START (the
// RTX server: point it at Unsloth's UD-IQ2_XXS shard that holds per_layer_token_embd.weight, with START its data offset), so
// the check runs on the model's own bytes, not only on synthetic ones.  On the pinned release that tensor is shard 6 all by
// itself, dims [256, 768022850], dtype Q5_K, tensor-info ending at byte 173 so its bytes start at offset 192:
//   ./q5k_engram_test --file DeepSeek-V4.1-Flash-1017-UD-IQ2_XXS-00006-of-00006.gguf --off 192 --rows 4096
// Prints one RESULT line and "SUMMARY ... fails=N"; the exit code is 1 when anything failed.
#include "../../src/dsv41/ds4_q5k.cuh"
#include "strata/dsv41/hip_or_cuda.h"   // HIP names on a HIP build, the CUDA runtime otherwise

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <string>
#include <vector>

namespace q5 = strata::ds4;

namespace {
[[noreturn]] void bail(int code) { std::printf("SUMMARY q5k_engram fails=1 (device error)\n"); std::fflush(stdout); std::exit(code); }
}
#define CK(x) do { hipError_t e_ = (x); if (e_ != hipSuccess) { std::printf("DEVICE ERROR %s at line %d: %s\n", hipGetErrorString(e_), __LINE__, #x); std::fflush(stdout); bail(3); } } while (0)

// a __global__ over the shared decoder, exactly the (block base, value index) contract engram_dequant_kernel uses:
// a thread a value, the block at q5::kQ5kBytes bytes apart.
__global__ void q5k_decode_kernel(const uint8_t* __restrict__ blocks, int nblocks, float* __restrict__ out) {
    const int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= nblocks * 256) return;
    out[idx] = q5::q5k_value(blocks + (size_t) (idx >> 8) * q5::kQ5kBytes, idx & 255);
}

// fp16 -> fp32, exact (finite inputs only; ggml's scales are finite).
float h2f(uint16_t u) {
    uint32_t s = (uint32_t) (u >> 15) << 31, e = (u >> 10) & 0x1f, m = u & 0x3ff, x;
    if (e == 0) {
        if (m == 0) x = s;
        else { int ee = -1; uint32_t mm = m; do { ++ee; mm <<= 1; } while ((mm & 0x400) == 0); x = s | (uint32_t) (127 - 15 - ee) << 23 | (mm & 0x3ff) << 13; }
    } else if (e == 31) x = s | 0x7f800000u | m << 13;
    else x = s | (e - 15 + 127) << 23 | m << 13;
    float f; std::memcpy(&f, &x, 4); return f;
}
// the CPU reference: ggml's get_scale_min_k4 + dequantize_row_q5_K arithmetic in F32.  Build with -ffp-contract=off so each
// multiply and the subtraction is a single rounded step (the same rounding the device's __fmul_rn / __fsub_rn give).
float host_q5k_value(const uint8_t* b, int i) {
    const int ib = i >> 5, l = i & 31;
    int sc, m;
    if (ib < 4) { sc = b[4 + ib] & 63; m = b[8 + ib] & 63; }
    else { sc = (b[8 + ib] & 0xF) | ((b[ib] >> 6) << 4); m = (b[8 + ib] >> 4) | ((b[4 + ib] >> 6) << 4); }
    const int sh = 4 * (ib & 1);
    const int q = ((b[48 + 32 * (ib >> 1) + l] >> sh) & 0xF) | (((b[16 + l] >> ib) & 1) << 4);
    const float d = h2f((uint16_t) (b[0] | (b[1] << 8)));
    const float dmin = h2f((uint16_t) (b[2] | (b[3] << 8)));
    const float dl = d * (float) sc, dm = dmin * (float) m;
    return dl * (float) q - dm;
}

uint32_t fbits(float f) { uint32_t u; std::memcpy(&u, &f, 4); return u; }

// a finite fp16 in a sane Engram scale range (positive, exponent 1..14 so d * 63 never overflows a float)
uint16_t rand_half(std::mt19937& rng) {
    std::uniform_int_distribution<int> expo(1, 14), mant(0, 1023);
    return (uint16_t) (expo(rng) << 10 | mant(rng));
}

// compare device bits against the host decode of the same bytes; returns the number of mismatches (prints the first few)
long compare(const std::vector<float>& dev, const std::vector<uint8_t>& blocks, int nblocks, const char* what) {
    long bad = 0;
    for (int idx = 0; idx < nblocks * 256; ++idx) {
        const uint8_t* blk = blocks.data() + (size_t) (idx >> 8) * q5::kQ5kBytes;
        const float ref = host_q5k_value(blk, idx & 255);
        if (fbits(ref) != fbits(dev[(size_t) idx])) {
            if (bad < 5)
                std::printf("  MISMATCH %s: block %d value %d (sub-block %d): device %08x host %08x\n", what, idx >> 8, idx & 255,
                            (idx & 255) >> 5, fbits(dev[(size_t) idx]), fbits(ref));
            ++bad;
        }
    }
    return bad;
}

int main(int argc, char** argv) {
    int nblocks = 4096, device = 0;
    long seed = 20261109;
    const char* file = nullptr;
    long off = 0;
    int rows = 0;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        auto next = [&](const char* d) -> long { return i + 1 < argc ? std::atol(argv[++i]) : std::atol(d); };
        if (a == "--blocks") nblocks = (int) next("4096");
        else if (a == "--seed") seed = next("0");
        else if (a == "--device") device = (int) next("0");
        else if (a == "--file") file = argv[++i];
        else if (a == "--off") off = next("0");
        else if (a == "--rows") rows = (int) next("0");
    }
    CK(hipSetDevice(device));

    std::mt19937 rng((uint32_t) seed);
    std::uniform_int_distribution<int> byte(0, 255);
    std::vector<uint8_t> blocks((size_t) nblocks * q5::kQ5kBytes);
    for (int b = 0; b < nblocks; ++b) {
        uint8_t* p = blocks.data() + (size_t) b * q5::kQ5kBytes;
        // random bytes over the whole superblock: that exercises every reader path (the get_scale_min_k4 split at the
        // sub-block-4 boundary, both nibble halves, the shared even/odd sub-block byte, the transposed 5th bit in qh) and
        // the scale / min / fp16 extremes.  The device and the CPU decode the SAME bytes, so an arbitrary pattern still
        // catches any divergence between the two implementations; matching ggml's layout on real bytes is covered by the
        // --file path below.  d (b[0..1]) and dmin (b[2..3]) are forced to finite positive fp16 so d * 63 stays a normal
        // float (a real Q5_K block's scales are finite).
        for (int k = 0; k < q5::kQ5kBytes; ++k) p[k] = (uint8_t) byte(rng);
        const uint16_t d = rand_half(rng), dmin = rand_half(rng);
        p[0] = (uint8_t) d; p[1] = (uint8_t) (d >> 8);
        p[2] = (uint8_t) dmin; p[3] = (uint8_t) (dmin >> 8);
    }

    // real bytes (optional): R rows of 176 read at off
    std::vector<uint8_t> real;
    if (file != nullptr && rows > 0) {
        std::FILE* f = std::fopen(file, "rb");
        if (f == nullptr) { std::perror(file); return 2; }
        if (std::fseek(f, off, SEEK_SET) != 0) { std::fprintf(stderr, "--off past the end\n"); std::fclose(f); return 2; }
        real.resize((size_t) rows * q5::kQ5kBytes);
        const size_t got = std::fread(real.data(), 1, real.size(), f);
        std::fclose(f);
        rows = (int) (got / q5::kQ5kBytes);
        real.resize((size_t) rows * q5::kQ5kBytes);
        if (rows <= 0) { std::fprintf(stderr, "no whole superblock read from %s at %ld\n", file, off); return 2; }
    }

    const int total_blocks = nblocks + rows;
    std::vector<uint8_t> all = blocks;
    all.insert(all.end(), real.begin(), real.end());

    uint8_t* d_in = nullptr;
    float* d_out = nullptr;
    CK(hipMalloc(&d_in, (size_t) total_blocks * q5::kQ5kBytes));
    CK(hipMalloc(&d_out, (size_t) total_blocks * 256 * sizeof(float)));
    CK(hipMemcpy(d_in, all.data(), (size_t) total_blocks * q5::kQ5kBytes, hipMemcpyHostToDevice));
    // Pre-fill the output with a NaN sentinel.  If the kernel never runs - the usual cause is --device pointing at a card
    // this binary was not built for ("no kernel image for device", which the hip shim cannot report here) - the outputs stay
    // NaN and we say so below, instead of printing one bogus mismatch per value.  A Q5_K decode of these inputs is always
    // finite (d and dmin are forced to finite fp16; a real table row's scales are finite), so a surviving NaN means unwritten.
    {
        std::vector<float> sent((size_t) total_blocks * 256, std::nanf(""));
        CK(hipMemcpy(d_out, sent.data(), sent.size() * sizeof(float), hipMemcpyHostToDevice));
    }
    const int tpb = 256;
    q5k_decode_kernel<<<(total_blocks * 256 + tpb - 1) / tpb, tpb>>>(d_in, total_blocks, d_out);
    CK(hipDeviceSynchronize());   // (the hip_or_cuda shim maps no hipGetLastError; a failed launch surfaces here)

    std::vector<float> dev((size_t) total_blocks * 256);
    CK(hipMemcpy(dev.data(), d_out, dev.size() * sizeof(float), hipMemcpyDeviceToHost));
    long nan = 0;
    for (float v : dev) if (v != v) ++nan;   // (NaN != NaN; a real decode never yields NaN, so these are unwritten slots)
    if (nan > (long) dev.size() / 4) {
        std::printf("DEVICE: the kernel left %ld of %zu outputs unwritten (NaN sentinel) - it produced no output.\n", nan, dev.size());
        std::printf("  Usually a device/arch mismatch: this binary's CUDA_ARCH must include the compute capability of --device.\n");
        std::printf("  Cards here: `nvidia-smi --query-gpu=index,compute_cap,name --format=csv`.  Run on the matching --device, or build for both: CUDA_ARCH=\"89 120\".\n");
        std::printf("SUMMARY q5k_engram kernel_did_not_run=1\n");
        std::fflush(stdout);
        return 3;
    }
    long bad = 0;
    bad += compare(dev, all, nblocks, "synthetic");
    if (rows > 0)
        bad += [&] { std::vector<float> d2(dev.begin() + (size_t) nblocks * 256, dev.end()); return compare(d2, real, rows, "real"); }();
    std::printf("RESULT q5k_engram blocks=%d (synthetic=%d real=%d) values=%ld fails=%ld\n", total_blocks, nblocks, rows,
                (long) total_blocks * 256, bad);
    std::printf("SUMMARY q5k_engram fails=%ld\n", bad);
    std::fflush(stdout);
    hipFree(d_in); hipFree(d_out);
    return bad != 0 ? 1 : 0;
}
