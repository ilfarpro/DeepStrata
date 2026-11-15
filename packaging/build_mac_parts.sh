#!/bin/bash
# packaging/build_mac_parts.sh - build the macOS half of a DeepStrata release.
#
# Run it on an Apple Silicon Mac (or a macOS CI runner). It writes a folder that
#   python packaging/release.py build --mac-parts <folder>
# imports on the release PC. It builds, but never signs, and it never sees the feed key:
#
#   * the Terminal/Control archive (strata-terminal-<version>-darwin-arm64.tar.gz,
#     with the pinned CPython, Node and the page inside);
#   * the Qwen Metal engine package (strata, strata-vision, the kernel sources the
#     engine compiles when it starts, packaging/package_engine.py --backend metal);
#   * the DeepSeek V4.1-Flash engine package (strata-dsv41, feed member `dsv41`)
#     through packaging/mac/build_dsv41_engine.sh.
#
# The result is a self-contained folder (the shape windows-parts.json has):
#
#   mac-parts.json                   commit, versions, toolchain, hashes
#   terminal/*.tar.gz                the Terminal archive
#   engines/<name>/                  the Qwen engine package (unsigned manifest)
#   dsv41/<name>/                    the DeepSeek engine package (unsigned manifest)
#   smoke/smoke-terminal.json        the Terminal smoke report (run on this Mac)
#
# The release PC signs both engine manifests with the feed key and packs the archives,
# so this machine needs no secret. Nothing here is uploaded. The Terminal smoke test
# runs the mock engine: no model, no GPU.
#
# Usage:  bash packaging/build_mac_parts.sh [options]
#   --out DIR        where the parts folder is written (default: <repo>/build-mac-parts;
#                    a previous folder is refused, pass --clean to replace it)
#   --commit SHA     the commit the parts must come from (default: HEAD; the release PC
#                    refuses parts built from another commit)
#   --version V      the component version (default: CMakeLists.txt's project VERSION)
#   --only LIST      build only these parts: terminal, qwen, dsv41 (comma separated)
#   --skip-host      reuse an existing apps/host/dist instead of running npm ci / npm run build
#   --skip-smoke     do not run the Terminal smoke test (the release tool then refuses these parts)
#   --allow-dirty    build from a dirty tree for a local test (the release tool refuses such parts)
#   --clean          remove a previous --out folder first
#
# Needs: Xcode Command Line Tools, cmake, ninja, Node 24.12+ and npm (for the page),
# Python 3.10+, git. The Metal engine also needs Apple's metal-cpp and the pinned
# llama.cpp: METAL_CPP_DIR and STRATA_GGML_DIR name them (defaults: ~/strata-mac/metal-cpp
# and third_party/llama.cpp, then ~/strata-mac/llamacpp), the same as mac/build_dsv41_engine.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="$ROOT/build-mac-parts" commit="" version="" only="" skip_host="" skip_smoke="" allow_dirty="" clean=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out) out="$2"; shift 2 ;;
    --commit) commit="$2"; shift 2 ;;
    --version) version="$2"; shift 2 ;;
    --only) only="$2"; shift 2 ;;
    --skip-host) skip_host=1; shift ;;
    --skip-smoke) skip_smoke=1; shift ;;
    --allow-dirty) allow_dirty=1; shift ;;
    --clean) clean=1; shift ;;
    -h|--help) sed -n '2,45p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument $1 (--help for the list)" >&2; exit 2 ;;
  esac
done
say() { printf '\n== %s\n' "$*"; }
fail() { printf 'build_mac_parts: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || fail "this builds on an Apple Silicon Mac"
for tool in git python3; do command -v "$tool" >/dev/null || fail "$tool is missing"; done

# ----------------------------------------------------------------- the commit and the versions
head="$(git -C "$ROOT" rev-parse HEAD)" || fail "$ROOT is not a git checkout"
[ -z "$commit" ] || [ "$commit" = "$head" ] || fail "--commit $commit is not this checkout's HEAD (${head:0:12})"
dirty=false
[ -z "$(git -C "$ROOT" status --porcelain)" ] || dirty=true
if [ "$dirty" = true ] && [ -z "$allow_dirty" ]; then
  fail "the tree has uncommitted changes. Commit them, or pass --allow-dirty for a local test build (the release tool refuses such parts)"
fi
component="$version"
[ -n "$component" ] || component="$(sed -nE 's/.*project *\([^)]*VERSION +([0-9]+(\.[0-9]+)+).*/\1/p' "$ROOT/CMakeLists.txt" | head -1)"
[ -n "$component" ] || fail "no component version: CMakeLists.txt has no project VERSION, pass --version"
product="$(sed -nE 's/PRODUCT_VERSION *= *"([0-9]+(\.[0-9]+)+)".*/\1/p' "$ROOT/strata_cli/__init__.py" | head -1)"
[ -n "$product" ] || fail "strata_cli/__init__.py has no PRODUCT_VERSION"
host_version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version",""))' "$ROOT/apps/host/package.json")"
[ -n "$host_version" ] || fail "apps/host/package.json has no version"

wanted="${only//,/ }"
[ -n "$wanted" ] || wanted="terminal qwen dsv41"
for name in $wanted; do
  case "$name" in terminal|qwen|dsv41) ;; *) fail "--only knows terminal, qwen, dsv41 (got $name)" ;; esac
