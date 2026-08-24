# Agent 接口调用指导文档

> 本文档指导调用方如何调用部署在 AgentArts 平台上的智能体（以录音文件转写 Agent 为例）。
> 适用于 ConvAIAgent / 其他系统 / Postman 手动测试。

---

## 一、调用前准备

### 1.1 需要获取的信息

| 信息 | 示例值 | 从哪获取 |
|---|---|---|
| 访问域名 | `defaultgw-grstqnldg5.cn-southwest-2.huaweicloud-agentarts.com` | 运行时详情页「访问方式」 |
| 运行时名称 | `asr-agent` | 部署时起的名字 |
| 认证凭证 | AK/SK（IAM V11 签名） | 华为云「我的凭证」 |
| 接口文档 | 本文档 + 样例报文 | 开发者提供 |

### 1.2 认证方式确认

先确认运行时的认证方式（运行时详情页「入站身份认证方式」）：

| 认证方式 | 调用方式 | 复杂度 |
|---|---|---|
| **IAM** | 需要 V11-HMAC-SHA256 签名 | 标准方式，需用 SDK 或 CLI |

---

## 二、通用请求格式

### 2.1 请求地址

```
POST https://{访问域名}/runtimes/{运行时名称}/invocations
```

### 2.2 请求头

| Header | 必填 | 值 |
|---|---|---|
| Content-Type | 是 | `application/json` |
| Authorization | 是 | IAM V11-HMAC-SHA256 签名 |
| X-Hw-Agentarts-Session-Id | 是 | 任意字符串，建议 UUID |

> ⚠️ **Session 亲和**：create_response 与 fetch_response 必须使用**同一个** Session-Id，否则查不到任务（返回 404）。

### 2.3 请求体统一格式（inputs 包装）

```json
{
  "inputs": {
    "operation": "操作类型",
    "file_url": ["音频文件 URL 数组"],
    "model_config": [ ... ],
    "response_id": "resp_xxx"
  }
}
```

所有字段包在 `inputs` 里。

---

## 三、四个 operation 详解

### 3.1 query_capabilities —— 能力查询

**用途**：调用前确认 Agent 支持哪些能力（平台侧 pre-check 用）。

**请求**
```json
{"inputs": {"operation": "query_capabilities"}}
```

**响应**
```json
{
  "capabilities": {
    "chat_completions": true,
    "responses_api": true,
    "responses_get_fetch": true
  }
}
```

---

### 3.2 chat_completions —— 同步转写

**用途**：转写音频，**阻塞**直到转写完成返回文本。适合短音频（< 5 分钟）。

**请求**
```json
{
  "inputs": {
    "operation": "chat_completions",
    "file_url": ["https://obs.../audio.wav"]
  }
}
```

**响应**
```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "转写文本..."},
      "finish_reason": "stop"
    }
  ]
}
```

**注意**：
- 阻塞时长 ≈ 0.3~1 倍音频时长，调用方需设置足够超时
- 转写失败返回 `500 E5001`

---

### 3.3 create_response —— 异步创建转写任务

**用途**：提交转写任务，**立即返回**任务 ID。适合长音频（> 5 分钟）。

**请求**
```json
{
  "inputs": {
    "operation": "create_response",
    "file_url": ["https://obs.../audio.wav"]
  }
}
```

**响应**
```json
{
  "response_id": "resp_4eb836d7-f1c5-4f5f-be80-84897b7ae61c",
  "status": "in_progress"
}
```

**注意**：
- `response_id` 24 小时有效
- 任务存在创建请求所在实例的内存中（Session 亲和）

---

### 3.4 fetch_response —— 查询异步转写结果

**用途**：用 response_id 查询转写结果。建议轮询（如每 10 秒一次）。

**请求**
```json
{
  "inputs": {
    "operation": "fetch_response",
    "response_id": "resp_4eb836d7-..."
  }
}
```

**响应（已完成）**
```json
{
  "id": "resp_4eb836d7-...",
  "object": "response",
  "status": "completed",
  "output": [
    {
      "type": "message",
      "role": "assistant",
      "content": [
        {"type": "output_text", "text": "转写文本..."}
      ]
    }
  ]
}
```

**响应（进行中）**
```json
{"id": "resp_xxx", "object": "response", "status": "in_progress"}
```

**响应（失败）**
```json
{"id": "resp_xxx", "object": "response", "status": "failed",
 "error": {"code": "E5001", "message": "失败原因", "type": "transcribe_failed"}}
```

**响应（不存在/过期/非本人）→ 404**
```json
{"error": {"code": "E4006", "message": "响应不存在或已过期", "type": "response_not_found"}}
```

---

## 四、错误码

