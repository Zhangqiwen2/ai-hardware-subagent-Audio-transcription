#!/usr/bin/env python3
"""
录音文件转写 Agent - 响应时间摸底测试

测试方法：
  - 对每个音频样本，分别测 chat_completions（同步）和 create+fetch（异步）的耗时
  - chat_completions：单次请求 wall-clock
  - create_response：TTFB（任务受理）
  - fetch_response：轮询直到 completed 的 wall-clock（从 create 发起开始算）
  - 每个样本测 2 轮取平均，消除冷启动波动

用法：
  python3 benchmark.py                   # 使用内置样本列表
  python3 benchmark.py --url URL --label "标签" --duration 120  # 追加单个

前置：
  - curl（系统自带）
  - python3（系统自带）
  - 音频 URL 必须公网可访问（OBS 公开桶或临时签名 URL）
"""

import json
import statistics
import subprocess
import sys
import time
import argparse
import uuid
from datetime import datetime

# ========== 配置 ==========
GATEWAY = "https://defaultgw-grstqnldg5.cn-southwest-2.huaweicloud-agentarts.com"
RUNTIME = "asr-agent"
API_KEY = "asr-agent-sk-2026"
URL = f"{GATEWAY}/runtimes/{RUNTIME}/invocations"
POLL_INTERVAL = 10       # 异步轮询间隔（秒）
POLL_TIMEOUT = 1800      # 轮询超时（秒，30 分钟）
ROUNDS = 2               # 每个样本测几轮取平均
# ==========================

# 内置样本列表（可按需增删）
DEFAULT_SAMPLES = [
    {
        "label": "30秒",
        "url": "https://asr-test-wav.obs.cn-southwest-2.myhuaweicloud.com/test_30s.wav",
        "duration_sec": 30,
    },
    {
        "label": "2分钟",
        "url": "https://asr-test-wav.obs.cn-southwest-2.myhuaweicloud.com/test_2m.wav",
        "duration_sec": 120,
    },
    {
        "label": "5分钟",
        "url": "https://asr-test-wav.obs.cn-southwest-2.myhuaweicloud.com/test_5m.wav",
        "duration_sec": 300,
    },
    {
        "label": "10分钟",
        "url": "https://asr-test-wav.obs.cn-southwest-2.myhuaweicloud.com/test_10m.wav",
        "duration_sec": 600,
    },
]


