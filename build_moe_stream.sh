#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$HOME/mengmx-moe-build"
SRC="$ROOT/llama.cpp"
HERE="$(cd "$(dirname "$0")" && pwd)"

rm -rf "$ROOT"
mkdir -p "$ROOT"

echo "==> cloning llama.cpp PR #25294 head"
git clone --depth 1 --branch feat/moe-streaming-core https://github.com/freedomljc/llama.cpp.git "$SRC"

cd "$SRC"
echo "==> source:"
git rev-parse HEAD

cmake -S . -B build   -DCMAKE_BUILD_TYPE=Release   -DBUILD_SHARED_LIBS=OFF   -DGGML_OPENMP=OFF   -DGGML_VULKAN=OFF   -DGGML_OPENCL=OFF   -DGGML_NATIVE=OFF   -DLLAMA_CURL=OFF

cmake --build build --target llama-cli -j2

echo
echo "===== MOE STREAM BUILD OK ====="
echo "Binary: $SRC/build/bin/llama-cli"
echo "PR: https://github.com/ggml-org/llama.cpp/pull/25294"
