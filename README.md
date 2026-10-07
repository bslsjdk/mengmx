# mengmx

Android llama.cpp 0.6.0 experimental sparse lazy-mmap build.

This repository contains an automatic patcher. It downloads upstream llama.cpp v0.6.0, applies the Android sparse lazy-mmap changes, and builds llama-cli.

Goal:
- avoid a single multi-GB GGUF shard mmap reservation on Android
- map tensors as independent file fragments
- keep the model file-backed and demand-paged
- target a ~3 GiB working set, with 4 GiB treated as the hard safety ceiling

Build:

```bash
git clone -b android-paged-mmap https://github.com/bslsjdk/mengmx.git
cd mengmx
bash build_v060.sh
```

Run:

```MODEL=/storage/emulated/0/qwen/qwen3-coder-30b-a3b-instruct-q4_k_m-00001-of-00004.gguf
$HOME/mengmx-build/llama.cpp/build/bin/llama-cli -m "$MODEL" --load-mode none --lazy-mode on --device none -t 4 -c 512 -b 16 -ub 16 -n 16 -st -p "你好"```

Experimental Android memory-management change.