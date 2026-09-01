# -*- coding: utf-8 -*-
"""讯飞录音文件转写大模型客户端。

基于讯飞开放平台《录音文件转写大模型》API 文档实现：
  - HMAC-SHA1 签名鉴权
  - /v2/upload  上传音频，获取订单 ID
  - /v2/getResult  轮询查询转写结果

移植自 Ifasr_llm/Ifasr.py，并修正以下与文档不一致之处：
  1. getResult 参数按文档仅传 accessKeyId/dateTime/signatureRandom/orderId/resultType
     （原示例多传了 appId、ts，且漏传了文档要求必传的 resultType）
  2. signatureRandom 在上传与查询间复用（文档要求「需与上传接口使用相同的随机串」）
  3. 查询的 dateTime 重新生成（文档要求「需重新生成」）
  4. 客户端不再在初始化时绑定单个音频文件，transcribe() 可复用
"""
import base64
import datetime
import hmac
import json
import logging
import os
import random
import string
import time
import urllib.parse
import wave
from dataclasses import dataclass, field
from typing import Optional

import requests

from result_parser import parse_order_result

logger = logging.getLogger("iflytek_asr")


class InvalidAudioError(Exception):
    """音频文件无效（客户端问题，E4002）：空文件、非音频格式、静音文件、损坏等。"""


# 讯飞订单 failType -> 中文说明（getResult 的 orderInfo.failType）
FAILTYPE_DESC = {
    0: "正常",
    1: "音频上传失败",
    2: "音频转码失败（文件损坏或格式不支持）",
    3: "音频识别失败",
    4: "音频时长超限（最大 5 小时）",
    5: "音频校验失败",
    6: "静音/空音频文件，无可转写内容",
    7: "翻译失败",
    8: "账号无翻译权限",
    9: "转写质检失败",
    10: "转写质检未匹配出关键词",
    11: "upload接口未开启对应能力",
    12: "音频语种分析失败",
    99: "其他",
}

# 属于客户端文件问题的 failType（映射 E4002 提示用户检查文件；其余归服务端 E5001）
CLIENT_FAULT_FAILTYPES = {2, 4, 5, 6}


def raise_for_failtype(fail_type: int, status=None) -> None:
    """按讯飞 failType 抛对应异常：客户端文件问题抛 InvalidAudioError，否则 RuntimeError。

    网关路径不下载音频文件（urlLink 直传），本地无法预检，
    客户端文件问题只能靠讯飞处理后的 failType 识别（如静音文件 failType=6）。
    """
    desc = FAILTYPE_DESC.get(fail_type, f"未知类型{fail_type}")
    if status is not None:
        detail = f"（failType={fail_type}, status={status}）"
    else:
        detail = f"（failType={fail_type}）"
    if fail_type in CLIENT_FAULT_FAILTYPES:
        raise InvalidAudioError(f"{desc}{detail}") from None
    raise RuntimeError(f"{desc}{detail}") from None


@dataclass
class TimingInfo:
    """转写耗时分解（单位：秒）。

    总耗时 = agent_overhead + iflytek_total
    - agent_overhead: Agent 侧开销（下载音频、解析 payload、格式转换等）
    - iflytek_total:  讯飞侧总耗时（上传 + 处理/轮询）
      - iflytek_upload:   上传到讯飞并被接收的耗时
      - iflytek_process:  讯飞处理耗时（含轮询等待）
    """
    agent_overhead: float = 0.0
    iflytek_upload: float = 0.0
    iflytek_process: float = 0.0

    @property
    def iflytek_total(self) -> float:
        return self.iflytek_upload + self.iflytek_process

    @property
    def total(self) -> float:
        return self.agent_overhead + self.iflytek_total

    def to_dict(self) -> dict:
        return {
            "total_ms": round(self.total * 1000, 1),
            "agent_overhead_ms": round(self.agent_overhead * 1000, 1),
            "iflytek_total_ms": round(self.iflytek_total * 1000, 1),
            "iflytek_upload_ms": round(self.iflytek_upload * 1000, 1),
            "iflytek_process_ms": round(self.iflytek_process * 1000, 1),
        }

    def log_summary(self, label: str = ""):
        """输出结构化耗时日志。"""
        d = self.to_dict()
        prefix = f"[{label}] " if label else ""
        logger.info(
            "%s耗时分解: total=%dms | agent=%dms | iflytek=%dms (upload=%dms + process=%dms)",
            prefix,
            d["total_ms"],
            d["agent_overhead_ms"],
            d["iflytek_total_ms"],
            d["iflytek_upload_ms"],
            d["iflytek_process_ms"],
        )