done
want() { case " $wanted " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

# ----------------------------------------------------------------- the tools
need() { command -v "$1" >/dev/null || fail "$1 is missing$2"; }
node_version=""
if want terminal; then
  need npm " (the page build needs it, or pass --skip-host with apps/host/dist already built)"
  need node " (the page build needs it, or pass --skip-host)"
  node_version="$(node --version)"
  node_major="${node_version#v}"; node_major="${node_major%%.*}"
  node_minor="$(node -p 'Number(process.versions.node.split(".")[1])')"
  [ "$node_major" -ge 24 ] && { [ "$node_major" -gt 24 ] || [ "$node_minor" -ge 12 ]; } \
    || fail "Node $node_version is too old: the App Host needs 24.12 or newer"
fi
if want qwen || want dsv41; then
  need cmake " (the Metal engine build)"; need ninja " (the Metal engine build)"
fi
metal_cpp="${METAL_CPP_DIR:-$HOME/strata-mac/metal-cpp}"
ggml="${STRATA_GGML_DIR:-}"
[ -n "$ggml" ] || { [ -f "$ROOT/third_party/llama.cpp/CMakeLists.txt" ] && ggml="$ROOT/third_party/llama.cpp"; }
[ -n "$ggml" ] || [ ! -d "$HOME/strata-mac/llamacpp" ] || ggml="$HOME/strata-mac/llamacpp"
jobs_count="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"

# ----------------------------------------------------------------- the parts folder
[ -z "$clean" ] || rm -rf "$out"
[ ! -e "$out" ] || fail "$out already exists; pass --clean to replace it"
mkdir -p "$out/terminal" "$out/smoke"

build_id="${head:0:12}"
terminal_rel=""; smoke_rel=""; qwen_rel=""; dsv41_rel=""   # what the manifest names, set below

# ----------------------------------------------------------------- the page
if want terminal && [ -z "$skip_host" ]; then
  say "building the App Host (the browser page)"
  ( cd "$ROOT/apps/host"
    [ -d node_modules ] || npm ci --no-audit --no-fund
    npm run build )
  [ -f "$ROOT/apps/host/dist/index.html" ] || fail "apps/host/dist/index.html is still missing after npm run build"
fi

# ----------------------------------------------------------------- the Terminal/Control archive
if want terminal; then
  say "building the Terminal archive (pinned Python, Node and page inside)"
  python3 "$ROOT/packaging/package_terminal.py" --out "$out/terminal" --target darwin-arm64 \
    --version "$component" --commit "$head"
  archive="$out/terminal/strata-terminal-$component-darwin-arm64.tar.gz"
  [ -f "$archive" ] || fail "package_terminal.py finished but $archive is missing"
  terminal_rel="terminal/$(basename "$archive")"
  say "smoke-testing the Terminal archive (bundled Python, Node, page, mock server)"
  if [ -n "$skip_smoke" ]; then
    echo "WARNING: smoke skipped: the release tool will refuse these parts (--skip-smoke)" >&2
  else
    python3 "$ROOT/packaging/smoke_terminal.py" "$archive" --json "$out/smoke/smoke-terminal.json"
    python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("ok") else 1)' \
      "$out/smoke/smoke-terminal.json" || fail "the Terminal smoke test failed; see $out/smoke/smoke-terminal.json"
    smoke_rel="smoke/smoke-terminal.json"
  fi
fi

# ----------------------------------------------------------------- the Metal build inputs
prepare_metal() {
  [ -d "$metal_cpp" ] || fail "metal-cpp is missing at $metal_cpp (pass METAL_CPP_DIR; the mac-parts workflow downloads it)"
  [ -n "$ggml" ] && [ -f "$ggml/CMakeLists.txt" ] || fail "the pinned llama.cpp is missing (pass STRATA_GGML_DIR; the mac-parts workflow downloads it)"
}

