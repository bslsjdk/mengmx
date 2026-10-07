#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
ROOT="$HOME/mengmx-build"
SRC="$ROOT/llama.cpp"

rm -rf "$ROOT"
mkdir -p "$ROOT"

echo "==> cloning llama.cpp 0.6.0"
git clone --depth 1 --branch b0.6.0 https://github.com/ggml-org/llama.cpp.git "$SRC"

cp "$(cd "$(dirname "$0")" && pwd)/patch_paged_mmap.py" "$ROOT/patch_paged_mmap.py"

cd "$ROOT"
python patch_paged_mmap.py

cd "$SRC"
cmake -S . -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_OPENMP=OFF \
  -DGGML_VULKAN=OFF \
  -DGGML_OPENCL=OFF \
  -DGGML_NATIVE=OFF

cmake --build build --target llama-cli -j2

echo
echo "BUILD OK"
echo "Binary: $SRC/build/bin/llama-cli"
echo
echo "Run with:"
echo 'export LLAMA_PAGED_LAZY=1'
echo 'MODEL=/storage/emulated/0/qwen/qwen3-coder-30b-a3b-instruct-q4_k_m-00001-of-00004.gguf'
echo '$HOME/mengmx-build/llama.cpp/build/bin/llama-cli -m "$MODEL" --load-mode none --lazy-mode on --device none -t 4 -c 512 -b 16 -ub 16 -n 16 -st -p "你好"'