# 讯飞 API 基础配置
LFASR_HOST = "https://office-api-ist-dx.iflyaisol.com"
API_UPLOAD = "/v2/upload"
API_GET_RESULT = "/v2/getResult"

# 订单状态
STATUS_CREATED = 0
STATUS_PROCESSING = 3
STATUS_DONE = 4
STATUS_FAILED = -1


class XfyunAsrClient:
    """讯飞录音文件转写客户端。"""

    def __init__(
        self,
        app_id: str,
        access_key_id: str,
        access_key_secret: str,
        poll_interval: int = 10,
        poll_max_wait: int = 3600,
    ):
        self.app_id = app_id
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.poll_interval = poll_interval
        self.poll_max_wait = poll_max_wait

    # ---------- 工具方法 ----------

    @staticmethod
    def _generate_random_str(length: int = 16) -> str:
        """生成 16 位大小写字母+数字随机串。"""
        return "".join(random.choices(string.ascii_letters + string.digits, k=length))

    @staticmethod
    def _get_local_time_with_tz() -> str:
        """生成带时区偏移的本地时间（格式：yyyy-MM-dd'T'HH:mm:ss±HHmm）。"""
        local_now = datetime.datetime.now()
        tz_offset = local_now.astimezone().strftime("%z")  # +0800 / -0500
        return f"{local_now.strftime('%Y-%m-%dT%H:%M:%S')}{tz_offset}"

    @staticmethod
    def _get_wav_duration_ms(file_path: str) -> int:
        """用 Python 内置 wave 模块获取 WAV 音频时长（毫秒，整数）。"""
        with wave.open(file_path, "rb") as wav_file:
            n_frames = wav_file.getnframes()
            sample_rate = wav_file.getframerate()
            if sample_rate <= 0:
                raise ValueError(f"采样率异常：{sample_rate}")
            return int(round(n_frames / sample_rate * 1000))

    def _generate_signature(self, params: dict) -> str:
        """生成签名：对 key/value 做 URL 编码后排序拼接，再 HMAC-SHA1 + Base64。"""
        sign_params = {k: v for k, v in params.items() if k != "signature" and v is not None and str(v).strip() != ""}
        base_parts = []
        for k in sorted(sign_params.keys()):
            encoded_key = urllib.parse.quote(k, safe="")
            encoded_value = urllib.parse.quote(str(sign_params[k]), safe="")
            base_parts.append(f"{encoded_key}={encoded_value}")
        base_string = "&".join(base_parts)

        hmac_obj = hmac.new(
            self.access_key_secret.encode("utf-8"),
            base_string.encode("utf-8"),
            digestmod="sha1",
        )
        return base64.b64encode(hmac_obj.digest()).decode("utf-8")

    def _build_request(self, path: str, params: dict) -> tuple[str, str, dict]:
        """构建带签名的请求 URL 与请求头。返回 (url, signature, headers)。"""
        signature = self._generate_signature(params)
        encoded_parts = []
        for k, v in params.items():
            if v is None or str(v).strip() == "":
                continue
            encoded_parts.append(
                f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='')}"
            )
        url = f"{LFASR_HOST}{path}?{'&'.join(encoded_parts)}"
        return url, signature

    # ---------- 上传 ----------

    def upload_audio(
        self,
        file_path: str,
        signature_random: str,
        language: str = "autodialect",
        pd: str = "",
        duration_check_disable: bool = None,
    ) -> str:
        """上传音频文件，返回订单 ID（orderId）。

        参数:
            file_path: 音频文件路径（支持 wav/mp3/opus/flac 等讯飞支持格式）
            signature_random: 签名随机串（上传与查询需复用同一个）
            language: autodialect（中英+方言）/ autominor（多语种）
            pd: 领域参数，空串表示不传
            duration_check_disable: None=自动判断（WAV算时长，非WAV关闭校验）；
                                    True=关闭时长校验；False=强制算时长(仅WAV)
        """
        file_path = os.path.abspath(file_path)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"音频文件不存在：{file_path}")

        audio_size = str(os.path.getsize(file_path))
        audio_name = os.path.basename(file_path)
        is_wav = file_path.lower().endswith(".wav")
        # 自动判断：WAV 用 wave 模块算时长；非 WAV 关闭时长校验（讯飞自算，无需 ffprobe）
        if duration_check_disable is None:
            duration_check_disable = not is_wav
        if not duration_check_disable:
            try:
                duration_ms = self._get_wav_duration_ms(file_path)
            except EOFError:
                # wave 模块对空/损坏文件抛 EOFError（str 为空），转译为明确消息
                raise RuntimeError("音频文件为空或损坏，无法解析 WAV 头") from None
            logger.info("上传音频：%s（%s 字节，%d 毫秒）", audio_name, audio_size, duration_ms)
        else:
            logger.info("上传音频：%s（%s 字节，非WAV关闭时长校验）", audio_name, audio_size)

        url_params = {
            "appId": self.app_id,
            "accessKeyId": self.access_key_id,
            "dateTime": self._get_local_time_with_tz(),
            "signatureRandom": signature_random,
            "fileSize": audio_size,
            "fileName": audio_name,
            "language": language,
        }
        if pd:
            url_params["pd"] = pd
        if duration_check_disable:
            url_params["durationCheckDisable"] = "true"
        else:
            url_params["duration"] = str(duration_ms)

        url, signature = self._build_request(API_UPLOAD, url_params)
        headers = {"Content-Type": "application/octet-stream", "signature": signature}

        with open(file_path, "rb") as f:
            audio_data = f.read()

        try:
            resp = requests.post(url, headers=headers, data=audio_data, timeout=30, verify=False)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"上传请求网络失败：{e}") from e

        result = self._parse_json(resp.text)
        if str(result.get("code")) != "000000":
            raise RuntimeError(
                f"上传失败：code={result.get('code')}, desc={result.get('descInfo')}"
            )
        order_id = result["content"]["orderId"]
        estimate = result["content"].get("taskEstimateTime")
        logger.info("上传成功，订单ID=%s，预估耗时=%sms", order_id, estimate)
        return order_id

    # ---------- 查询 ----------

    def get_result(self, order_id: str, signature_random: str) -> dict:
        """轮询查询转写结果，直到订单完成或超时。返回完整的 API 响应字典。"""
        query_params = {
            "accessKeyId": self.access_key_id,
            "dateTime": self._get_local_time_with_tz(),  # 需重新生成
            "signatureRandom": signature_random,          # 需与上传一致
            "orderId": order_id,
            "resultType": "transfer",
        }
        url, signature = self._build_request(API_GET_RESULT, query_params)
        headers = {"Content-Type": "application/json", "signature": signature}

        deadline = time.time() + self.poll_max_wait
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                resp = requests.post(
                    url, headers=headers, data=json.dumps({}), timeout=15, verify=False
                )
                resp.raise_for_status()
            except requests.exceptions.RequestException as e:
                logger.warning("查询请求失败（第%d次）：%s", attempt, e)
                time.sleep(self.poll_interval)
                continue

            result = self._parse_json(resp.text)
            if str(result.get("code")) != "000000":
                raise RuntimeError(
                    f"查询失败：code={result.get('code')}, desc={result.get('descInfo')}"
                )

            content = result.get("content", {})
            order_info = content.get("orderInfo", {})
            status = order_info.get("status")
            fail_type = order_info.get("failType", 0)

            if status == STATUS_DONE:
                logger.info("转写完成（共查询 %d 次）", attempt)
                return result
            if status == STATUS_FAILED:
                raise_for_failtype(fail_type, status)
            if fail_type != 0:
                raise_for_failtype(fail_type, status)

            estimate = content.get("taskEstimateTime", "?")
            logger.info(
                "转写处理中（第%d次，status=%s，预估%sms），%ds 后重试...",
                attempt, status, estimate, self.poll_interval,
            )
            time.sleep(self.poll_interval)

        raise TimeoutError(f"查询超时：已等待 {self.poll_max_wait}s，订单ID={order_id}")

    # ---------- 一站式转写 ----------

    def transcribe(
        self,
        file_path: str,
        language: str = "autodialect",
        pd: str = "",
        duration_check_disable: bool = None,
        timing: TimingInfo = None,
    ) -> str:
        """上传 + 轮询 + 解析，返回转写纯文本。duration_check_disable=None 时自动判断格式。

        如果传入 timing=TimingInfo()，会填充讯飞侧耗时（upload + process）。
        Agent 侧开销（下载等）由调用方在 timing.agent_overhead 中累加。
        """
        signature_random = self._generate_random_str()

        # --- 上传计时 ---
        t_upload_start = time.time()
        order_id = self.upload_audio(
            file_path, signature_random, language=language, pd=pd,
            duration_check_disable=duration_check_disable,
        )
        t_upload_end = time.time()

        # --- 轮询/处理计时 ---
        t_process_start = time.time()
        result = self.get_result(order_id, signature_random)
        t_process_end = time.time()

        text = parse_order_result(result)

        # 填充 timing
        if timing is not None:
            timing.iflytek_upload = t_upload_end - t_upload_start
            timing.iflytek_process = t_process_end - t_process_start
            timing.log_summary(label="iflytek")

        logger.info("转写文本长度：%d", len(text))
        return text

    @staticmethod
    def _parse_json(text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise RuntimeError(f"API 返回非 JSON 数据：{text[:200]}")