| 错误码 | HTTP | 含义 | 触发场景 |
|---|---|---|---|
| E4001 | 400 | 参数非法 | operation 未知；缺 inputs；缺 file_url；缺 response_id |
| E4006 | 404 | 响应不存在或过期 | response_id 无效/过期(>24h)/Session 不匹配 |
| E4007 | 400 | 能力不支持 | operation 超出声明能力（正常不触发） |
| E5001 | 500 | 转写失败 | 同步转写异常（异步场景返回 200+failed） |

**错误响应格式**
```json
{"error": {"code": "E4001", "message": "错误详情", "type": "invalid_request"}}
```

---

## 五、推荐调用流程

### 5.1 短音频（< 5 分钟）—— 同步调用

```
① query_capabilities  确认能力
② chat_completions    直接拿结果（阻塞）
```

### 5.2 长音频（> 5 分钟）—— 异步调用

```
① query_capabilities      确认能力
② create_response         创建任务，拿 response_id
③ fetch_response 轮询     每 10 秒查一次，直到 completed / failed
   （全程使用同一个 Session-Id）
```

---

## 六、完整调用示例（IAM 签名）

> IAM 认证需要 V11-HMAC-SHA256 签名，不能直接 curl。以下使用项目提供的 `invoke.py`（基于 agentarts SDK 自动签名）。

```bash
# 配置凭证
export HUAWEICLOUD_SDK_AK='你的AK'
export HUAWEICLOUD_SDK_SK='你的SK'

SESSION="sess-$(date +%s)"

# 1. 能力查询
python3 invoke.py -o query_capabilities -s "${SESSION}"

# 2. 同步转写
python3 invoke.py -o chat_completions -f "https://obs.../audio.wav" -s "${SESSION}"

# 3. 异步创建
python3 invoke.py -o create_response -f "https://obs.../audio.wav" -s "${SESSION}"
# 记录返回的 response_id

# 4. 查询结果（Session-Id 必须与创建时相同！）
python3 invoke.py -o fetch_response -i "<response_id>" -s "${SESSION}"
```

或使用 `agentarts invoke` CLI：

```bash
agentarts invoke --agent asr-agent --payload '{"inputs":{"operation":"query_capabilities"}}'
```

---

## 七、Python 调用示例（IAM 签名）

```python
import json
import os
import time
import uuid

from agentarts.sdk.service.runtime_client import RuntimeClient
from agentarts.sdk.service.http_client import SignMode

GATEWAY = "https://defaultgw-grstqnldg5.cn-southwest-2.huaweicloud-agentarts.com"
RUNTIME = "asr-agent"
REGION = "cn-southwest-2"

# 从环境变量读取 AK/SK
client = RuntimeClient(
    data_endpoint=GATEWAY,
    verify_ssl=True,
    sign_mode=SignMode.V11_HMAC_SHA256,
    region_id=REGION,
)


def invoke(operation: str, session: str, **fields):
    payload = {"inputs": {"operation": operation, **fields}}
    return client.invoke_agent(
        agent_name=RUNTIME,
        session_id=session,
        payload=json.dumps(payload, ensure_ascii=False),
        timeout=300,
    )


# --- 同步转写 ---
session = str(uuid.uuid4())
result = invoke("chat_completions", session,
                file_url=["https://obs.../audio.wav"])
print(result["choices"][0]["message"]["content"])

# --- 异步转写 ---
session = str(uuid.uuid4())
result = invoke("create_response", session,
                file_url=["https://obs.../audio.wav"])
response_id = result["response_id"]

while True:
    result = invoke("fetch_response", session, response_id=response_id)
    if result.get("status") == "completed":
        print(result["output"][0]["content"][0]["text"])
        break
    elif result.get("status") == "failed":
        print("转写失败:", result.get("error"))
        break
    time.sleep(10)  # 轮询间隔 10 秒
```

---

## 八、常见问题

| 问题 | 原因 | 解决 |
|---|---|---|
| fetch 返回 404，但 response_id 明明存在 | Session-Id 和 create 时不一致 | 确保 create 和 fetch 用同一个 Session-Id |
| 同步转写超时 | 音频太长 | 改用异步（create_response + fetch_response） |
| 返回 401 Authentication failed | IAM 签名错误或过期 | 确认 AK/SK 和签名算法正确 |
| 返回 400 E4001 缺 inputs | 请求体没包 `inputs` | 按统一格式：`{"inputs": {...}}` |
| file_url 传了字符串报错 | file_url 必须是数组 | 改为 `"file_url": ["https://..."]` |

---

## 九、注意事项汇总

1. **统一格式**：所有字段包在 `inputs` 里
2. **file_url 是数组**：`["https://..."]`，取第一个元素
3. **Session 亲和**：异步的 create 和 fetch 必须同 Session
4. **响应 24h 有效**：过期返回 404 E4006
5. **同步阻塞**：长音频建议用异步
6. **运行时重启**：内存任务丢失，fetch 返回 404（正常现象）
7. **model_config**：调用方（主Agent）需要注入 ConvAIRouter 的 endpoint 和 auth_token 时，放在 `inputs.model_config` 里
