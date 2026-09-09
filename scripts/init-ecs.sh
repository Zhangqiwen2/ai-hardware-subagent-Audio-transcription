#!/bin/bash
# ============================================================
# AgentArts ECS 环境初始化脚本（Ubuntu / 鲲鹏 ARM64）
# 用法：在 ECS 上执行  bash init-ecs.sh
# 作用：装 Python3 + Docker + 镜像加速 + 虚拟环境 + agentarts-sdk
# ============================================================
set -e

echo "=== 1. 安装 Python3 / venv / pip ==="
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

echo ""
echo "=== 2. 安装 Docker ==="
if ! command -v docker &>/dev/null; then
    sudo apt install -y docker.io
    sudo systemctl enable --now docker
    sudo usermod -aG docker "$USER"
    echo "已将 $USER 加入 docker 组（需重新登录后生效，免 sudo 调 docker）"
else
    echo "Docker 已安装：$(docker --version)"
fi

echo ""
echo "=== 3. 配置 Docker 镜像加速（避免拉 python:3.10-slim 超时）==="
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.net",
    "https://mirror.baidubce.com"
  ]
}
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker

echo ""
echo "=== 4. Docker 27+ 兼容（SWR 基础版不支持 OCI 镜像格式）==="
grep -q 'DOCKER_BUILDKIT' ~/.bashrc || echo 'export DOCKER_BUILDKIT=0' >> ~/.bashrc

echo ""
echo "=== 5. 创建 Python 虚拟环境并安装 agentarts-sdk ==="
python3 -m venv ~/venv
source ~/venv/bin/activate
pip install --upgrade pip
pip install agentarts-sdk

echo ""
echo "============================================================"
echo "环境初始化完成！接下来："
echo "  1. 退出并重新登录 SSH（让 docker 组、DOCKER_BUILDKIT 生效）"
echo "  2. 上传项目代码后：cd ~/asr_agent && source ~/venv/bin/activate"
echo "  3. pip install -r requirements.txt"
echo "  4. export HUAWEICLOUD_SDK_AK='你的AK'"
echo "     export HUAWEICLOUD_SDK_SK='你的SK'"
echo "  5. agentarts configure --entrypoint app:app"
echo "  6. agentarts launch"
echo "============================================================"
