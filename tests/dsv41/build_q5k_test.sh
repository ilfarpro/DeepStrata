#!/bin/bash
# Builds tests/dsv41/q5k_engram_test.cpp by hand (no CMake): nvcc (CUDA) or the ROCm clang (HIP).  It checks the Engram's
# Q5_K table row (src/dsv41/ds4_q5k.cuh's q5k_value, Engram fmt 4) on the device against a CPU decode, bit for bit - the
# decoder unsloth's UD-IQ2_XXS n-gram table runs on.  One translation unit (ds4_q5k.cuh is header-only).
#   build_q5k_test.sh cuda <outdir>   env: CUDA_ARCH=89 (an sm_ number, or several space-separated - "89 120" - for one
#                                      binary that runs on every card listed; use that on a box with mixed GPUs), NVCC=nvcc
#   build_q5k_test.sh hip  <outdir>   env: HIP_ROOT=<_rocm_sdk_devel>, HIP_ARCH=gfx1201
# SRC (the tree) defaults to the repo this script is in.  Then:
#   <outdir>/q5k_engram_test                                   # random blocks
#   <outdir>/q5k_engram_test --file <shard6.gguf> --off <data-start> --rows 4096   # the model's own Q5_K bytes
set -u
MODE=${1:?cuda|hip}; OUT=${2:?outdir}
SRC=${SRC:-$(cd "$(dirname "$0")/../.." && pwd)}
mkdir -p "$OUT"
INC="-I$SRC/include -I$SRC/src/dsv41"
F="tests/dsv41/q5k_engram_test.cpp"

if [ "$MODE" = cuda ]; then
    ARCHES=${CUDA_ARCH:?CUDA_ARCH, e.g. 89, or several: "89 120"}
    NVCC=${NVCC:-nvcc}
    CODEGEN=""; SHIM=0
    for a in $ARCHES; do
        CODEGEN="$CODEGEN --generate-code=arch=compute_$a,code=sm_$a"
        if [ "$a" -lt 70 ] 2>/dev/null; then SHIM=1; fi
    done
    [ -z "$CODEGEN" ] && { echo "CUDA_ARCH is empty" >&2; exit 1; }
    FL="-O2 -std=c++20$CODEGEN -Xcompiler -ffp-contract=off -Xcompiler -fPIC $INC"
    if [ "$SHIM" -eq 1 ]; then FL="$FL -include $SRC/tests/dsv41/dq_pascal_shim.h"; fi
    ( cd "$SRC" && $NVCC $FL -x cu -c "$F" -o "$OUT/q5k.o" ) || exit 1
    $NVCC -o "$OUT/q5k_engram_test" "$OUT/q5k.o" || exit 1
else
    HP=${HIP_ROOT:?HIP_ROOT}
    ARCH=${HIP_ARCH:?HIP_ARCH}
    CXX=${HIPCXX:-$HP/llvm/bin/clang++}
    export HIP_PLATFORM=amd HIP_COMPILER=clang HIP_RUNTIME=rocclr ROCM_PATH=$HP HIP_PATH=$HP
    FL="--rocm-path=$HP --rocm-device-lib-path=$HP/amdgcn/bitcode -O2 -std=c++20 --offload-arch=$ARCH -fPIE -ffp-contract=off"
    FL="$FL -include $SRC/include/strata/dsv41/hip_extra.h -include $SRC/include/strata/hip_compat/cuda_runtime.h"
    FL="$FL -DSTRATA_USE_HIP=1 -D__HIP_PLATFORM_AMD__=1 -D__HIP_ROCclr__=1 -isystem $HP/include $INC -I$SRC/include/strata/hip_compat"
    ( cd "$SRC" && $CXX $FL -x hip -c "$F" -o "$OUT/q5k.o" ) || exit 1
    $CXX --hip-link --rtlib=compiler-rt -unwindlib=libgcc --offload-arch=$ARCH -o "$OUT/q5k_engram_test" "$OUT/q5k.o" -L$HP/lib -lamdhip64 -Wl,-rpath,$HP/lib || exit 1
fi
echo "built $OUT/q5k_engram_test"
