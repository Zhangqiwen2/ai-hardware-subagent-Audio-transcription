#!/bin/bash
# 生成不同时长的测试音频（用于 benchmark）
# 从现有的 98 秒音频生成 30s / 2m / 5m / 10m 版本
# 依赖: ffmpeg

set -e

SRC="https://asr-test-wav.obs.cn-southwest-2.myhuaweicloud.com/lfasr_%E6%B6%89%E6%94%BF.wav"
OUT_DIR="./test_audio"
mkdir -p "$OUT_DIR"

# 先下载源文件（如果还没有）
if [ ! -f "$OUT_DIR/src.wav" ]; then
    echo ">>> 下载源音频..."
    curl -sL "$SRC" -o "$OUT_DIR/src.wav"
fi

# 获取源音频时长
DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT_DIR/src.wav" | cut -d. -f1)
echo "源音频时长: ${DUR}s"

# 生成各时长版本
generate() {
    local label="$1" target_sec="$2" out="$3"
    echo ">>> 生成 ${label} (${target_sec}s) → ${out}"
    if [ "$target_sec" -le "$DUR" ]; then
        # 截取前 N 秒
        ffmpeg -y -i "$OUT_DIR/src.wav" -t "$target_sec" -acodec copy "$out" 2>/dev/null
    else
        # 循环拼接直到达到目标时长
        local repeats=$(( target_sec / DUR + 1 ))
        ffmpeg -y -stream_loop "$repeats" -i "$OUT_DIR/src.wav" -t "$target_sec" -acodec copy "$out" 2>/dev/null
    fi
    local actual=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$out" | cut -d. -f1)
    echo "    实际时长: ${actual}s, 文件大小: $(du -h "$out" | cut -f1)"
}

generate "30s"  30  "$OUT_DIR/test_30s.wav"
generate "2m"   120 "$OUT_DIR/test_2m.wav"
generate "5m"   300 "$OUT_DIR/test_5m.wav"
generate "10m"  600 "$OUT_DIR/test_10m.wav"

echo ""
echo ">>> 生成完毕，文件列表:"
ls -lh "$OUT_DIR"/*.wav
echo ""
echo ">>> 下一步: 上传到 OBS 公开桶，然后把 URL 填入 benchmark.py 的 DEFAULT_SAMPLES"
echo "    或直接本地起一个 HTTP 服务器: cd $OUT_DIR && python3 -m http.server 9000"
echo "    然后: python3 benchmark.py --url http://<你的IP>:9000/test_30s.wav --label 30s --duration 30"
