# -*- coding: utf-8 -*-
"""ConvAIAgent 网关转写客户端（Bearer 认证 + JSON 协议）。

按 2026-08-20 重构后的 RuntimeInvocationController 协议调用：
- POST /internal/v2/upload    请求体 {"audio_url": "...", "options": {...}}
- POST /internal/v2/getResult 请求体 {"order_id": "..."}

鉴权：Authorization: Bearer（model_config.auth_token 注入）。
网关内部用 audioMode=urlLink 让讯飞直接拉取 audio_url，故本客户端只传 URL，不再上传二进制。

响应是讯飞原始 JSON 透传（code/descInfo/content/orderId/orderInfo/orderResult），
本客户端仅做 code 校验与 orderId/orderInfo 提取，orderResult 解析复用 result_parser。
"""
import json
import logging
import time

import requests

from iflytek_asr import TimingInfo, raise_for_failtype, http_error_detail
from result_parser import parse_order_result

logger = logging.getLogger("gateway_asr")

# 订单状态（与讯飞一致）
STATUS_DONE = 4
STATUS_FAILED = -1


class GatewayAsrClient:
    """通过 ConvAIAgent 网关调讯飞离线转写的客户端（Bearer 认证 + JSON 协议）。

    upload_url 和 result_url 由调用方传入完整地址（不拼接），
    分别来自 model_config 中 type=offline_asr_upload 和 type=offline_asr_get_result 的 endpoint。
    """

    def __init__(
        self,
        upload_url: str,
        result_url: str,
        auth_token: str,
        poll_interval: int = 10,
        poll_max_wait: int = 3600,
    ):
        self.upload_url = upload_url
        self.result_url = result_url
        # auth_token 兼容带/不带 Bearer 前缀
        self.bearer = (
            auth_token if auth_token.strip().lower().startswith("bearer ")
            else f"Bearer {auth_token.strip()}"
        )
        self.poll_interval = poll_interval
        self.poll_max_wait = poll_max_wait

    # ---------- 上传 ----------

    def upload_audio(self, file_url: str, language: str = "autodialect", pd: str = "") -> str:
        """提交音频 URL 到 ConvAIAgent 网关，返回 orderId。

        网关用 audioMode=urlLink 让讯飞直接拉取 audio_url，故只传 URL 不传二进制。
        """
        if not file_url or not file_url.strip().lower().startswith(("http://", "https://")):
            raise ValueError(f"音频 URL 非法：{file_url}")

        options = {"language": language}
        if pd:
            options["pd"] = pd
        body = {"audio_url": file_url, "options": options}
        headers = {
            "Authorization": self.bearer,
            "Content-Type": "application/json",
        }
        logger.info("网关提交音频 URL：%s", file_url)

        try:
            resp = requests.post(self.upload_url, headers=headers, json=body, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            # 附上网关响应体（网关侧的具体报错原因，此前被 raise_for_status 丢掉）
            detail = http_error_detail(e)
            raise RuntimeError(f"网关上传请求失败：{e}，网关响应体：{detail}") from e
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"网关上传请求失败：{e}") from e

        result = self._parse_json(resp.text)
        if str(result.get("code")) != "000000":
            raise RuntimeError(f"网关上传失败：code={result.get('code')}, desc={result.get('descInfo')}")
        order_id = result["content"]["orderId"]
        logger.info("网关上传成功，订单ID=%s", order_id)
        return order_id

    # ---------- 查询 ----------

    def get_result(self, order_id: str) -> dict:
        """轮询查询转写结果，直到订单完成或超时。返回完整响应字典。"""
        body = {"order_id": order_id}
        headers = {
            "Authorization": self.bearer,
            "Content-Type": "application/json",
        }

        deadline = time.time() + self.poll_max_wait
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                resp = requests.post(self.result_url, headers=headers, json=body, timeout=15)
                resp.raise_for_status()
            except requests.exceptions.HTTPError as e:
                detail = http_error_detail(e)
                logger.warning("网关查询失败（第%d次）：%s，网关响应体：%s", attempt, e, detail)
                time.sleep(self.poll_interval)
                continue
            except requests.exceptions.RequestException as e:
                logger.warning("网关查询失败（第%d次）：%s", attempt, e)
                time.sleep(self.poll_interval)
                continue

            result = self._parse_json(resp.text)
            if str(result.get("code")) != "000000":
                raise RuntimeError(f"网关查询失败：code={result.get('code')}, desc={result.get('descInfo')}")

            content = result.get("content", {})
            order_info = content.get("orderInfo", {})
            status = order_info.get("status")
            fail_type = order_info.get("failType", 0)

            if status == STATUS_DONE:
                logger.info("网关转写完成（共查询 %d 次）", attempt)
                return result
            if status == STATUS_FAILED:
                raise_for_failtype(fail_type, status)
            if fail_type != 0:
                raise_for_failtype(fail_type, status)

            logger.info("网关转写处理中（第%d次，status=%s），%ds 后重试...",
                        attempt, status, self.poll_interval)
            time.sleep(self.poll_interval)

        raise TimeoutError(f"网关查询超时：已等待 {self.poll_max_wait}s，订单ID={order_id}")

    # ---------- 一站式转写 ----------

    def transcribe(self, file_url: str, language: str = "autodialect", pd: str = "",
                   timing: TimingInfo = None) -> str:
        """提交 URL + 轮询 + 解析，返回转写纯文本。"""
        # --- 上传计时 ---
        t_upload_start = time.time()
        order_id = self.upload_audio(file_url, language=language, pd=pd)
        t_upload_end = time.time()

        # --- 轮询/处理计时 ---
        t_process_start = time.time()
        result = self.get_result(order_id)
        t_process_end = time.time()

        text = parse_order_result(result)

        # 填充 timing
        if timing is not None:
            timing.iflytek_upload = t_upload_end - t_upload_start
            timing.iflytek_process = t_process_end - t_process_start
            timing.log_summary(label="gateway")

        logger.info("网关转写文本长度：%d", len(text))
        return text

    @staticmethod
    def _parse_json(text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise RuntimeError(f"网关返回非 JSON 数据：{text[:200]}")
