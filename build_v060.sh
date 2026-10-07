#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
ROOT="$HOME/mengmx-build"
SRC="$ROOT/llama.cpp"
HERE="$(cd "$(dirname "$0")" && pwd)"
rm -rf "$ROOT"
mkdir -p "$ROOT"
echo "==> cloning llama.cpp v0.6.0"
git clone --depth 1 --branch v0.6.0 https://github.com/ggml-org/llama.cpp.git "$SRC"
cp "$HERE/patch_paged_mmap.py" "$ROOT/patch_paged_mmap.py"
cd "$ROOT"
python patch_paged_mmap.py
cd "$SRC"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DGGML_OPENMP=OFF -DGGML_VULKAN=OFF -DGGML_OPENCL=OFF -DGGML_NATIVE=OFF
cmake --build build --target llama-cli -j2
echo
echo "===== BUILD OK ====="
echo "Binary: $SRC/build/bin/llama-cli"
