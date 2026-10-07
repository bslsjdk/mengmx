#!/data/data/com.termux/files/usr/bin/bash
set -u
PID="${1:?usage: $0 PID [seconds]}"
SEC="${2:-300}"
BEST=0
END=$(($(date +%s)+SEC))
while kill -0 "$PID" 2>/dev/null; do
  HWM=$(awk '/^VmHWM:/ {print $2}' "/proc/$PID/status" 2>/dev/null || echo 0)
  PSS=$(awk '/^Pss:/ {sum+=$2} END {print sum+0}' "/proc/$PID/smaps_rollup" 2>/dev/null || echo 0)
  [ "$HWM" -gt "$BEST" ] && BEST="$HWM"
  printf 'VmHWM=%s KiB (%.1f MiB) PSS=%s KiB (%.1f MiB)\n' "$HWM" "$((HWM/1024))" "$PSS" "$((PSS/1024))"
  [ "$(date +%s)" -ge "$END" ] && break
  sleep 1
done
echo "PEAK_VmHWM=$BEST KiB ($((BEST/1024)) MiB)"
if [ "$BEST" -gt 4194304 ]; then
  echo "HARD_LIMIT=FAIL (>4 GiB)"
else
  echo "HARD_LIMIT=PASS (<=4 GiB)"
fi
