# mengmx · MoE Streaming validation

这是独立于 `android-paged-mmap` 的验证分支，直接构建 llama.cpp PR #25294 的 head：
`freedomljc/llama.cpp:feat/moe-streaming-core`。

本分支**不套用 V5/V3 mmap patch**。这是故意的：PR #25294 的 streaming 路径会自动禁用 mmap，否则无法判断真正的 expert streaming 是否有效。

## 构建

Termux：

```bash
cd "$HOME"
rm -rf "$HOME/mengmx"
git clone -b moe-stream-android https://github.com/bslsjdk/mengmx.git "$HOME/mengmx"
cd "$HOME/mengmx"
bash build_moe_stream.sh
```

## 模型

第一轮不要改模型文件。把四个 GGUF shard 放到 `/data/local/tmp/` 后，以第 1 shard 作为 `-m` 参数，llama.cpp 会自动发现其余 shard。

## 运行

```bash
BIN="$HOME/mengmx-moe-build/llama.cpp/build/bin/llama-cli"
MODEL="/data/local/tmp/qwen3-coder-30b-a3b-instruct-q4_k_m-00001-of-00004.gguf"

"$BIN" \
  -m "$MODEL" \
  --moe-stream-cache 2048 \
  --moe-stream-io-threads 2 \
  --moe-stream-direct \
  -t 4 \
  -c 2048 \
  -b 16 \
  -ub 16 \
  -n 256 \
  -st \
  -p "你好，请简单介绍一下你自己。"
```

第一轮不启用 `--overlap`。

## 峰值内存

运行时另开一个 Termux 窗口，先找到 PID：

```bash
pgrep -f llama-cli
```

然后：

```bash
cd "$HOME/mengmx"
bash monitor_vmhwm.sh PID 600
```

判定标准：

- `VmHWM > 4194304 KiB`：失败
- `VmHWM <= 4194304 KiB`：通过内存硬上限
- 同时记录程序输出中的 `moe-stream:` 统计，确认 cache hit、flash/token、I/O、`o_direct`。

注意：`VmHWM` 是进程级 resident high-water mark。不要只看某一时刻的 `dumpsys meminfo` 快照。
