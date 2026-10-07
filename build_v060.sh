#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
ROOT="$HOME/mengmx-build"
SRC="$ROOT/llama.cpp"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$ROOT"

if [ ! -d "$SRC/.git" ]; then
  echo "==> first build: cloning llama.cpp v0.6.0"
  git clone --depth 1 --branch v0.6.0 https://github.com/ggml-org/llama.cpp.git "$SRC"
else
  echo "==> incremental build: reusing existing llama.cpp build"
fi

# The source tree contains generated experimental patches. Always restore the
# tracked v0.6.0 mmap source before applying the current patch.
cd "$SRC"
git restore --source=HEAD -- src/llama-mmap.cpp src/llama-mmap.h src/llama-model-loader.cpp src/llama-model.cpp

cp "$HERE/patch_paged_mmap.py" "$ROOT/patch_paged_mmap.py"
cd "$ROOT"
python patch_paged_mmap.py

cd "$SRC"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DGGML_OPENMP=OFF -DGGML_VULKAN=OFF -DGGML_OPENCL=OFF -DGGML_NATIVE=OFF
cmake --build build --target llama-cli -j2

echo
echo "===== BUILD OK ====="
echo "Binary: $SRC/build/bin/llama-cli"
