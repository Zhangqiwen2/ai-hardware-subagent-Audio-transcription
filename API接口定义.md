# 录音文件转写 Agent - API 接口定义

## 基础信息

| 项 | 值 |
|---|---|
| 接口地址 | `POST https://defaultgw-grstqnldg5.cn-southwest-2.huaweicloud-agentarts.com/runtimes/asr-agent/invocations` |
| 认证方式 | `Authorization: Bearer {API_KEY}` |
| 请求头 | `Content-Type: application/json` |
| 必填头 | `X-Hw-Agentarts-Session-Id: {会话ID字符串}` |
| Session 亲和 | create_response 与 fetch_response 必须使用**同一个** Session-Id，否则 404 |

---

## 统一请求体（invocation 信封，inputs 包装格式）

所有请求都包在 `inputs` 里（**唯一格式，不兼容旧版扁平格式**）：

```json
{
  "inputs": {
    "operation": "query_capabilities | chat_completions | create_response | fetch_response",
    "file_url": ["https://obs.../audio.wav"],
    "model_config": [ { "type": "offline_asr_upload", ... } ],
    "response_id": "resp_xxx"
  }
}
```

### operation 必填参数对照

| operation | 必填 | 选填 | 说明 |
|---|---|---|---|
| `query_capabilities` | 无 | 无 | 仅需 inputs.operation |
| `chat_completions` | `inputs.file_url[0]` | `inputs.model_config` | 同步阻塞 |
| `create_response` | `inputs.file_url[0]` | `inputs.model_config` | 异步，立即返回 |
| `fetch_response` | `inputs.response_id` | 无 | 异步查询 |

> `file_url` 是**数组**，取第一个元素（暂不支持多文件）。
> 缺必填参数时返回 `400 E4001`。缺 `inputs` 包装也返回 `400 E4001`。

---

## ① query_capabilities —— 能力查询

### 接口说明
所有 operation 共用同一个接口地址和请求头，通过请求体中的 `operation` 字段路由到不同处理逻辑。以下以 query_capabilities 为例展示完整输入输出，其余 operation 仅列出请求体差异。

### 请求地址
```
POST https://defaultgw-grstqnldg5.cn-southwest-2.huaweicloud-agentarts.com/runtimes/asr-agent/invocations
```

### 请求头
| Header | 值 | 必填 |
|---|---|---|
| Content-Type | application/json | 是 |
| Authorization | Bearer {API_KEY} | 是 |
| X-Hw-Agentarts-Session-Id | 任意字符串（如 "sess-001"） | 是 |

### 请求体
```json
{
  "inputs": {
    "operation": "query_capabilities"
  }
}
```

### 响应 200
```json
{
  "capabilities": {
    "chat_completions": true,
    "responses_api": true,
    "responses_get_fetch": true
  }
}
```

### 完整 curl 示例
```bash
curl -s -X POST https://defaultgw-grstqnldg5.cn-southwest-2.huaweicloud-agentarts.com/runtimes/asr-agent/invocations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {API_KEY}" \
  -H "X-Hw-Agentarts-Session-Id: sess-001" \
  -d '{"inputs":{"operation":"query_capabilities"}}'
```

---

## ② chat_completions —— 同步转写（阻塞至完成）

### 请求
```json
{
  "inputs": {
    "operation": "chat_completions",
    "file_url": ["https://obs.../audio.wav"],
    "model_config": [
      {
        "type": "offline_asr_upload",
        "endpoint": "https://convai-router/v2",
        "auth_token": "Bearer xxx"
      }
    ]
  }
}
```

### 响应 200 —— 成功
```json
{
  "id": "chatcmpl-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "转写文本" },
      "finish_reason": "stop"
    }
  ]
}
```

### 响应 500 —— 失败
```json
{
  "error": { "code": "E5001", "message": "转写失败: 原因", "type": "transcribe_failed" }
}
```

---

## ③ create_response —— 异步创建转写任务

### 请求
```json
{
  "inputs": {
    "operation": "create_response",
    "file_url": ["https://obs.../audio.wav"],
    "model_config": [
      {
        "type": "offline_asr_upload",
        "endpoint": "https://convai-router/v2",
        "auth_token": "Bearer xxx"
      }
    ]
  }
}
```

### 响应 200
```json
{
  "response_id": "resp_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "status": "in_progress"
}
```

---

## ④ fetch_response —— 查询异步转写结果

### 请求
```json
{
  "inputs": {
    "operation": "fetch_response",
    "response_id": "resp_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  }
}
```
> ⚠️ X-Hw-Agentarts-Session-Id 必须与 create_response 时相同

### 响应 200 —— 已完成
```json
{
  "id": "resp_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "object": "response",
  "status": "completed",
  "output": [
    {
      "type": "message",
      "role": "assistant",
      "content": [
        { "type": "output_text", "text": "转写文本" }
      ]
    }
  ]
}
```

### 响应 200 —— 进行中
```json
{
  "id": "resp_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "object": "response",
  "status": "in_progress"
}
```

### 响应 200 —— 转写失败
```json
{
  "id": "resp_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "object": "response",
  "status": "failed",
  "error": { "code": "E5001", "message": "失败原因", "type": "transcribe_failed" }
}
```

### 响应 404 —— 不存在/过期/非本人
```json
{
  "error": { "code": "E4006", "message": "响应不存在或已过期", "type": "response_not_found" }
}
```

---

## 错误码汇总

| 错误码 | HTTP | 触发场景 |
|---|---|---|
| E4001 | 400 | operation 未知枚举；缺 input；缺 response_id；请求体非 JSON |
| E4006 | 404 | response_id 不存在/过期(>24h)/非本人 |
| E5001 | 500 | 同步转写失败（异步场景返回 200 + status=failed） |

---

## model_config 字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| 位置 | `inputs.model_config` | 是 | 在 inputs **里面** |
| `type` | 是 | 固定为 `offline_asr` |
| `endpoint` | 是 | ConvAIRouter 地址，兼容带/不带 `/v2` 后缀 |
| `auth_token` | 是 | Bearer 认证 token，兼容带/不带 `Bearer ` 前缀 |
| `provider` | 否 | 预留 |
| `model_name` | 否 | 预留 |