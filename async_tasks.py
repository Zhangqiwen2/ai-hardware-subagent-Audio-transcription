# -*- coding: utf-8 -*-
"""异步转写任务管理（内存任务表，按 FE2026081400138 约束实现）。

create_response 创建任务后由后台线程执行转写，fetch_response 查询状态：
- response_id 格式：resp_{uuid4}
- 任务有效期 24h（惰性过期 + 每小时周期清理）
- 运行时重启任务即失（fetch 返回 404/E4006，设计接受的安全失败）

runner（转写执行函数）由构造方注入，便于测试替换。
"""
import logging
import threading
import time
import uuid

from iflytek_asr import TimingInfo, InvalidAudioError

logger = logging.getLogger("async_tasks")

TASK_TTL_SECONDS = 24 * 3600  # 24h 过期（E4006 约束）
MAX_TASKS = 1000              # 内存保护上限
_CLEANUP_INTERVAL = 3600      # 周期清理间隔（秒）

STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


class AsyncTaskStore:
    """内存任务表：response_id -> 任务状态。线程安全。"""

    def __init__(self, runner):
        """runner: 任务执行函数 (request_payload) -> str（转写文本）。"""
        self._runner = runner
        self._tasks = {}  # response_id -> {status, text, error, created_at, owner}
        self._lock = threading.Lock()
        self._start_cleanup_thread()

    def create(self, request_payload, owner=None, response_id=None) -> str:
        """创建任务：起后台线程执行 runner，立即返回 response_id。

        response_id: 外部传入则复用（主 Agent 保证 session 亲和路由），
                     不传则自动生成 resp_{uuid4}。
        """
        if response_id is None:
            response_id = f"resp_{uuid.uuid4()}"
        with self._lock:
            self._evict_if_full()
            self._tasks[response_id] = {
                "status": STATUS_IN_PROGRESS,
                "text": None,
                "error": None,
                "created_at": time.time(),
                "owner": owner,
            }
        threading.Thread(
            target=self._run, args=(response_id, request_payload), daemon=True
        ).start()
        return response_id

    def fetch(self, response_id, owner=None):
        """查询任务。返回任务信息 dict（status/text/error）。

        不存在 / 已过期(>24h) / owner 不匹配 -> None（调用方回 E4006）。
        """
        with self._lock:
            task = self._tasks.get(response_id)
            if task is None:
                return None
            if time.time() - task["created_at"] > TASK_TTL_SECONDS:
                del self._tasks[response_id]  # 惰性过期
                return None
            if owner is not None and task.get("owner") is not None and task["owner"] != owner:
                return None
            return dict(task)

    # ---------- 内部 ----------

    def _run(self, response_id, request_payload):
        """后台线程：执行转写并回写状态。"""
        timing = TimingInfo()
        try:
            result = self._runner(request_payload, timing=timing)