def curl_post(body: dict, session: str, timeout: int = 300) -> dict:
    """发起 POST 请求，返回解析后的 JSON + 耗时（秒）"""
    cmd = [
        "curl", "-s",
        "-w", "\n%{http_code} %{time_total} %{time_starttransfer}",
        "-X", "POST", URL,
        "-H", "Content-Type: application/json",
        "-H", f"Authorization: Bearer {API_KEY}",
        "-H", f"X-Hw-Agentarts-Session-Id: {session}",
        "-d", json.dumps(body),
        "--max-time", str(timeout),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stdout.strip()
    lines = output.rsplit("\n", 1)
    if len(lines) != 2:
        raise RuntimeError(f"curl 输出异常: {output[:200]}")
    body_str, metrics = lines
    parts = metrics.split()
    http_code = int(parts[0])
    time_total = float(parts[1])
    time_ttfb = float(parts[2])
    data = json.loads(body_str)
    return data, http_code, time_total, time_ttfb


def bench_sync(audio_url: str, label: str) -> dict:
    """同步转写耗时"""
    session = f"sync-{uuid.uuid4().hex[:8]}"
    body = {"inputs": {"operation": "chat_completions", "file_url": [audio_url]}}
    data, code, total, ttfb = curl_post(body, session, timeout=600)
    return {
        "operation": "chat_completions",
        "label": label,
        "http_code": code,
        "total_sec": round(total, 2),
        "ttfb_sec": round(ttfb, 2),
        "status": data.get("error", {}).get("code", "OK") if code != 200 else "completed",
    }


def bench_async(audio_url: str, label: str, duration_sec: int, poll_interval: int = POLL_INTERVAL) -> dict:
    """异步 create+fetch 耗时"""
    session = f"async-{uuid.uuid4().hex[:8]}"

    # --- create ---
    t0 = time.time()
    body_create = {"inputs": {"operation": "create_response", "file_url": [audio_url]}}
    data, code, _, create_ttfb = curl_post(body_create, session)
    create_elapsed = time.time() - t0

    if code != 200 or "response_id" not in data:
        return {
            "operation": "create_response",
            "label": label,
            "http_code": code,
            "create_ttfb_sec": round(create_ttfb, 2),
            "total_sec": None,
            "status": data.get("error", {}).get("code", f"HTTP{code}"),
        }

    response_id = data["response_id"]

    # --- fetch 轮询 ---
    deadline = time.time() + POLL_TIMEOUT
    poll_count = 0
    final_status = "timeout"
    first_completed_elapsed = None

    while time.time() < deadline:
        time.sleep(poll_interval)
        poll_count += 1
        body_fetch = {"inputs": {"operation": "fetch_response", "response_id": response_id}}
        fdata, fcode, _, _ = curl_post(body_fetch, session)
        elapsed = time.time() - t0

        if fcode == 200 and fdata.get("status") in ("completed", "failed"):
            final_status = fdata["status"]
            first_completed_elapsed = round(elapsed, 2)
            break

    return {
        "operation": "create+fetch",
        "label": label,
        "http_code": 200,
        "create_ttfb_sec": round(create_ttfb, 2),
        "poll_count": poll_count,
        "total_sec": first_completed_elapsed,
        "status": final_status,
    }


def run_rounds(sample: dict, rounds: int = ROUNDS, poll_interval: int = POLL_INTERVAL) -> dict:
    """对单个样本跑 rounds 轮，返回平均值"""
    label = sample["label"]
    url = sample["url"]
    dur = sample.get("duration_sec", 0)

    print(f"\n{'='*60}")
    print(f"  样本: {label}")
    print(f"  音频时长: {dur}s（~{dur//60}m{dur%60}s）" if dur else "  音频时长: 未知")
    print(f"  URL: {url[:80]}...")
    print(f"  跑 {rounds} 轮取平均")
    print(f"{'='*60}")

    sync_results = []
    async_results = []

    for i in range(rounds):
        print(f"\n  --- 第 {i+1}/{rounds} 轮 ---")

        # 同步
        print(f"  [sync] chat_completions ...", end="", flush=True)
        r = bench_sync(url, label)
        sync_results.append(r)
        print(f" {r['http_code']} | total={r['total_sec']}s | status={r['status']}")

        # 间隔避免限流
        time.sleep(3)

        # 异步
        print(f"  [async] create+fetch ...", end="", flush=True)
        r = bench_async(url, label, dur, poll_interval=poll_interval)
        async_results.append(r)
        total_str = f"{r['total_sec']}s" if r['total_sec'] else "timeout"
        print(f" create_ttfb={r['create_ttfb_sec']}s | polls={r['poll_count']} | total={total_str} | status={r['status']}")

        if i < rounds - 1:
            time.sleep(5)

    # 汇总
    sync_totals = [r["total_sec"] for r in sync_results if r["status"] == "completed"]
    async_totals = [r["total_sec"] for r in async_results if r["status"] == "completed"]
    create_ttfbs = [r["create_ttfb_sec"] for r in async_results]

    return {
        "label": label,
        "duration_sec": dur,
        "sync_avg_sec": round(statistics.mean(sync_totals), 2) if sync_totals else None,
        "sync_min_sec": round(min(sync_totals), 2) if sync_totals else None,
        "sync_max_sec": round(max(sync_totals), 2) if sync_totals else None,
        "async_create_ttfb_avg": round(statistics.mean(create_ttfbs), 2) if create_ttfbs else None,
        "async_total_avg_sec": round(statistics.mean(async_totals), 2) if async_totals else None,
        "async_total_min_sec": round(min(async_totals), 2) if async_totals else None,
        "async_total_max_sec": round(max(async_totals), 2) if async_totals else None,
        "ratio_sync": round(statistics.mean(sync_totals) / dur, 2) if sync_totals and dur else None,
        "ratio_async": round(statistics.mean(async_totals) / dur, 2) if async_totals and dur else None,
    }


def print_report(summary: list):
    """打印汇总报告"""
    print(f"\n\n{'='*80}")
    print(f"  录音文件转写 Agent - 响应时间摸底报告")
    print(f"  测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  每样本轮数: {ROUNDS}")
    print(f"{'='*80}")

    # 表1：原始数据
    print(f"\n{'样本':<20} {'音频时长':>8} {'同步avg':>10} {'同步min':>10} {'同步max':>10} {'异步avg':>10} {'异步min':>10} {'异步max':>10}")
    print("-" * 100)
    for s in summary:
        dur = f"{s['duration_sec']}s" if s['duration_sec'] else "?"
        sync_avg = f"{s['sync_avg_sec']}s" if s['sync_avg_sec'] else "FAIL"
        sync_min = f"{s['sync_min_sec']}s" if s['sync_min_sec'] else "-"
        sync_max = f"{s['sync_max_sec']}s" if s['sync_max_sec'] else "-"
        async_avg = f"{s['async_total_avg_sec']}s" if s['async_total_avg_sec'] else "FAIL"
        async_min = f"{s['async_total_min_sec']}s" if s['async_total_min_sec'] else "-"
        async_max = f"{s['async_total_max_sec']}s" if s['async_total_max_sec'] else "-"
        print(f"{s['label']:<20} {dur:>8} {sync_avg:>10} {sync_min:>10} {sync_max:>10} {async_avg:>10} {async_min:>10} {async_max:>10}")

    # 表2：耗时/时长比
    print(f"\n{'样本':<20} {'音频时长':>8} {'同步/时长':>10} {'异步/时长':>10} {'结论'}")
    print("-" * 80)
    for s in summary:
        dur = f"{s['duration_sec']}s" if s['duration_sec'] else "?"
        ratio_sync = f"{s['ratio_sync']}x" if s['ratio_sync'] else "N/A"
        ratio_async = f"{s['ratio_async']}x" if s['ratio_async'] else "N/A"
        # 结论
        if s['ratio_sync'] and s['ratio_sync'] < 0.5:
            conclusion = "快（<0.5x）"
        elif s['ratio_sync'] and s['ratio_sync'] < 1.0:
            conclusion = "中等（0.5~1x）"
        elif s['ratio_sync']:
            conclusion = "慢（>1x）"
        else:
            conclusion = "测试失败"
        print(f"{s['label']:<20} {dur:>8} {ratio_sync:>10} {ratio_async:>10}   {conclusion}")

    # 估算参考
    print(f"\n{'='*80}")
    print("  估算参考（基于实测比例）")
    print(f"{'='*80}")
    # 用所有样本的平均比例
    ratios = [s['ratio_sync'] for s in summary if s['ratio_sync']]
    if ratios:
        avg_ratio = statistics.mean(ratios)
        print(f"  实测平均耗时比: {avg_ratio:.2f}x 音频时长")
        print()
        for minutes in [1, 5, 10, 30, 60, 120, 180]:
            est = minutes * 60 * avg_ratio
            est_min = int(est // 60)
            est_sec = int(est % 60)
            print(f"    {minutes:>3} 分钟音频  →  预估 {est_min}分{est_sec}秒")
    print(f"\n  ⚠️ 以上为粗略估算，实际受音频格式、大小、网络、讯飞负载影响")
    print(f"{'='*80}")


def main():
    parser = argparse.ArgumentParser(description="转写 Agent 响应时间摸底")
    parser.add_argument("--url", help="追加测试音频 URL")
    parser.add_argument("--label", help="音频标签")
    parser.add_argument("--duration", type=int, help="音频时长（秒）")
    parser.add_argument("--rounds", type=int, default=ROUNDS, help=f"每样本轮数（默认{ROUNDS}）")
    parser.add_argument("--poll-interval", type=int, default=POLL_INTERVAL, help=f"轮询间隔秒（默认{POLL_INTERVAL}）")
    args = parser.parse_args()

    rounds = args.rounds
    poll_interval = args.poll_interval

    samples = list(DEFAULT_SAMPLES)
    if args.url:
        samples.append({
            "label": args.label or "自定义音频",
            "url": args.url,
            "duration_sec": args.duration or 0,
        })

    if not samples:
        print("❌ 无测试样本。请通过 --url 添加音频，或编辑 DEFAULT_SAMPLES 列表")
        sys.exit(1)

    print(f"  测试样本数: {len(samples)}")
    print(f"  每样本轮数: {rounds}")
    print(f"  轮询间隔: {poll_interval}s")
    print(f"  总预计耗时: ~{len(samples) * rounds * 3} 分钟（取决于音频时长）")

    summary = []
    for s in samples:
        result = run_rounds(s, rounds=rounds, poll_interval=poll_interval)
        summary.append(result)

    print_report(summary)

    # 保存 JSON 结果
    out_file = f"benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_file, "w") as f:
        json.dump({"timestamp": datetime.now().isoformat(), "config": {
            "gateway": GATEWAY, "runtime": RUNTIME, "rounds": rounds,
        }, "results": summary}, f, ensure_ascii=False, indent=2)
    print(f"\n  详细结果已保存: {out_file}")


if __name__ == "__main__":
    main()
