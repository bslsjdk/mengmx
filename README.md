# mengmx

Android llama.cpp 0.6.0 experimental lazy-mmap build.

仓库地址：https://github.com/bslsjdk/mengmx

## 一条命令构建

Termux 里直接复制：

```bash
pkg update -y
pkg install -y git cmake python
rm -rf "$HOME/mengmx"
git clone -b android-paged-mmap https://github.com/bslsjdk/mengmx.git "$HOME/mengmx"
cd "$HOME/mengmx"
bash build_v060.sh
```

编译完成后，二进制在：

```
$HOME/mengmx-build/llama.cpp/build/bin/llama-cli
```

## 运行 30B GGUF

先设置模型路径：

```bash
MODEL=/storage/emulated/0/qwen/qwen3-coder-30b-a3b-instruct-q4_k_m-00001-of-00004.gguf
```

然后运行实验版本：

```bash
$HOME/mengmx-build/llama.cpp/build/bin/llama-cli \
  -m "$MODEL" \
  --load-mode mmap \
  --lazy-mode on \
  --device none \
  -t 4 \
  -c 512 \
  -b 16 \
  -ub 16 \
  -n 16 \
  -st \
  -p "你好"
```

## 重要

这个版本是实验性的 Android mmap 修改版。目标是避免一次性申请整个 GGUF 的实际内存，并让模型按需从文件读取。

**4 GiB 是硬安全上限，不把超过 4 GiB 当作正常运行状态。**

目前修改尚未证明可以让 30B 在 Android 上稳定保持 3 GiB 左右工作集，因此不要把“目标”理解成已经验证成功。先用小规模参数测试。

