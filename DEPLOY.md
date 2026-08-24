# 部署到 AgentArts 指南

本文档说明如何把录音文件转写智能体部署到华为云 AgentArts（智果）平台的高代码运行时。

## 部署模型

```
Mac ──rsync──> 鲲鹏 ECS(ARM64) ──agentarts launch──> 构建 ARM64 镜像
                                                        │
                                          推送 SWR ──────┤
                                                        ▼
                                              AgentArts 运行时(云端)
                                                        │
                                    平台注入讯飞密钥环境变量 + PUBLIC 公网出口
                                                        ▼
                                          /invocations 对外提供服务
```

`agentarts launch` 一条命令自动完成「构建 ARM64 镜像 → 推送 SWR → 部署运行时」。

## 前置条件

- **华为云账号**，且有 AgentArts 与 SWR 使用权限
- **华为云 AK/SK**：控制台 → 右上角头像 →「我的凭证」→「访问密钥」→ 新增并下载 CSV（仅能下载一次）
- **讯飞密钥**：`IFLYTEK_APP_ID` / `IFLYTEK_API_KEY` / `IFLYTEK_API_SECRET`（本项目 `.env` 已配置）

---

## 一、购买鲲鹏 ECS

华为云控制台 → ECS → 购买实例：

| 配置项 | 取值 | 说明 |
|---|---|---|
| 区域 | 西南-贵阳一（cn-southwest-2） | 与 AgentArts 同区域 |
| 架构 | **鲲鹏 ARM64** | 关键：X86 镜像部署后调用会失败 |
| 镜像 | Ubuntu 22.04 | |
| 规格 | 2 vCPU / 4GB 起步 | 构建镜像够用 |
| 公网 IP | 必须购买 | SSH、拉基础镜像、推 SWR 都需要 |
| 安全组 | 放通 22 端口 | 供 SSH |

记下 **ECS 公网 IP** 与登录密码/密钥。

## 二、首次部署（Mac + ECS 协同）

### 1. 上传代码并远程初始化环境（在 Mac 执行）

```bash
cd /Users/zhangqiwen/work/agent/asr_agent

# 上传代码 + 远程装环境 + 装依赖（首次会自动跑 init-ecs.sh）
bash scripts/deploy.sh root@<ECS公网IP>
```

`deploy.sh` 会：rsync 上传代码 → 在 ECS 上装 Python/Docker/镜像加速/SDK → 装项目依赖。
脚本结束后会打印后续手动步骤。

### 2. SSH 进 ECS 配置凭证并部署（在 ECS 执行）

```bash
ssh root@<ECS公网IP>
cd ~/asr_agent
source ~/venv/bin/activate

# 华为云 AK/SK（建议写入 ~/.bashrc 持久化）
export HUAWEICLOUD_SDK_AK='你的AK'
export HUAWEICLOUD_SDK_SK='你的SK'

# 首次配置（交互式）
agentarts configure --entrypoint app:app
```

交互提示填写：
- 智能体名称：`asr-agent`（小写字母开头，可含数字、中划线）
- 部署区域：`cn-southwest-2`（目前仅支持此区域）
- 依赖文件：`requirements.txt`
- SWR 镜像组织名：用默认；若自定义，需先在 SWR 控制台（贵阳一）创建同名组织

然后部署：

```bash
agentarts launch
```

成功后，AgentArts 控制台「部署运行 → 智能体运行时」可见 `asr-agent`。

## 三、控制台配置运行时

进入 `asr-agent` 运行时详情：

1. **环境变量**（必填，运行时用这些调讯飞 API）：
   - `IFLYTEK_APP_ID=5cefcf5d`
   - `IFLYTEK_API_KEY=2acb757703aa18fba126a5a0b83ff8ef`
   - `IFLYTEK_API_SECRET=YTliMzFkNzVjMzcyMjY1MTFlYzc4Njcz`

2. **出站网络**：选 `PUBLIC`（运行时需走公网访问讯飞 API）

3. **访问方式 / 入站网关**：创建一个，拿到**访问域名**（gateway_domain）

## 四、获取调用凭证

运行时详情 →「权限与访问控制」→ 访问 URN → 跳转 **AgentIdentity** 服务 → 复制 **API Key**。

## 五、调用验证

```bash
curl -X POST https://{gateway_domain}/runtimes/asr-agent/invocations \
  -H "X-Hw-Agentarts-Session-Id: sess-001" \
  -H "Authorization: Bearer {API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"audio_url": "https://某公网地址/test.wav"}'
```

成功响应：
```json
{"response": "转写文本...", "status": "success"}
```

也支持传运行时内文件路径（先经 `ExecuteRuntimeUploadFiles` 上传音频）：
```json
{"file_path": "/path/to/test.wav"}
```

---

## 六、后续更新代码

代码改完后，在 Mac 上一条命令重新上传并部署（`configure` 只需首次做一次）：

```bash
bash scripts/deploy.sh root@<ECS公网IP> --launch
```

会自动 rsync 新代码 → 远程 `pip install -r requirements.txt` → `agentarts launch`。

## 七、本地 Mac 直接部署（可选替代方案）

Apple Silicon Mac 本身是 arm64，也可不买 ECS 直接本地部署：

1. 安装 [Docker Desktop for Mac (Apple Silicon)](https://www.docker.com/products/docker-desktop/)
2. Docker → Settings → Docker Engine 加镜像加速：
   ```json
   {"registry-mirrors": ["https://docker.m.daocloud.net", "https://mirror.baidubce.com"]}
   ```
3. Docker 27+ 需 `export DOCKER_BUILDKIT=0`
4. `pip install agentarts-sdk` → 配 AK/SK → `agentarts configure --entrypoint app:app` → `agentarts launch`

> 注：官方文档以 Linux ARM64 为准，Mac 路径未在文档中明说，但架构合规，通常可行。

---

## 八、常见问题

| 现象 | 原因 / 解决 |
|---|---|
| `agentarts launch` AK/SK 认证报错 | AK/SK 未设或无权限；确认已 `export` 且账号有 AgentArts+SWR 权限 |
| `Organization 'xxx' already exists` | SWR 组织名已存在，换名或复用已有组织 |
| `403 Insufficient permission` | IAM 子账号未授权 SWR，需主账号授权或用主账号 AK/SK |
| 推送镜像超时 / `docker.io` 拉取失败 | Docker 镜像加速未配，见 `init-ecs.sh` 第 3 步 |
| 部署后调用失败 | 镜像非 ARM64（用 X86 机器构建了）；或出站网络没选 PUBLIC |
| 讯飞返回 `000002 accessKeyId not exist` | 录音文件转写大模型服务未开通，去讯飞控制台开通 |
| OCI 镜像格式报错 | Docker 27+，执行 `export DOCKER_BUILDKIT=0` 后重试 |

## 九、成本提示

- ECS 用完可**停机不删除**（停机不计计算资源费，仅收磁盘费），下次更新镜像再开机。
- AgentArts 运行时按实际调用计费，详见平台计费说明。
- 讯飞转写按时长计费，注意免费额度。
