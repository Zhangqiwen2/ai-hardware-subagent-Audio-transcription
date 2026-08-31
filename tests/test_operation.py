# -*- coding: utf-8 -*-
"""operation 路由与异步任务测试（统一 inputs 包装格式版）。

覆盖：operation 路由、双层校验、错误码与 HTTP 状态码、异步全流程、
失败、过期、owner 归属、file_url 数组提取、OpenAI response 格式。

运行：python tests/test_operation.py
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from async_tasks import AsyncTaskStore, TASK_TTL_SECONDS
from operation_router import handle_invocation, CAPABILITIES, validate_capabilities


def make_store(runner):
    return AsyncTaskStore(runner=runner)


def call(payload, store, owner=None, transcribe_fn=None):
    body, status = handle_invocation(payload, store, owner=owner, transcribe_fn=transcribe_fn)
    # 解包 SSE 包装（如果存在）
    if isinstance(body, dict) and body.get("__sse_stream__"):
        body = body["body"]
    return body, status


def wait_status(store, response_id, status, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = store.fetch(response_id)
        if task and task["status"] == status:
            return task
        time.sleep(0.02)
    raise AssertionError(f"等待任务 {response_id} 变为 {status} 超时")


def main():
    # ---- 1. query_capabilities ----
    validate_capabilities()
    store = make_store(lambda req, **kw: "ok")
    body, status = call({"inputs": {"operation": "query_capabilities"}}, store)
    assert status == 200, status
    assert body == {"capabilities": {"chat_completions": True,
                                     "create_response": True,
                                     "fetch_response": True}}, body
    print("[1] query_capabilities -> 200 OK")

    # ---- 2. 未知 operation -> 400 E4001 ----
    body, status = call({"inputs": {"operation": "do_something"}}, store)
    assert status == 400 and body["error_code"] == "E4001", (status, body)
    print("[2] 未知 operation -> 400 E4001 OK")

    # ---- 3. 缺 inputs -> 400 E4001 ----
    body, status = call({"operation": "query_capabilities"}, store)
    assert status == 400 and body["error_code"] == "E4001", (status, body)
    print("[3] 缺 inputs 包装 -> 400 E4001 OK")

    # ---- 4. chat_completions 同步转写（file_url 数组）----
    sync_fn = lambda req, **kw: f"文本:{req['file_url']}"
    body, status = call({"inputs": {"operation": "chat_completions",
                                     "file_url": ["https://x/a.wav"]}},
                        store, transcribe_fn=sync_fn)
    assert status == 200, (status, body)
    assert body["object"] == "chat.completion", body
    assert body["choices"][0]["message"]["content"] == "文本:https://x/a.wav", body
    print("[4] chat_completions file_url数组 -> 200 chat.completion OK")

    # ---- 4b. file_url 字符串兼容 ----
    body, status = call({"inputs": {"operation": "chat_completions",
                                     "file_url": "https://x/b.wav"}},
                        store, transcribe_fn=sync_fn)
    assert status == 200 and "b.wav" in body["choices"][0]["message"]["content"], (status, body)
    print("[4b] file_url字符串兼容 OK")

    # ---- 4c. chat_completions 缺 file_url -> 400 E4001 ----
    body, status = call({"inputs": {"operation": "chat_completions"}},
                        store, transcribe_fn=sync_fn)
    assert status == 400 and body["error_code"] == "E4001", (status, body)
    print("[4c] 缺 file_url -> 400 E4001 OK")

    # ---- 4d. chat_completions 转写失败 -> 500 E5001 ----
    def sync_fail(req, **kw):
        raise RuntimeError("转写炸了")
    body, status = call({"inputs": {"operation": "chat_completions",
                                     "file_url": ["https://x/a.wav"]}},
                        store, transcribe_fn=sync_fail)
    assert status == 500 and body["error_code"] == "E5001", (status, body)
    print("[4d] 转写失败 -> 500 E5001 OK")

    # ---- 5. 异步全流程：create -> in_progress -> completed ----
    started = threading.Event()
    release = threading.Event()

    def slow_runner(req, **kw):
        started.set()
        release.wait(5)
        return f"异步文本:{req['file_url']}"

    store2 = make_store(slow_runner)
    body, status = call({"inputs": {"operation": "create_response",
                                     "file_url": ["https://x/a.wav"]}}, store2)
    assert status == 200 and body["status"] == "in_progress", (status, body)
    rid = body["id"]
    assert rid.startswith("resp_"), body

    assert started.wait(2), "后台任务未启动"
    body, status = call({"inputs": {"operation": "fetch_response",
                                     "response_id": rid}}, store2)
    assert status == 200 and body["status"] == "in_progress", (status, body)
    release.set()
    wait_status(store2, rid, "completed")
    body, status = call({"inputs": {"operation": "fetch_response",
                                     "response_id": rid}}, store2)
    assert status == 200, status
    assert body["id"] == rid and body["object"] == "response" and body["status"] == "completed", body
    assert body["output"][0]["content"][0]["text"] == "异步文本:https://x/a.wav", body
    print("[5] 异步全流程 + OpenAI response OK")

    # ---- 6. fetch 缺 response_id / 不存在 ----
    body, status = call({"inputs": {"operation": "fetch_response"}}, store2)
    assert status == 400 and body["error_code"] == "E4001", (status, body)
    body, status = call({"inputs": {"operation": "fetch_response",
                                     "response_id": "resp_not_exist"}}, store2)
    assert status == 404 and body["error_code"] == "E4006", (status, body)
    print("[6] 缺 response_id / 不存在 -> E4001/E4006 OK")

    # ---- 7. 任务失败 ----
    def fail_runner(req, **kw):
        raise RuntimeError("异步炸了")
    store3 = make_store(fail_runner)
    body, status = call({"inputs": {"operation": "create_response",
                                     "file_url": ["https://x/a.wav"]}}, store3)
    rid3 = body["id"]
    wait_status(store3, rid3, "failed")
    body, status = call({"inputs": {"operation": "fetch_response",
                                     "response_id": rid3}}, store3)
    assert status == 200 and body["status"] == "failed", (status, body)
    print("[7] 任务失败 -> failed OK")

    # ---- 8. 24h 过期 -> 404 E4006 ----
    store4 = make_store(lambda req, **kw: "ok")
    body, status = call({"inputs": {"operation": "create_response",
                                     "file_url": ["https://x/a.wav"]}}, store4)
    rid4 = body["id"]
    wait_status(store4, rid4, "completed")
    with store4._lock:
        store4._tasks[rid4]["created_at"] -= TASK_TTL_SECONDS + 1
    body, status = call({"inputs": {"operation": "fetch_response",
                                     "response_id": rid4}}, store4)
    assert status == 404 and body["error_code"] == "E4006", (status, body)
    print("[8] 24h 过期 -> 404 E4006 OK")

    # ---- 9. owner 归属校验 ----
    store5 = make_store(lambda req, **kw: "ok")
    body, status = call({"inputs": {"operation": "create_response",
                                     "file_url": ["https://x/a.wav"]}},
                        store5, owner="userA")
    rid5 = body["id"]
    body, status = call({"inputs": {"operation": "fetch_response",
                                     "response_id": rid5}}, store5, owner="userB")
    assert status == 404 and body["error_code"] == "E4006", (status, body)
    body, status = call({"inputs": {"operation": "fetch_response",
                                     "response_id": rid5}}, store5, owner="userA")
    assert status == 200, (status, body)
    print("[9] owner 归属校验 OK")

    # ---- 10. model_config 提取（inputs 内）----
    captured = []
    store6 = make_store(lambda req, **kw: captured.append(req) or "ok")
    body, status = call({"inputs": {"operation": "create_response",
                                     "file_url": ["https://x/a.mp3"],
                                     "model_config": [{"type": "offline_asr"}]}}, store6)
    rid6 = body["id"]
    wait_status(store6, rid6, "completed")
    req = captured[0]
    assert req["file_url"] == "https://x/a.mp3" and req["model_config"] == [{"type": "offline_asr"}], req
    print("[10] model_config 从 inputs 内提取 OK")

    # ---- 11. 空 file_url 数组 / 非数组 ----
    body, status = call({"inputs": {"operation": "chat_completions",
                                     "file_url": []}}, store, transcribe_fn=sync_fn)
    assert status == 400 and body["error_code"] == "E4001", (status, body)
    body, status = call({"inputs": {"operation": "chat_completions",
                                     "file_url": [None]}}, store, transcribe_fn=sync_fn)
    assert status == 400 and body["error_code"] == "E4001", (status, body)
    print("[11] 空/无效 file_url 数组 -> E4001 OK")

    # ---- 12. 非 dict 请求体 ----
    body, status = call("just a string", store)
    assert status == 400 and body["error_code"] == "E4001", (status, body)
    print("[12] 非 dict 请求体 -> 400 E4001 OK")

    print("\n全部测试通过（统一 inputs 格式版）✓")


if __name__ == "__main__":
    main()