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

能力声明：chat_completions=True, responses_api=True, responses_get_fetch=True
"""
import logging
import uuid

from async_tasks import AsyncTaskStore
from iflytek_asr import TimingInfo

logger = logging.getLogger("operation_router")

CAPABILITIES = {
    "chat_completions": True,
    "responses_api": True,
    "responses_get_fetch": True,
}

_VALID_OPERATIONS = ("query_capabilities", "chat_completions",
                     "create_response", "fetch_response")


def validate_capabilities() -> None:
    """启动自检：get_fetch=true 必须 api=true。"""
    if CAPABILITIES["responses_get_fetch"] and not CAPABILITIES["responses_api"]:
        raise RuntimeError("capabilities 非法：responses_get_fetch=true 要求 responses_api=true")


def _error(code: str, message: str, error_type: str, http_status: int) -> tuple[dict, int]:
    return {"error": {"code": code, "message": message, "type": error_type}}, http_status


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


def _openai_response(response_id: str, text: str) -> dict:
    return {
        "id": response_id,
        "object": "response",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }


def _chat_completion(text: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }


def handle_invocation(payload, task_store: AsyncTaskStore, owner=None,
                      transcribe_fn=None) -> tuple[dict, int]:
    """operation 路由入口。返回 (响应体, HTTP状态码)。

    Args:
        payload: invocation 请求体 {inputs: {operation, file_url, model_config, response_id}}
        task_store: 异步任务表
        owner: 调用方标识（任务归属校验）
        transcribe_fn: 同步转写函数 (request) -> str
    """
    if not isinstance(payload, dict):
        return _error("E4001", "请求体必须是 JSON 对象", "invalid_request", 400)

    # 解包统一 inputs 层（唯一格式，必须有 inputs 字段）
    inputs = _unwrap_inputs(payload)
    if not inputs:
        return _error("E4001", "请求体缺少 inputs 字段或格式不正确", "invalid_request", 400)

    # 缺省 operation 默认 chat_completions
    operation = inputs.get("operation") or "chat_completions"

    # 校验1：operation 合法性
    if operation not in _VALID_OPERATIONS:
        return _error("E4001", f"未知的 operation: {operation}", "invalid_request", 400)

    # query_capabilities 元操作
    if operation == "query_capabilities":
        return {"capabilities": dict(CAPABILITIES)}, 200

    # chat_completions：同步转写
    if operation == "chat_completions":
        if not CAPABILITIES["chat_completions"]:
            return _error("E4007", "本 agent 未声明 chat_completions 能力",
                          "capability_not_supported", 400)
        request = _extract_request(inputs)
        if not request.get("file_url"):
            return _error("E4001", "chat_completions 缺少必填参数 inputs.file_url",
                          "invalid_request", 400)
        if transcribe_fn is None:
            return _error("E5001", "未注入同步转写函数", "internal_error", 500)
        try:
            timing = TimingInfo()
            text = transcribe_fn(request, timing=timing)
        except Exception as e:
            return _error("E5001", f"转写失败: {e}", "transcribe_failed", 500)
        timing.log_summary(label="sync")
        result = _chat_completion(text)
        # 讯飞不支持流式，但主 Agent 以 SSE 流式调用。
        # 将非流式结果包装为 SSE 格式返回，使主 Agent 能正常解析。
        return {"__sse_stream__": True, "body": result}, 200

    # create_response：创建异步转写任务
    if operation == "create_response":
        if not CAPABILITIES["responses_api"]:
            return _error("E4007", "本 agent 未声明 responses_api 能力",
                          "capability_not_supported", 400)
        request = _extract_request(inputs)
        if not request.get("file_url"):
            return _error("E4001", "create_response 缺少必填参数 inputs.file_url",
                          "invalid_request", 400)
        response_id = task_store.create(request, owner=owner)
        return {"response_id": response_id, "status": "in_progress"}, 200

    # fetch_response：查询异步任务
    if not CAPABILITIES["responses_get_fetch"]:
        return _error("E4007", "本 agent 未声明 responses_get_fetch 能力",
                      "capability_not_supported", 400)
    response_id = inputs.get("response_id")
    if not response_id or not isinstance(response_id, str):
        return _error("E4001", "fetch_response 缺少必填参数 inputs.response_id",
                      "invalid_request", 400)
    task = task_store.fetch(response_id, owner=owner)
    if task is None:
        return _error("E4006", "响应不存在或已过期", "response_not_found", 404)
    if task["status"] == "completed":
        return _openai_response(response_id, task["text"] or ""), 200
    if task["status"] == "failed":
        return ({"id": response_id, "object": "response", "status": "failed",
                 "error": {"code": "E5001", "message": task["error"],
                           "type": "transcribe_failed"}}, 200)
    return {"id": response_id, "object": "response", "status": "in_progress"}, 200