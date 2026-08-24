# -*- coding: utf-8 -*-
"""AgentArts 高代码运行时入口（FE2026081400138 spec 版对齐）。

薄封装：仅做 SDK 接入，路由逻辑在 operation_router.py（无 SDK 依赖，可本地测试）。

  - POST /invocations   @app.entrypoint -> handle_invocation 按 operation 路由
  - GET  /ping          健康检查（SDK 默认）

operation 路由（capabilities：同步+异步都支持）：
  - query_capabilities -> 200 SSE 事件，responseContent=能力字典字符串
  - chat_completions（含无 operation 默认）-> 200 SSE 事件，responseContent=转写文本
  - create_response    -> 200 SSE 事件，responseContent={response_id, status}
  - fetch_response     -> 200 SSE 事件，responseContent=转写文本/失败/进行中；404 E4006
  - 未知 operation      -> 400 E4001

200 成功响应统一包装为 SSE 事件流（workflow_started -> message -> workflow_finished
-> end），文本放进 data.outputs.responseContent，主 Agent 统一从该字段解析（与
AgentArts 低码工作流运行时一致）。

HTTP 状态码对齐 spec 错误码表（E4001/E4007->400，E4006->404）：
  路由返回 (body, status) 二元组；非 200 时用 Starlette JSONResponse 返回真实状态码。
"""
import os

from agentarts.sdk import AgentArtsRuntimeApp, RequestContext

from async_tasks import AsyncTaskStore
from operation_router import handle_invocation, validate_capabilities
from service import transcribe_from_payload

# 实例化平台运行时应用（入口对象，供 agentarts configure --entrypoint app:app 使用）
app = AgentArtsRuntimeApp()

# 启动时能力自检（spec 5.18.1 规则6）
validate_capabilities()

# 异步任务表（runner 注入转写函数；运行时重启任务即失，设计接受的安全失败）
task_store = AsyncTaskStore(runner=transcribe_from_payload)


@app.entrypoint
def handler(payload: dict, context: RequestContext = None):
    """AgentArts 平台标准 HTTP 暴露入口。

    使用同步 def：SDK 在线程池执行，create_response 仅创建任务立即返回，
    转写由后台线程执行，不阻塞事件循环。

    返回：200 时直接返回 dict（SDK 序列化为 JSON）；
    非 200（400/404）时返回 Starlette JSONResponse 以携带真实 HTTP 状态码。
    """
    # owner 用于任务归属校验（response_id 防跨用户访问）；context 无 user_id 时跳过
    owner = getattr(context, "user_id", None) if context else None
    # transcribe_fn：chat_completions 同步转写使用；task_store：异步任务使用
    body, status = handle_invocation(payload, task_store, owner=owner,
                                     transcribe_fn=transcribe_from_payload)
    if status == 200:
        # 讯飞不支持流式转写，但主 Agent 以 SSE 流式调用。
        # 将非流式结果包装为 SSE 格式（单事件），使主 Agent 能正常解析。
        if isinstance(body, dict) and body.get("__sse_stream__"):
            from starlette.responses import StreamingResponse
            import json as _json
            import time
            import uuid
            # 主 Agent 以 SSE 流式调用，期望与 AgentArts 工作流运行时一致的
            # 事件序列，最终从 data.outputs.responseContent 取结果文本。
            # 只有我们实际拥有的字段（没有的不编造）。
            # response_content 由 operation_router 按 operation 生成：
            #   转写类 -> 纯文本；能力/任务类 -> 字符串化字典。
            text = body.get("response_content", "") or ""
            start_ms = int(time.time() * 1000)
            exec_id = uuid.uuid4().hex

            def _event(ev, payload):
                return f"data: {_json.dumps({'event': ev, 'data': payload,
                                             'createdTime': int(time.time() * 1000)}, ensure_ascii=False)}\n\n"

            async def event_stream():
                yield _event("workflow_started", {"start_time": start_ms})
                yield _event("message", {"text": text, "index": 1,
                                         "node_type": "End", "node_name": "结束"})
                yield _event("workflow_finished", {
                    "status": {"code": 0, "desc": "succeeded"},
                    "outputs": {"responseContent": text},
                    "start_time": start_ms, "end_time": int(time.time() * 1000),
                    "execution_id": exec_id,
                })
                yield _event("end")
            return StreamingResponse(event_stream(), media_type="text/event-stream")
        return body
    from starlette.responses import JSONResponse  # agentarts-sdk 基于 Starlette，运行时可用
    return JSONResponse(body, status_code=status)


if __name__ == "__main__":
    # 平台托管时通过 AGENT_RUN_PORT 注入端口，默认 8080
    run_port = int(os.getenv("AGENT_RUN_PORT", "8080"))
    app.run(port=run_port)
