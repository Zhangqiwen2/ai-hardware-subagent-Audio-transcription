# 录音文件转写智能体（AgentArts 高代码运行时）

基于讯飞「录音文件转写大模型」API，使用 **AgentArts SDK** 封装为华为云 AgentArts（智果）平台的高代码智能体运行时。
接收 WAV 音频文件，返回转写纯文本。

## 架构

```
调用方 ──HTTPS──> AgentArts 网关 ──> /invocations ──> app.py (AgentArtsRuntimeApp)
                                                         │ @app.entrypoint
                                                         ▼
                                                   service.py 解析音频来源
                                                         │
                                          file_path / audio_url / message
                                                         ▼
                                              iflytek_asr.py 上传+轮询
                                                         │
                                                         ▼
                                            讯飞转写 API (office-api-ist-dx.iflyaisol.com)
```

- **运行时形态**：基于 `agentarts-sdk` 的 `AgentArtsRuntimeApp`，`@app.entrypoint` 注册 `/invocations`
- **入栈协议**：HTTP（`/invocations` POST、`/ping` GET 由 SDK 提供）
- **端口**：`AGENT_RUN_PORT`（默认 8080）
- **出站网络**：需 `PUBLIC`（公网），以便运行时访问讯飞 API

## 项目结构

```
asr_agent/
├── app.py              # AgentArts SDK 入口（@app.entrypoint，入口对象 app）
├── service.py          # 转写服务：payload 解析 + 调用讯飞（与 SDK 解耦）
├── iflytek_asr.py      # 讯飞客户端：HMAC-SHA1 签名、上传、轮询查询
├── result_parser.py    # 转写结果解析（多层嵌套 JSON -> 纯文本）
├── config.py           # 环境变量配置（自动加载 .env）
├── main.py             # 本地测试入口（不依赖 SDK，直接转写）
├── requirements.txt
├── .env.example        # 密钥与运行参数模板
└── tests/
    └── test_local.py   # 解析器单测 + 完整转写测试
```

## 接口说明

### `POST /invocations`

请求体 `payload` 支持以下音频来源（任选其一）：

```jsonc
// 方式一：运行时内文件路径（经 ExecuteRuntimeUploadFiles 上传后）
{"file_path": "/data/audio/test.wav"}

// 方式二：公网音频 URL（运行时自动下载）
{"audio_url": "https://example.com/test.wav"}

// 方式三：message 字段传路径或 URL
{"message": "/data/audio/test.wav"}

// 可选：覆盖语言/领域
{"file_path": "/data/test.wav", "language": "autodialect", "pd": "finance"}
```

响应：

```json
{"response": "喂你好，舒高先生是吧...", "status": "success"}
```

失败时：`{"response": "转写失败: ...", "status": "error"}`

### `GET /ping` -> `{"status": "Healthy"}`（SDK 默认健康检查）

## 本地开发

```bash
cd asr_agent
pip install -r requirements.txt   # 含 agentarts-sdk

# 配置讯飞密钥
cp .env.example .env
# 编辑 .env 填入真实的 IFLYTEK_APP_ID / IFLYTEK_API_KEY / IFLYTEK_API_SECRET

# 方式一：直接转写示例音频（不启动 HTTP 服务）
python main.py ../Ifasr_llm/audio/lfasr_涉政.wav

# 方式二：启动 SDK HTTP 服务并调用
python app.py
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"file_path": "../Ifasr_llm/audio/lfasr_涉政.wav"}'
```

仅验证解析逻辑（无需密钥/网络/SDK）：

```bash
python tests/test_local.py
```

## 部署到 AgentArts

> 前置：Linux ARM64 环境（鲲鹏 ECS）、Python 3.10+、Docker 18.06+。X86 镜像调用会失败。

### 1. 安装 SDK 并配置华为云凭证

```bash
pip install agentarts-sdk
export HUAWEICLOUD_SDK_AK="your-access-key"
export HUAWEICLOUD_SDK_SK="your-secret-key"
```

### 2. 配置智能体

```bash
agentarts configure --entrypoint app:app
```

按指引配置：智能体名称（小写字母开头）、部署区域（`cn-southwest-2`）、`requirements.txt`、SWR 镜像组织名。

### 3. 一键构建并部署

```bash
agentarts launch
```

该命令自动完成：本地构建 **ARM64** Docker 镜像 -> 推送到华为云 SWR -> 部署到 AgentArts 运行时。（无需手写 Dockerfile）

### 4. 配置运行时环境变量与网络

在 AgentArts 控制台「部署运行 > 智能体运行时」中：
- **环境变量**：`IFLYTEK_APP_ID`、`IFLYTEK_API_KEY`、`IFLYTEK_API_SECRET` 等
- **出站网络**：选 `PUBLIC`（需访问讯飞公网 API）
- **访问方式/入站网关**：创建后获取访问域名

### 5. 调用运行时

```bash
curl -X POST https://{gateway_domain}/runtimes/{runtime_name}/invocations \
  -H "X-Hw-Agentarts-Session-Id: sess-001" \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"audio_url": "https://example.com/test.wav"}'
```

### （可选）大文件先上传再转写

```bash
# 经平台 API 上传音频到运行时内
curl -X POST https://{gateway_domain}/runtimes/{runtime_name}/upload-files \
  -H "Authorization: Bearer <API_KEY>" -F "file=@test.wav"
# 再以文件路径调用
curl -X POST https://{gateway_domain}/runtimes/{runtime_name}/invocations \
  -H "Authorization: Bearer <API_KEY>" -H "X-Hw-Agentarts-Session-Id: s1" \
  -H "Content-Type: application/json" -d '{"file_path": "/path/to/test.wav"}'
```

## 实现说明

相对讯飞官方示例 `Ifasr_llm/Ifasr.py` 的修正：

1. **getResult 参数对齐文档**：补上文档要求必传的 `resultType=transfer`（原示例漏传），移除多余的 `appId`/`ts`。
2. **signatureRandom 复用**：上传与查询使用同一个随机串（文档要求）。
3. **dateTime 重新生成**：查询时重新生成时间戳（文档要求）。
4. **客户端可复用**：不再在初始化时绑定单个音频文件。
5. **结果解析增强**：容错处理多层嵌套 JSON 的额外转义。

## 约束

- 仅支持 **WAV** 格式（用 Python 内置 `wave` 模块计算时长，无额外依赖）。
- 采样率 16kHz/8kHz、位长 16bit、单声道；时长 ≤ 5 小时、文件 ≤ 500MB。
- 输出为**纯文本**。
- 镜像需 **ARM64**（`agentarts launch` 自动处理）。
