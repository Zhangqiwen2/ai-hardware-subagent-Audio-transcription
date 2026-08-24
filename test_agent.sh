#!/bin/bash
# 录音文件转写 Agent - 一键接口测试脚本
# 用法: bash test_agent.sh
# 依赖: curl + python3（无其他依赖）
# 耗时: 约 2 分钟（含转写等待）

# ========== 配置（按需修改）==========
GATEWAY="https://defaultgw-grstqnldg5.cn-southwest-2.huaweicloud-agentarts.com"
RUNTIME="asr-agent"
API_KEY="asr-agent-sk-2026"
AUDIO_URL="https://asr-test-wav.obs.cn-southwest-2.myhuaweicloud.com/lfasr_%E6%B6%89%E6%94%BF.wav"
WAIT_SECONDS=50
# ==================================

URL="${GATEWAY}/runtimes/${RUNTIME}/invocations"
SESSION="test-$(date +%s)"

echo "============================================"
echo "  录音文件转写 Agent - 接口一键测试"
echo "  时间:   $(date '+%Y-%m-%d %H:%M:%S')"
echo "  网关:   ${GATEWAY}"
echo "  运行时: ${RUNTIME}"
echo "  Session: ${SESSION}"
echo "============================================"
echo ""

# ---------- 辅助函数 ----------
post() {
    local body="$1"
    curl -s -X POST "${URL}" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${API_KEY}" \
        -H "X-Hw-Agentarts-Session-Id: ${SESSION}" \
        -d "${body}"
}

format() {
    python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2, ensure_ascii=False))" 2>/dev/null || cat
}

# ---------- 1. 能力查询 ----------
echo "[1/7] query_capabilities -- 能力查询"
post '{"inputs":{"operation":"query_capabilities"}}' | format
echo -e "\n"

# ---------- 2. 同步转写 ----------
echo "[2/7] chat_completions -- 同步转写（阻塞约 45 秒）"
post "{\"inputs\":{\"operation\":\"chat_completions\",\"file_url\":[\"${AUDIO_URL}\"]}}" | format
echo -e "\n"

# ---------- 3. 异步创建 ----------
echo "[3/7] create_response -- 异步创建转写任务"
RESP_CR=$(post "{\"inputs\":{\"operation\":\"create_response\",\"file_url\":[\"${AUDIO_URL}\"]}}")
echo "${RESP_CR}" | format
RID=$(echo "${RESP_CR}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('response_id',''))" 2>/dev/null)
echo "  >> response_id: ${RID}"
echo -e "\n"

# ---------- 4. 立即查询（预期 in_progress） ----------
if [ -n "${RID}" ]; then
    echo "[4/7] fetch_response -- 立即查询（预期 in_progress）"
    post "{\"inputs\":{\"operation\":\"fetch_response\",\"response_id\":\"${RID}\"}}" | format
    echo -e "\n"

    # ---------- 5. 等待后查询（预期 completed） ----------
    echo "[5/7] fetch_response -- ${WAIT_SECONDS} 秒后查询（预期 completed）"
    sleep "${WAIT_SECONDS}"
    post "{\"inputs\":{\"operation\":\"fetch_response\",\"response_id\":\"${RID}\"}}" | format
    echo -e "\n"
else
    echo "[4/7] 跳过：创建失败，无 response_id"
    echo "[5/7] 跳过：创建失败，无 response_id"
    echo ""
fi

# ---------- 6. 不存在的 response_id（预期 404） ----------
echo "[6/7] fetch_response -- 不存在的 response_id（预期 404 E4006）"
post '{"inputs":{"operation":"fetch_response","response_id":"resp_not_exist"}}' | format
echo -e "\n"

# ---------- 7. 缺 inputs 包装（预期 400） ----------
echo "[7/7] 错误场景 -- 缺 inputs 包装（预期 400 E4001）"
curl -s -X POST "${URL}" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "X-Hw-Agentarts-Session-Id: ${SESSION}-err" \
    -d '{"operation":"query_capabilities"}' | format
echo -e "\n"

echo "============================================"
echo "  测试完成（7 个场景）"
echo "============================================"

资料书 包含接口如何调用 