#!/bin/bash
# ============================================================
# 从 Mac 一键上传代码到鲲鹏 ECS 并（可选）触发 AgentArts 部署
#
# 用法:
#   首次部署:   bash scripts/deploy.sh root@<ECS公网IP>
#   更新并部署: bash scripts/deploy.sh root@<ECS公网IP> --launch
#   强制重装环境: bash scripts/deploy.sh root@<ECS公网IP> --init
#
# 前提: Mac 已配置好到 ECS 的 SSH 免密登录，或会交互式询问密码
# ============================================================
set -euo pipefail

# ---------- 参数解析 ----------
ECS_HOST="${1:?用法: bash scripts/deploy.sh <user@ip> [--launch] [--init]}"
shift || true

LAUNCH=0
FORCE_INIT=0
for arg in "$@"; do
  case "$arg" in
    --launch) LAUNCH=1 ;;
    --init)   FORCE_INIT=1 ;;
    *) echo "未知参数: $arg"; exit 1 ;;
  esac
done

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_DIR="~/asr_agent"

echo "============================================================"
echo " 本地项目: $PROJECT_DIR"
echo " 远程主机: $ECS_HOST"
echo " 远程目录: $REMOTE_DIR"
echo "============================================================"

# ---------- 1. 上传代码 ----------
echo ""
echo ">>> [1/3] rsync 上传代码到 ECS ..."
rsync -av --delete \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='venv/' \
  --exclude='.venv/' \
  --exclude='.git/' \
  --exclude='.agentarts_config.yaml' \
  --exclude='Dockerfile' \
  "$PROJECT_DIR/" "$ECS_HOST:$REMOTE_DIR/"

# ---------- 2. 远程环境初始化（首次或 --init）----------
NEED_INIT=$FORCE_INIT
if [ "$FORCE_INIT" -eq 0 ]; then
  # 检测远程是否已有 venv
  if ssh "$ECS_HOST" "test -d ~/venv" 2>/dev/null; then
    NEED_INIT=0
  else
    NEED_INIT=1
  fi
fi

if [ "$NEED_INIT" -eq 1 ]; then
  echo ""
  echo ">>> [2/3] 首次部署，远程初始化环境（装 Python/Docker/SDK）..."
  ssh "$ECS_HOST" "cd $REMOTE_DIR && bash scripts/init-ecs.sh"
  echo ""
  echo "!! 环境初始化完成。请重新运行本脚本以使 docker 组生效后再部署："
  echo "   bash scripts/deploy.sh $ECS_HOST --launch"
  echo "   （首次还需先 SSH 进 ECS 执行 agentarts configure，见 DEPLOY.md 第二节）"
  exit 0
fi

# ---------- 3. 远程安装依赖 ----------
echo ""
echo ">>> [2/3] 远程安装项目依赖 ..."
ssh "$ECS_HOST" "cd $REMOTE_DIR && source ~/venv/bin/activate && pip install -r requirements.txt"

# ---------- 4. 触发部署（--launch）----------
if [ "$LAUNCH" -eq 1 ]; then
  echo ""
  echo ">>> [3/3] 远程触发 agentarts launch ..."
  ssh "$ECS_HOST" "cd $REMOTE_DIR && source ~/venv/bin/activate && agentarts launch"
  echo ""
  echo "============================================================"
  echo " 部署完成！前往 AgentArts 控制台查看运行时。"
  echo "============================================================"
else
  echo ""
  echo "============================================================"
  echo " 代码已上传、依赖已安装。"
  echo ""
  echo " 首次部署请 SSH 进 ECS 执行："
  echo "   ssh $ECS_HOST"
  echo "   cd ~/asr_agent && source ~/venv/bin/activate"
  echo "   export HUAWEICLOUD_SDK_AK='你的AK'"
  echo "   export HUAWEICLOUD_SDK_SK='你的SK'"
  echo "   agentarts configure --entrypoint app:app"
  echo "   agentarts launch"
  echo ""
  echo " 后续更新代码并重新部署："
  echo "   bash scripts/deploy.sh $ECS_HOST --launch"
  echo "============================================================"
fi
