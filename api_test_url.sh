#!/bin/bash
# ============================================
# 录音文件转写 Agent - 接口测试 curl 命令集
# ============================================
# 使用方法：直接 bash api_test_curl.sh 运行全部接口
# 或单独复制某个 curl 命令执行
# ============================================


BASE_URL="https://defaultgw-grstqnldg5.cn-southwest-2.huaweicloud-agentarts.com/runtimes/asr-agent/invocations"
AUTH="Bearer asr-agent-sk-2026"
AUDIO_URL="https://asr-test-wav.obs.cn-southwest-2.myhuaweicloud.com/lfasr_%E6%B6%89%E6%94%BF.wav"


echo "============================================"
echo "1. query_capabilities - 能力查询"
echo "============================================"
curl -sS -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: $AUTH" \
  -H "X-Hw-Agentarts-Session-Id: test-001" \
  -d '{"inputs":{"operation":"query_capabilities"}}' | python3 -m json.tool 2>/dev/null || cat
echo -e "\n"


echo "============================================"
echo "2. chat_completions - 同步转写（阻塞约45秒）"
echo "============================================"
curl -sS -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: $AUTH" \
  -H "X-Hw-Agentarts-Session-Id: final-test-002" \
  -d "{\"inputs\":{\"operation\":\"chat_completions\",\"file_url\":[\"$AUDIO_URL\"]}}" | python3 -m json.tool 2>/dev/null || cat
echo -e "\n"


echo "============================================"
echo "3. create_response - 异步创建"
echo "============================================"
RESPONSE=$(curl -sS -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: $\(AUTH" \
  -H "X-Hw-Agentarts-Session-Id: final-test-003" \
  -d "{\"inputs\":{\"operation\":\"create_response\",\"file_url\":[\"\)$AUDIO_URL\"]}}")
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
RESPONSE_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('response_id',''))" 2>/dev/null)
echo "提取到 response_id: $RESPONSE_ID"
echo -e "\n"


if [ -n "$RESPONSE_ID" ]; then
  echo "等待30秒后查询结果..."
  sleep 30
  echo "============================================"
  echo "4. fetch_response - 查询异步结果"
  echo "============================================"
  curl -sS -X POST "$BASE_URL" \
    -H "Content-Type: application/json" \
    -H "Authorization: $\(AUTH" \
    -H "X-Hw-Agentarts-Session-Id: final-test-003" \
    -d "{\"inputs\":{\"operation\":\"fetch_response\",\"response_id\":\"\)$RESPONSE_ID\"}}" | python3 -m json.tool 2>/dev/null || cat
  echo -e "\n"
else
  echo "❌ 未获取到 response_id，跳过 fetch_response 测试"
  echo "可手动执行："
  echo "curl -sS -X POST '$BASE_URL' \\"
  echo "  -H 'Content-Type: application/json' \\"
  echo "  -H 'Authorization: $AUTH' \\"
  echo "  -H 'X-Hw-Agentarts-Session-Id: final-test-003' \\"
  echo "  -d '{\"inputs\":{\"operation\":\"fetch_response\",\"response_id\":\"替换为实际ID\"}}'"
  echo ""
fi


echo "============================================"
echo "5.1 错误场景 - 未知operation"
echo "============================================"
curl -sS -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: $AUTH" \
  -H "X-Hw-Agentarts-Session-Id: test-err-001" \
  -d '{"inputs":{"operation":"unknown_op"}}' | python3 -m json.tool 2>/dev/null || cat
echo -e "\n"


echo "============================================"
echo "5.2 错误场景 - 缺inputs包装"
echo "============================================"
curl -sS -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: $AUTH" \
  -H "X-Hw-Agentarts-Session-Id: test-err-002" \
  -d '{"operation":"query_capabilities"}' | python3 -m json.tool 2>/dev/null || cat
echo -e "\n"


echo "============================================"
echo "5.3 错误场景 - 缺file_url"
echo "============================================"
curl -sS -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: $AUTH" \
  -H "X-Hw-Agentarts-Session-Id: test-err-003" \
  -d '{"inputs":{"operation":"create_response"}}' | python3 -m json.tool 2>/dev/null || cat
echo -e "\n"


echo "============================================"
echo "5.4 错误场景 - 缺response_id"
echo "============================================"
curl -sS -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: $AUTH" \
  -H "X-Hw-Agentarts-Session-Id: test-err-004" \
  -d '{"inputs":{"operation":"fetch_response"}}' | python3 -m json.tool 2>/dev/null || cat
echo -e "\n"


echo "============================================"
echo "5.5 错误场景 - 不存在的response_id"
echo "============================================"
curl -sS -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: $AUTH" \
  -H "X-Hw-Agentarts-Session-Id: test-err-005" \
  -d '{"inputs":{"operation":"fetch_response","response_id":"resp_not_exist"}}' | python3 -m json.tool 2>/dev/null || cat
echo -e "\n"


echo "============================================"
echo "5.6 错误场景 - 跨Session查询"
echo "============================================"
curl -sS -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: $AUTH" \
  -H "X-Hw-Agentarts-Session-Id: wrong-session" \
  -d '{"inputs":{"operation":"fetch_response","response_id":"resp_4eb836d7-f1c5-4f5f-be80-84897b7ae61c"}}' | python3 -m json.tool 2>/dev/null || cat
echo -e "\n"


echo "============================================"
echo "全部测试完成"
echo "============================================"