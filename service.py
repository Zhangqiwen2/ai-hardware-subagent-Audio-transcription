# -*- coding: utf-8 -*-
"""转写服务：解析请求 payload，定位音频，调讯飞转写，返回纯文本。

支持新设计（FE2026080500089）请求格式：
  {
    "input": {"file_url": "录音文件URL"},
    "model_config": [
      {"type": "offline_asr_upload",     "endpoint": "https:///v2/upload",    "auth_token": ""},
      {"type": "offline_asr_get_result", "endpoint": "https:///v2/getResult", "auth_token": ""}
    ]
  }

有 model_config 时走 ConvAIAgent 网关（Bearer 认证 + JSON 协议）：
file_url 直接交给网关，网关用 audioMode=urlLink 让讯飞拉取，无需本地下载。

向后兼容旧格式（本地测试，无 model_config）：
  {"file_path": "/data/x.wav"} / {"audio_url": "https://..."} 等
  走环境变量密钥直调讯飞，需本地下载二进制上传。
"""
import logging
import os
import tempfile
import time
import urllib.request
from urllib.parse import urlparse

from config import settings
from gateway_asr import GatewayAsrClient
from iflytek_asr import TimingInfo, XfyunAsrClient

logger = logging.getLogger("transcribe_service")

_URL_PREFIXES = ("http://", "https://")


class TranscribeError(Exception):
    """转写业务异常。"""

    pass


# ---------- payload 解析 ----------

def _extract_file_url(payload) -> str | None:
    """从 payload 提取音频 URL。

    新格式：input.file_url
    旧格式兼容：audio_url / message·prompt·input 为 URL
    """
    if isinstance(payload, str):
        s = payload.strip()
        return s if s.lower().startswith(_URL_PREFIXES) else None
    if isinstance(payload, dict):
        # 顶层 file_url（operation_router 提取后的标准格式）
        if payload.get("file_url"):
            return payload["file_url"]
        # 新格式 input.file_url
        inp = payload.get("input")
        if isinstance(inp, dict) and inp.get("file_url"):
            return inp["file_url"]
        # 旧格式 audio_url
        if payload.get("audio_url"):
            return payload["audio_url"]
        # 旧格式 message/prompt/input 字段为 URL
        for key in ("message", "prompt"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip().lower().startswith(_URL_PREFIXES):
                return val.strip()
    return None


def _extract_file_path(payload) -> str | None:
    """从 payload 提取本地文件路径（旧格式兼容，本地测试用）。"""
    if isinstance(payload, dict):
        if payload.get("file_path"):
            return payload["file_path"]
        for key in ("message", "prompt"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip() and not val.strip().lower().startswith(_URL_PREFIXES):
                return val.strip()
    return None


def _extract_model_config(payload) -> tuple[str | None, str | None, str | None]:
    """从 model_config 数组中提取上传和查询两个 endpoint。

    返回 (upload_url, result_url, auth_token)。
    新格式（两条独立配置，endpoint 是完整 URL，不拼接）：
      - type=offline_asr_upload     -> upload_url
      - type=offline_asr_get_result -> result_url
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


def _guess_suffix(file_url: str) -> str:
    """根据 URL 扩展名猜测音频文件后缀。"""
    ext = os.path.splitext(urlparse(file_url).path)[1]
    return ext or ".wav"


def _resolve_to_local(payload) -> tuple[str, bool]:
    """把 payload 解析为本地音频文件路径。返回 (local_path, is_tempfile)。"""
    file_path = _extract_file_path(payload)
    file_url = _extract_file_url(payload)

    if file_path:
        if not os.path.exists(file_path):
            raise TranscribeError(f"音频文件不存在：{file_path}")
        return file_path, False

    if file_url:
        os.makedirs(settings.tmp_dir, exist_ok=True)
        suffix = _guess_suffix(file_url)
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, dir=settings.tmp_dir, delete=False)
        tmp.close()
        try:
            urllib.request.urlretrieve(file_url, tmp.name)
        except Exception as e:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
            raise TranscribeError(f"下载音频失败：{e}") from e
        return tmp.name, True

    raise TranscribeError(
        "无法从 payload 解析音频来源，需提供 input.file_url 或 file_path。"
        " 示例：{\"input\":{\"file_url\":\"https://.../x.mp3\"},\"model_config\":[...]}"
    )


# ---------- 主入口 ----------

def transcribe_from_payload(payload, timing: TimingInfo = None) -> str:
    """解析 payload -> 定位音频 -> 转写 -> 返回纯文本。

    调用方式（二选一，按 payload 自动选择）：
    - 有 model_config（含 endpoint + auth_token）：走 ConvAIAgent 网关（Bearer 认证），
      直接把 file_url 交给网关（网关用 audioMode=urlLink 让讯飞拉取），无需本地下载
    - 无 model_config：过渡期直调讯飞（环境变量密钥，本地测试用），需本地下载二进制上传

    如果传入 timing=TimingInfo()，会记录完整的耗时分解（agent + iflytek）。
    """
    t_agent_start = time.time()

    # 解析 model_config：两条独立配置（upload + get_result），有则走网关
    upload_url, result_url, auth_token = _extract_model_config(payload)
    use_gateway = bool(upload_url and result_url and auth_token)

    # 网关路径直接传 URL；直调讯飞路径需先下载到本地
    if use_gateway:
        audio_source = _extract_file_url(payload)
        if not audio_source:
            raise TranscribeError(
                "网关转写缺少音频 URL，需提供 input.file_url。"
                " 示例：{\"input\":{\"file_url\":\"https://.../x.mp3\"},\"model_config\":[...]}"
            )
        local_path = None
        is_tmp = False
    else:
        local_path, is_tmp = _resolve_to_local(payload)
        audio_source = local_path

    if timing is not None:
        timing.agent_overhead = time.time() - t_agent_start

    try:
        if use_gateway:
            client = GatewayAsrClient(
                upload_url=upload_url,
                result_url=result_url,
                auth_token=auth_token,
                poll_interval=settings.poll_interval,
                poll_max_wait=settings.poll_max_wait,
            )
        else:
            # 过渡：直调讯飞（env 密钥），本地测试 / model_config 缺失时使用
            try:
                settings.validate()
            except RuntimeError as e:
                raise TranscribeError(str(e)) from e
            client = XfyunAsrClient(
                app_id=settings.app_id,
                access_key_id=settings.access_key_id,
                access_key_secret=settings.access_key_secret,
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
    finally:
        if is_tmp and local_path and os.path.exists(local_path):
            os.unlink(local_path)
