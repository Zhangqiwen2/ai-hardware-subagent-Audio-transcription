# -*- coding: utf-8 -*-
"""配置模块：从环境变量读取讯飞密钥与运行参数。

本地开发时自动加载 .env 文件（若 python-dotenv 可用）；
AgentArts 运行时中环境变量由平台注入，无需 .env。
"""
import os
from dataclasses import dataclass, field

# 本地开发：尝试从 .env 加载环境变量（运行时无 dotenv 也不影响）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


@dataclass
class Settings:
    # 讯飞开放平台密钥（从控制台获取）
    app_id: str = field(default_factory=lambda: _env("IFLYTEK_APP_ID"))
    access_key_id: str = field(default_factory=lambda: _env("IFLYTEK_API_KEY"))
    access_key_secret: str = field(default_factory=lambda: _env("IFLYTEK_API_SECRET"))

    # 转写参数
    language: str = field(default_factory=lambda: _env("IFLYTEK_LANGUAGE", "autodialect"))
    pd: str = field(default_factory=lambda: _env("IFLYTEK_PD", ""))  # 领域参数，空表示不传

    # 轮询参数
    poll_interval: int = field(default_factory=lambda: int(_env("IFLYTEK_POLL_INTERVAL", "10")))
    poll_max_wait: int = field(default_factory=lambda: int(_env("IFLYTEK_POLL_MAX_WAIT", "3600")))  # 秒

    # 运行时端口（AgentArts 平台通过 AGENT_RUN_PORT 注入，默认 8080）
    port: int = field(default_factory=lambda: int(_env("AGENT_RUN_PORT", "8080")))

    # 临时文件目录（接收上传/下载音频时使用）
    tmp_dir: str = field(default_factory=lambda: _env("TMP_DIR", "/tmp/asr_agent"))

    def validate(self) -> None:
        """校验讯飞密钥是否已配置，缺失则抛出明确异常。"""
        missing = []
        if not self.app_id:
            missing.append("IFLYTEK_APP_ID")
        if not self.access_key_id:
            missing.append("IFLYTEK_API_KEY")
        if not self.access_key_secret:
            missing.append("IFLYTEK_API_SECRET")
        if missing:
            raise RuntimeError(
                "讯飞密钥未配置，请设置环境变量：" + ", ".join(missing)
                + "。可参考 .env.example。"
            )


settings = Settings()
