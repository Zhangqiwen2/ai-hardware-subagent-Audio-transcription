#!/usr/bin/env python3
"""
从现有 98 秒音频生成不同时长的测试音频（无需 ffmpeg）
用 Python 标准库 wave 实现：截取 / 循环拼接

用法：
  python3 scripts/gen_test_audio.py
  python3 scripts/gen_test_audio.py --src /path/to/source.wav
"""

import argparse
import os
import sys
import wave

# 源音频 URL
SRC_URL = "https://asr-test-wav.obs.cn-southwest-2.myhuaweicloud.com/lfasr_%E6%B6%89%E6%94%BF.wav"
OUT_DIR = "./test_audio"

# 要生成的目标时长（秒）
TARGETS = [
    (30,  "test_30s.wav"),
    (120, "test_2m.wav"),
    (300, "test_5m.wav"),
    (600, "test_10m.wav"),
]


def download_src(url: str, dest: str):
    """下载源音频"""
    import subprocess
    if os.path.exists(dest):
        print(f"  源文件已存在: {dest}")
        return
    print(f"  下载源音频: {url[:60]} ...")
    result = subprocess.run(["curl", "-sL", "-o", dest, url], capture_output=True)
    if result.returncode != 0:
        print(f"  下载失败: {result.stderr.decode()[:200]}")
        sys.exit(1)
    size = os.path.getsize(dest)
    print(f"  下载完成: {size / 1024:.0f} KB")


def make_audio(src_path: str, out_path: str, target_sec: int):
    """从源音频生成目标时长的音频"""
    with wave.open(src_path, "rb") as wf:
        params = wf.getparams()
        rate = wf.getframerate()
        sampwidth = wf.getsampwidth()
        nch = wf.getnchannels()
        nframes = wf.getnframes()
        src_duration = nframes / rate
        src_frames = wf.readframes(nframes)

    print(f"  源: {src_duration:.1f}s, {rate}Hz, {sampwidth}B, {nch}ch, {nframes} frames")

    target_frames = int(target_sec * rate)

    if target_frames <= nframes:
        # 截取前 N 秒
        print(f"  → 截取前 {target_sec}s")
        out_data = src_frames[:target_frames * sampwidth * nch]
    else:
        # 循环拼接
        needed = target_frames
        chunks = []
        remaining = needed * sampwidth * nch
        while remaining > 0:
            take = min(remaining, len(src_frames))
            chunks.append(src_frames[:take])
            remaining -= take
        out_data = b"".join(chunks)
        print(f"  → 循环拼接 {target_sec}s（{ target_sec / src_duration:.1f}x 源时长）")

    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(nch)
        wf.setsampwidth(sampwidth)
        wf.setframerate(rate)
        wf.writeframes(out_data)

    actual_sec = len(out_data) / (sampwidth * nch * rate)
    size_kb = os.path.getsize(out_path) / 1024
    print(f"  ✓ 输出: {out_path}  ({actual_sec:.1f}s, {size_kb:.0f} KB)")


def main():
    parser = argparse.ArgumentParser(description="生成不同时长的测试音频")
    parser.add_argument("--src", help="源音频本地路径（默认自动下载）")
    parser.add_argument("--out-dir", default=OUT_DIR, help="输出目录")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    src_path = args.src or os.path.join(args.out_dir, "src.wav")

    # 下载源文件
    if not args.src:
        download_src(SRC_URL, src_path)

    # 验证源文件
    if not os.path.exists(src_path):
        print(f"❌ 源文件不存在: {src_path}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  生成目标时长: {[t[0] for t in TARGETS]} 秒")
    print(f"  输出目录: {args.out_dir}")
    print(f"{'='*60}\n")

    for target_sec, filename in TARGETS:
        out_path = os.path.join(args.out_dir, filename)
        print(f"\n--- {filename} ({target_sec}s) ---")
        make_audio(src_path, out_path, target_sec)

    print(f"\n{'='*60}")
    print("  全部生成完毕！文件列表：")
    print(f"{'='*60}")
    for _, filename in TARGETS:
        p = os.path.join(args.out_dir, filename)
        if os.path.exists(p):
            size = os.path.getsize(p) / 1024
            with wave.open(p, "rb") as wf:
                dur = wf.getnframes() / wf.getframerate()
            print(f"  {filename:<20} {dur:>8.1f}s  {size:>8.0f} KB")

    print(f"\n  下一步：")
    print(f"  1. 上传到 OBS 公开桶")
    print(f"  2. 编辑 benchmark.py 的 DEFAULT_SAMPLES，填入各 URL")
    print(f"  3. python3 benchmark.py")


if __name__ == "__main__":
    main()
