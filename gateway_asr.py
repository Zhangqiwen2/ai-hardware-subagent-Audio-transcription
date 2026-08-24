# -*- coding: utf-8 -*-
"""ConvAIRouter 网关转写客户端（Bearer 认证）。

按 FE2026080500089 设计：Agent 用 model_config 注入的 endpoint + auth_token
调用 ConvAIRouter 的 /v2/upload 与 /v2/getResult（路径与讯飞一致），
ConvAIRouter 校验 Bearer 后转发给讯飞并透传结果。

请求/响应沿用讯飞 Ifasr_llm 的格式（ConvAIRouter 转发），差异：
- 不做 HMAC-SHA1 签名，改用 Authorization: Bearer <auth_token>
- 不传 appId/accessKeyId 等讯飞身份参数（由 ConvAIRouter 侧注入）
"""
import json
import logging
import os
import time

import requests

from iflytek_asr import TimingInfo, XfyunAsrClient
from result_parser import parse_order_result

logger = logging.getLogger("gateway_asr")

# 订单状态（与讯飞一致）
STATUS_DONE = 4
STATUS_FAILED = -1


class GatewayAsrClient:
    """通过 ConvAIRouter 调讯飞离线转写的客户端（Bearer 认证）。

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

    def upload_audio(self, file_path: str, language: str = "autodialect", pd: str = "") -> str:
        """上传音频到 ConvAIRouter，返回 orderId。"""
        file_path = os.path.abspath(file_path)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"音频文件不存在：{file_path}")

        audio_name = os.path.basename(file_path)
        is_wav = file_path.lower().endswith(".wav")
        params = {
            "fileName": audio_name,
            "fileSize": str(os.path.getsize(file_path)),
            "language": language,
        }
        if pd:
            params["pd"] = pd
        # WAV 用 wave 模块算时长；非 WAV 关闭时长校验（讯飞自算）
        if is_wav:
            params["duration"] = str(XfyunAsrClient._get_wav_duration_ms(file_path))
        else:
            params["durationCheckDisable"] = "true"

        headers = {
            "Authorization": self.bearer,
            "Content-Type": "application/octet-stream",
        }
        logger.info("网关上传音频：%s（%s 字节）", audio_name, params["fileSize"])

        with open(file_path, "rb") as f:
            audio_data = f.read()
        try:
            resp = requests.post(self.upload_url, params=params,
                                 headers=headers, data=audio_data, timeout=30)
            resp.raise_for_status()
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
        params = {"orderId": order_id, "resultType": "transfer"}
        headers = {
            "Authorization": self.bearer,
            "Content-Type": "application/json",
        }

        deadline = time.time() + self.poll_max_wait
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                resp = requests.post(self.result_url, params=params,
                                     headers=headers, data="{}", timeout=15)
                resp.raise_for_status()
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
                raise RuntimeError(f"网关转写失败：status={status}, failType={fail_type}")
            if fail_type != 0:
                raise RuntimeError(f"网关转写异常：failType={fail_type}, status={status}")

            logger.info("网关转写处理中（第%d次，status=%s），%ds 后重试...",
                        attempt, status, self.poll_interval)
            time.sleep(self.poll_interval)

        raise TimeoutError(f"网关查询超时：已等待 {self.poll_max_wait}s，订单ID={order_id}")

    # ---------- 一站式转写 ----------

    def transcribe(self, file_path: str, language: str = "autodialect", pd: str = "",
                   timing: TimingInfo = None) -> str:
        """上传 + 轮询 + 解析，返回转写纯文本。"""
        # --- 上传计时 ---
        t_upload_start = time.time()
        order_id = self.upload_audio(file_path, language=language, pd=pd)
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
