# -*- coding: utf-8 -*-
"""operation 路由（统一 inputs 包装格式，2026-08-19 联调对齐）。

请求格式（唯一格式，不兼容旧版扁平格式）：
  {
    "inputs": {
      "operation": "query_capabilities | chat_completions | create_response | fetch_response",
      "query": "...",              （文本类 agent 用，转写不用）
      "image_url": ["..."],        （图片类 agent 用，转写不用）
      "file_url": ["..."],         （录音文件 URL 数组，取第一个）
      "model_config": [...],
      "response_id": "resp_xxx"    （fetch_response 用）
    }
  }

能力声明：chat_completions=True, create_response=True, fetch_response=True
"""
import json
import logging
import uuid

from async_tasks import AsyncTaskStore
from iflytek_asr import TimingInfo, InvalidAudioError

logger = logging.getLogger("operation_router")

CAPABILITIES = {
    "chat_completions": True,
    "create_response": True,
    "fetch_response": True,
}

_VALID_OPERATIONS = ("query_capabilities", "chat_completions",
                     "create_response", "fetch_response")


def validate_capabilities() -> None:
    """启动自检：fetch_response=true 必须 create_response=true。"""
    if CAPABILITIES["fetch_response"] and not CAPABILITIES["create_response"]:
        raise RuntimeError("capabilities 非法：fetch_response=true 要求 create_response=true")


def _error(code: str, message: str, error_type: str, http_status: int) -> tuple[dict, int]:
    """统一错误格式：顶层 error_code + error_msg，方便主 Agent 解析。"""
    logger.error("[%s] %s (type=%s, http=%d)", code, message, error_type, http_status)
    return {"error_code": code, "error_msg": message}, http_status


def _unwrap_inputs(payload: dict) -> dict:
    """解包统一格式的 inputs 层。返回内部字段 dict，缺失则返回空 dict。"""
    inputs = payload.get("inputs")
    if isinstance(inputs, dict):
        return inputs
    return {}


def _extract_request(inputs: dict) -> dict:
    """从 inputs 提取转写请求。

    - file_url 是数组，取第一个元素（暂不支持多文件）
    - model_config 在 inputs 内部
    - 兼容 file_url 直接传字符串的写法
    """
    result = {}

    file_url = inputs.get("file_url")
    if isinstance(file_url, list) and file_url:
        first = file_url[0]
        if isinstance(first, str) and first.strip():
            result["file_url"] = first.strip()
    elif isinstance(file_url, str) and file_url.strip():
        result["file_url"] = file_url.strip()

    mc = inputs.get("model_config")
    if isinstance(mc, list):
        result["model_config"] = mc

    return result


def _openai_response(response_id: str, text: str, task: dict) -> dict: