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
from iflytek_asr import TimingInfo

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
    """completed 状态的完整 OpenAI response 格式。"""
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(task["created_at"]),
        "completed_at": int(task.get("completed_at") or task["created_at"]),
        "status": "completed",
        "model": None,
        "error": None,
        "output": [
            {
                "id": f"msg_{uuid.uuid4()}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "metadata": None,
    }


def _in_progress_response(response_id: str, task: dict) -> dict:
    """in_progress 状态的 OpenAI response 格式（create_response / fetch_response 共用）。"""
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(task["created_at"]),
        "completed_at": None,
        "status": "in_progress",
        "model": None,
        "error": None,
        "output": [],
        "metadata": None,
    }


def _failed_response(response_id: str, task: dict) -> dict:
    """failed 状态的 OpenAI response 格式（error_code/error_msg 统一字段）。"""
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(task["created_at"]),
        "completed_at": int(task.get("completed_at") or task["created_at"]),
        "status": "failed",
        "model": None,
        "error_code": "E5001",
        "error_msg": task["error"],
        "output": [],
        "metadata": None,
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


def _sse(body: dict, response_content: str) -> dict:
    """包装为 SSE 事件响应（配合调用处 ", 200"）。

    App 层把 response_content 放进 data.outputs.responseContent，
    主 Agent 统一从该字段解析（与 AgentArts 低码工作流运行时一致）。
    body 保留结构化响应体（测试/日志用）；结构化内容用 str() 转字符串，
    与平台工作流返回的字符串化字典风格一致。
    """
    return {"__sse_stream__": True, "body": body, "response_content": response_content}


def handle_invocation(payload, task_store: AsyncTaskStore, owner=None,
                      transcribe_fn=None) -> tuple[dict, int]:
    """operation 路由入口。返回 (响应体, HTTP状态码)。

    Args:
        payload: invocation 请求体 {inputs: {operation, file_url, model_config, response_id}}
        task_store: 异步任务表
        owner: 调用方标识（任务归属校验）
        transcribe_fn: 同步转写函数 (request) -> str
    """
    logger.info("收到请求: %s", json.dumps(payload, ensure_ascii=False)[:2000])

    if not isinstance(payload, dict):
        return _error("E4001", "请求体必须是 JSON 对象", "invalid_request", 400)

    # 解包统一 inputs 层（唯一格式，必须有 inputs 字段）
    inputs = _unwrap_inputs(payload)
    if not inputs:
        return _error("E4001", f"请求体缺少 inputs 字段或格式不正确, payload={payload}", "invalid_request", 400)

    # 缺省 operation 默认 chat_completions
    operation = inputs.get("operation") or "chat_completions"

    # 校验1：operation 合法性
    if operation not in _VALID_OPERATIONS:
        return _error("E4001", f"未知的 operation: {operation}, inputs={inputs}", "invalid_request", 400)

    # query_capabilities 元操作
    if operation == "query_capabilities":
        caps = {"capabilities": dict(CAPABILITIES)}
        logger.info("query_capabilities: %s", caps)
        return _sse(caps, str(caps)), 200

    # chat_completions：同步转写
    if operation == "chat_completions":
        if not CAPABILITIES["chat_completions"]:
            return _error("E4007", "本 agent 未声明 chat_completions 能力",
                          "capability_not_supported", 400)
        request = _extract_request(inputs)
        if not request.get("file_url"):
            return _error("E4001", f"chat_completions 缺少必填参数 inputs.file_url, inputs={inputs}",
                          "invalid_request", 400)
        if transcribe_fn is None:
            return _error("E5001", "未注入同步转写函数", "internal_error", 500)
        try:
            timing = TimingInfo()
            text = transcribe_fn(request, timing=timing)
        except Exception as e:
            logger.exception("转写异常: file_url=%s", request.get("file_url"))
            return _error("E5001", f"转写失败: {e}", "transcribe_failed", 500)
        timing.log_summary(label="sync")
        result = _chat_completion(text)
        logger.info("chat_completions 成功: text_len=%d, file_url=%s", len(text), request.get("file_url"))
        # 讯飞不支持流式，但主 Agent 以 SSE 流式调用。
        # 将非流式结果包装为 SSE 格式返回，使主 Agent 能正常解析。
        # chat_completions: responseContent 需包一层 message（主 Agent 解析要求）
        return _sse(result, {"message": text}), 200

    # create_response：创建异步转写任务
    if operation == "create_response":
        if not CAPABILITIES["create_response"]:
            return _error("E4007", "本 agent 未声明 create_response 能力",
                          "capability_not_supported", 400)
        request = _extract_request(inputs)
        if not request.get("file_url"):
            return _error("E4001", f"create_response 缺少必填参数 inputs.file_url, inputs={inputs}",
                          "invalid_request", 400)
        # 优先复用主 Agent 传入的 response_id（保证 session 亲和路由到同一沙箱）
        response_id = task_store.create(request, owner=owner,
                                        response_id=inputs.get("response_id"))
        task = task_store.fetch(response_id, owner=owner)
        logger.info("create_response 成功: response_id=%s, file_url=%s", response_id, request.get("file_url"))
        # 异步操作直接返回 OpenAI response 格式 body，不包 SSE
        return _in_progress_response(response_id, task), 200

    # fetch_response：查询异步任务
    if not CAPABILITIES["fetch_response"]:
        return _error("E4007", "本 agent 未声明 fetch_response 能力",
                      "capability_not_supported", 400)
    response_id = inputs.get("response_id")
    if not response_id or not isinstance(response_id, str):
        return _error("E4001", f"fetch_response 缺少必填参数 inputs.response_id, inputs={inputs}",
                      "invalid_request", 400)
    task = task_store.fetch(response_id, owner=owner)
    if task is None:
        return _error("E4006", f"响应不存在或已过期, response_id={response_id}", "response_not_found", 404)
    if task["status"] == "completed":
        logger.info("fetch_response 完成: response_id=%s, text_len=%d", response_id, len(task["text"] or ""))
        # 异步操作直接返回 OpenAI response 格式 body，不包 SSE
        return _openai_response(response_id, task["text"] or "", task), 200
    if task["status"] == "failed":
        logger.warning("fetch_response 失败: response_id=%s, error=%s", response_id, task.get("error"))
        return _failed_response(response_id, task), 200
    logger.info("fetch_response 处理中: response_id=%s", response_id)
    return _in_progress_response(response_id, task), 200