# ----------------------------------------------------------------- the Qwen Metal engine
if want qwen; then
  prepare_metal
  say "building the Qwen Metal engine (strata)"
  cmake -S "$ROOT/metal" -B "$out/build-strata" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DMETAL_CPP_DIR="$metal_cpp" -DSTRATA_GGML_DIR="$ggml" -DSTRATA_BUILD_ID="$build_id"
  cmake --build "$out/build-strata" --target strata
  exe="$out/build-strata/strata"
  [ -x "$exe" ] || fail "the Metal build produced no $exe"
  say "building the picture reader (strata-vision)"
  bash "$ROOT/packaging/build_vision_mac.sh" --llama "$ggml" --out "$out/stage" --build "$out/build-vision" --jobs "$jobs_count"
  say "staging the engine package: the engine, the reader, the kernel sources it compiles at start, the shipped tables"
  # the layout metal/src/resource_path.h and ENGINE-COMPONENT.md describe for a moved package
  stage="$out/stage"
  mkdir -p "$stage/metal/kernels" "$stage/metal/engine-msl" "$stage/metal/data"
  cp "$exe" "$stage/strata"; chmod +x "$stage/strata"
  cp "$ROOT"/metal/kernels/*.metal "$stage/metal/kernels/"
  cp "$ROOT"/metal/src/engine/msl/*.metal "$stage/metal/engine-msl/"
  cp "$ROOT/third_party/ggml/ggml-common.h" "$stage/metal/"
  cp "$ROOT/data/expert-profile.bin" "$stage/metal/data/expert-profile.bin"
  cp "$ROOT/data/draft_vocab.bin" "$stage/metal/data/draft_vocab.bin"
  if [ -f "$ROOT/LICENSE" ]; then cp "$ROOT/LICENSE" "$stage/LICENSE"; fi
  say "packaging the Qwen engine (unsigned: the release PC signs it)"
  python3 "$ROOT/packaging/package_engine.py" --src "$stage" --backend metal --version "$component" \
    --platform macos-arm64 --arch apple-gpu --entrypoint strata --build-id "$build_id" \
    --include 'metal/**' --out "$out/engines"
  qwen_folder="$out/engines/$component-macos-arm64-metal"
  python3 "$ROOT/packaging/package_engine.py" --verify "$qwen_folder"
  qwen_rel="engines/$(basename "$qwen_folder")"
fi

# ----------------------------------------------------------------- the DeepSeek engine
if want dsv41; then
  prepare_metal
  say "building and packaging strata-dsv41 (packaging/mac/build_dsv41_engine.sh)"
  METAL_CPP_DIR="$metal_cpp" STRATA_GGML_DIR="$ggml" \
    bash "$ROOT/packaging/mac/build_dsv41_engine.sh" --version "$component" \
      --build "$out/build-dsv41" --out "$out/dsv41" --build-id "$build_id"
  dsv41_folder="$out/dsv41/$component-macos-arm64-metal"
  [ -d "$dsv41_folder" ] || fail "build_dsv41_engine.sh finished but $dsv41_folder is missing"
  dsv41_rel="dsv41/$(basename "$dsv41_folder")"
fi

# ----------------------------------------------------------------- the hand-off manifest
say "writing mac-parts.json (the hand-off manifest release.py reads)"
# The manifest is written last, and by a script of its own so the schema it writes is tested on any machine
# (tests/tools/test_release_tool.py writes one with it and reads it back with release.py).
manifest_args=(--out "$out" --commit "$head" --component "$component" --product "$product" --host "$host_version")
[ -z "$terminal_rel" ] || manifest_args+=(--terminal "$terminal_rel")
[ -z "$smoke_rel" ] || manifest_args+=(--smoke "$smoke_rel")
[ -z "$qwen_rel" ] || manifest_args+=(--engine "qwen=$qwen_rel")
[ -z "$dsv41_rel" ] || manifest_args+=(--engine "dsv41=$dsv41_rel")
manifest_args+=(--toolchain "clang=$(clang --version | head -1)")
manifest_args+=(--toolchain "macos=$(sw_vers -productVersion 2>/dev/null || echo unknown)")
manifest_args+=(--toolchain "cmake=$(cmake --version 2>/dev/null | head -1)")
manifest_args+=(--toolchain "git=$(git --version | head -1)")
[ -z "$node_version" ] || manifest_args+=(--toolchain "node=$node_version")
[ "$dirty" = false ] || manifest_args+=(--dirty)
python3 "$ROOT/packaging/mac/parts_manifest.py" ${manifest_args[@]+"${manifest_args[@]}"} || fail "the manifest was not written"

say "done: $out"
echo "next, on the release PC:"
echo "  python packaging/release.py build --mac-parts $out"
