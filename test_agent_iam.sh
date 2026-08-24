#!/bin/bash
# 录音文件转写 Agent - 一键接口测试脚本（IAM 认证版）
# 用法: bash test_agent_iam.sh
# 依赖: python3 + agentarts-sdk（无其他依赖）
# 认证方式: IAM V11-HMAC-SHA256 签名（通过 invoke.py 自动签名）
# 耗时: 约 2 分钟（含转写等待）

# ========== 配置（按需修改）==========
GATEWAY="https://defaultgw-grstqnldg5.cn-southwest-2.huaweicloud-agentarts.com"
RUNTIME="asr-agent"
AUDIO_URL="https://asr-test-wav.obs.cn-southwest-2.myhuaweicloud.com/lfasr_%E6%B6%89%E6%94%BF.wav"
WAIT_SECONDS=50
# ==================================

SESSION="test-$(date +%s)"

echo "============================================"
echo "  录音文件转写 Agent - 接口一键测试（IAM）"
echo "  时间:   $(date '+%Y-%m-%d %H:%M:%S')"
echo "  网关:   ${GATEWAY}"
echo "  运行时: ${RUNTIME}"
echo "  Session: ${SESSION}"
echo "  认证:   IAM V11-HMAC-SHA256"
echo "============================================"
echo ""

# 检查 AK/SK
if [ -z "${HUAWEICLOUD_SDK_AK}" ] || [ -z "${HUAWEICLOUD_SDK_SK}" ]; then
    echo "❌ 请先设置环境变量:"
    echo "   export HUAWEICLOUD_SDK_AK='你的AK'"
    echo "   export HUAWEICLOUD_SDK_SK='你的SK'"
    exit 1
fi

# ---------- 辅助函数 ----------
_invoke() {
    local operation="$1"
    local extra_args="$2"
    python3 invoke.py -o "${operation}" -s "${SESSION}" ${extra_args}
}

format() {
    python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2, ensure_ascii=False))" 2>/dev/null || cat
}

# ---------- 1. 能力查询 ----------
echo "[1/7] query_capabilities -- 能力查询"
_invoke "query_capabilities" | format
echo -e "\n"

# ---------- 2. 同步转写 ----------
echo "[2/7] chat_completions -- 同步转写（阻塞约 45 秒）"
_invoke "chat_completions" "-f ${AUDIO_URL}" | format
echo -e "\n"

# ---------- 3. 异步创建 ----------
echo "[3/7] create_response -- 异步创建转写任务"
RESP_CR=$(_invoke "create_response" "-f ${AUDIO_URL}")
echo "${RESP_CR}" | format
RID=$(echo "${RESP_CR}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('response_id',''))" 2>/dev/null)
echo "  >> response_id: ${RID}"
echo -e "\n"

# ---------- 4. 立即查询（预期 in_progress） ----------
if [ -n "${RID}" ]; then
    echo "[4/7] fetch_response -- 立即查询（预期 in_progress）"
    _invoke "fetch_response" "-i ${RID}" | format
    echo -e "\n"

    # ---------- 5. 等待后查询（预期 completed） ----------
    echo "[5/7] fetch_response -- ${WAIT_SECONDS} 秒后查询（预期 completed）"
    sleep "${WAIT_SECONDS}"
    _invoke "fetch_response" "-i ${RID}" | format
    echo -e "\n"
else
    echo "[4/7] 跳过：创建失败，无 response_id"
    echo "[5/7] 跳过：创建失败，无 response_id"
    echo ""
fi

# ---------- 6. 不存在的 response_id（预期 404） ----------
echo "[6/7] fetch_response -- 不存在的 response_id（预期 404 E4006）"
_invoke "fetch_response" "-i resp_not_exist" | format
echo -e "\n"

# ---------- 7. 缺 inputs 包装（预期 400） ----------
echo "[7/7] 错误场景 -- 缺 inputs 包装（预期 400 E4001）"
# 直接调 invoke.py 但用错误的 payload（缺 inputs）
python3 -c "
import json, sys
sys.path.insert(0, '.')
from invoke import GATEWAY, RUNTIME
from agentarts.sdk.service.runtime_client import RuntimeClient
from agentarts.sdk.service.http_client import SignMode

client = RuntimeClient(
    data_endpoint='https://${GATEWAY}',
    verify_ssl=True,
    sign_mode=SignMode.V11_HMAC_SHA256,
    region_id='cn-southwest-2',
)
try:
    result = client.invoke_agent(
        agent_name='${RUNTIME}',
        session_id='${SESSION}-err',
        payload=json.dumps({'operation':'query_capabilities'}, ensure_ascii=False),
        timeout=30,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
except Exception as e:
    print(json.dumps({'error': str(e)}, ensure_ascii=False))
" | format
echo -e "\n"

echo "============================================"
echo "  测试完成（7 个场景）"
echo "============================================"
