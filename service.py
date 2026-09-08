# -*- coding: utf-8 -*-
"""转写服务：解析请求 payload，通过 ConvAIAgent 网关调讯飞转写。

请求格式（FE2026080500089）：
  {
    "input": {"file_url": "录音文件URL"},
    "model_config": [
      {"type": "offline_asr_upload",     "endpoint": "https:///v2/upload",    "auth_token": ""},
      {"type": "offline_asr_get_result", "endpoint": "https:///v2/getResult", "auth_token": ""}
    ]
  }

仅支持网关路径（Bearer 认证 + JSON 协议）：
file_url 直接交给网关，网关用 audioMode=urlLink 让讯飞拉取，无需本地下载。
不支持直调讯飞（无环境变量密钥路径）。
"""
import logging
import time

from config import settings
from gateway_asr import GatewayAsrClient
from iflytek_asr import TimingInfo

logger = logging.getLogger("transcribe_service")


class TranscribeError(Exception):
    """转写业务异常（服务端问题，E5001）。"""

    pass


# ---------- payload 解析 ----------


def _extract_file_url(payload) -> str | None:
    """从 payload 提取音频 URL。"""
    if isinstance(payload, str):
        s = payload.strip()
        return s if s.lower().startswith(("http://", "https://")) else None
    if isinstance(payload, dict):
        # 顶层 file_url（operation_router 提取后的标准格式）
        if payload.get("file_url"):
            return payload["file_url"]
        # 新格式 input.file_url
        inp = payload.get("input")
        if isinstance(inp, dict) and inp.get("file_url"):
            return inp.file_url
    return None


def _extract_model_config(payload) -> tuple[str | None, str | None, str | None]:
    """从 model_config 数组中提取上传和查询两个 endpoint。

    返回 (upload_url, result_url, auth_token)。
    auth_token 从任一条目取（两条应一致）。
    """
    if not isinstance(payload, dict):
        return None, None, None
    configs = payload.get("model_config")
    if not isinstance(configs, list):
        return None, None, None

    upload_url = None
    result_url = None
    auth_token = None
    for cfg in configs:
        if not isinstance(cfg, dict):
            continue
        cfg_type = cfg.get("type")
        if cfg_type == "offline_asr_upload":
            upload_url = cfg.get("endpoint")
            auth_token = auth_token or cfg.get("auth_token")
        elif cfg_type == "offline_asr_get_result":
            result_url = cfg.get("endpoint")
            auth_token = auth_token or cfg.get("auth_token")
    return upload_url, result_url, auth_token


# ---------- 主入口 ----------

def transcribe_from_payload(payload, timing: TimingInfo = None) -> dict:
    """解析 payload -> 通过网关转写 -> 返回 {"text": 纯文本, "sentences": 分段结果}。

    仅支持网关路径（需完整 model_config），不支持直调讯飞。
    如果传入 timing=TimingInfo()，会记录完整的耗时分解（agent + gateway）。
    """
    t_agent_start = time.time()

    # 解析 model_config
    upload_url, result_url, auth_token = _extract_model_config(payload)
    if not (upload_url and result_url and auth_token):
        raise TranscribeError(
            "缺少 model_config（需含 offline_asr_upload 与 offline_asr_get_result 的 endpoint 和 auth_token）。"
            " 示例：{\"input\":{\"file_url\":\"https://.../x.mp3\"},\"model_config\":[...]}"
        )

    # 提取音频 URL
    audio_source = _extract_file_url(payload)
    if not audio_source:
        raise TranscribeError(
            "缺少音频 URL，需提供 input.file_url。"
            " 示例：{\"input\":{\"file_url\":\"https://.../x.mp3\"},\"model_config\":[...]}"
        )

    if timing is not None:
        timing.agent_overhead = time.time() - t_agent_start

    try:
        client = GatewayAsrClient(
            upload_url=upload_url,
            result_url=result_url,
            auth_token=auth_token,
            poll_interval=settings.poll_interval,
            poll_max_wait=settings.poll_max_wait,
        )
        return client.transcribe(
            audio_source, language=settings.language, pd=settings.pd,
            timing=timing,
        )
    except TranscribeError:
        raise
    except TimeoutError as e:
        raise TranscribeError(str(e)) from e
    except Exception as e:
        raise TranscribeError(str(e)) from e
