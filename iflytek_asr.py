# -*- coding: utf-8 -*-
"""讯飞录音文件转写大模型 — 共享工具模块。

包含：
- 网关路径与直调路径共用的异常、常量与工具函数
- XfyunDirectClient：直调讯飞客户端（FORCE_DIRECT_IFLYTEK 自验证用）
"""
import base64
import datetime
import hmac
import json
import logging
import os
import random
import string
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

import requests

from result_parser import parse_order_result, parse_sentences

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
    """按讯飞 failType 抛对应异常：客户端文件问题抛 InvalidAudioError，否则 RuntimeError。"""
    desc = FAILTYPE_DESC.get(fail_type, f"未知类型{fail_type}")
    if status is not None:
        detail = f"（failType={fail_type}, status={status}）"
    else:
        detail = f"（failType={fail_type}）"
    if fail_type in CLIENT_FAULT_FAILTYPES:
        raise InvalidAudioError(f"{desc}{detail}") from None
    raise RuntimeError(f"{desc}{detail}") from None


def http_error_detail(e: Exception, limit: int = 500) -> str:
    """从 HTTPError 提取响应体摘要（服务端返回错误状态时的具体原因）。"""
    resp = getattr(e, "response", None)
    if resp is None:
        return ""
    return (resp.text or "").strip()[:limit]


logger = logging.getLogger("iflytek_asr")


@dataclass
class TimingInfo:
    """转写耗时分解（单位：秒）。"""
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


# ---------- 直调讯飞客户端（FORCE_DIRECT_IFLYTEK 自验证用）----------

# 讯飞 API 基础配置
LFASR_HOST = "https://office-api-ist-dx.iflyaisol.com"
API_UPLOAD = "/v2/upload"
API_GET_RESULT = "/v2/getResult"

# 订单状态
STATUS_DONE = 4
STATUS_FAILED = -1


class XfyunDirectClient:
    """讯飞录音文件转写直调客户端（FORCE_DIRECT_IFLYTEK 自验证用）。

    与旧 XfyunAsrClient 相比：
    - 输入为 URL（http/https），自动下载到临时文件后上传讯飞
    - 默认开启 roleType=1 说话人分离
    - 返回 {"text": 纯文本, "sentences": 分段结果}，与网关路径接口一致
    """

    def __init__(
        self,
        app_id: str,
        access_key_id: str,
        access_key_secret: str,
        poll_interval: int = 10,
        poll_max_wait: int = 3600,
        tmp_dir: str = "/tmp/asr_agent",
    ):
        self.app_id = app_id
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.poll_interval = poll_interval
        self.poll_max_wait = poll_max_wait
        self.tmp_dir = tmp_dir

    # ---------- 工具方法 ----------

    @staticmethod
    def _generate_random_str(length: int = 16) -> str:
        return "".join(random.choices(string.ascii_letters + string.digits, k=length))

    @staticmethod
    def _get_local_time_with_tz() -> str:
        local_now = datetime.datetime.now()
        tz_offset = local_now.astimezone().strftime("%z")
        return f"{local_now.strftime('%Y-%m-%dT%H:%M:%S')}{tz_offset}"

    def _generate_signature(self, params: dict) -> str:
        sign_params = {k: v for k, v in params.items()
                       if k != "signature" and v is not None and str(v).strip() != ""}
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

    def _build_request(self, path: str, params: dict) -> tuple:
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

    # ---------- 下载音频 ----------

    def _download_audio(self, file_url: str) -> str:
        """下载音频到临时目录，返回本地文件路径。"""
        os.makedirs(self.tmp_dir, exist_ok=True)
        # 从 URL 提取文件名
        url_path = urllib.parse.urlparse(file_url).path
        filename = os.path.basename(url_path) or "audio.wav"
        local_path = os.path.join(self.tmp_dir, f"direct_{filename}")

        logger.info("直调讯飞：下载音频 %s -> %s", file_url, local_path)
        resp = requests.get(file_url, stream=True, timeout=60)
        resp.raise_for_status()

        # 检查文件大小（讯飞限制 5 小时/500MB 左右）
        content_length = resp.headers.get("Content-Length")
        if content_length and int(content_length) == 0:
            raise InvalidAudioError("下载的音频文件为空（0字节）")

        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        size = os.path.getsize(local_path)
        if size == 0:
            raise InvalidAudioError("下载的音频文件为空（0字节）")
        logger.info("直调讯飞：下载完成，%d 字节", size)
        return local_path

    # ---------- 上传 ----------

    @staticmethod
    def _get_wav_duration_ms(file_path: str) -> int:
        """用 Python 内置 wave 模块获取 WAV 音频时长（毫秒，整数）。"""
        import wave
        with wave.open(file_path, "rb") as wav_file:
            n_frames = wav_file.getnframes()
            sample_rate = wav_file.getframerate()
            if sample_rate <= 0:
                raise ValueError(f"采样率异常：{sample_rate}")
            return int(round(n_frames / sample_rate * 1000))

    def upload_audio(self, file_path: str, signature_random: str,
                     language: str = "autodialect", pd: str = "") -> str:
        """上传本地音频文件到讯飞，返回 orderId。"""
        file_path = os.path.abspath(file_path)
        audio_size = str(os.path.getsize(file_path))
        audio_name = os.path.basename(file_path)

        # 时长参数：WAV 用 wave 模块计算；非 WAV 关闭时长校验（讯飞自算）
        is_wav = file_path.lower().endswith(".wav")
        duration_check_disable = not is_wav
        duration_ms = None
        if not duration_check_disable:
            try:
                duration_ms = self._get_wav_duration_ms(file_path)
            except Exception as e:
                # wave 模块解析失败（非标准 WAV / 损坏）-> 关闭时长校验
                logger.warning("WAV 时长解析失败（%s），关闭时长校验", e)
                duration_check_disable = True

        logger.info("直调讯飞上传参数：file=%s, size=%s, is_wav=%s, duration=%s, durationCheckDisable=%s",
                    audio_name, audio_size, is_wav, duration_ms, duration_check_disable)

        url_params = {
            "appId": self.app_id,
            "accessKeyId": self.access_key_id,
            "dateTime": self._get_local_time_with_tz(),
            "signatureRandom": signature_random,
            "fileSize": audio_size,
            "fileName": audio_name,
            "language": language,
            # 说话人分离：通用角色分离 + 盲分
            "roleType": 1,
            "roleNum": 0,
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
        except requests.exceptions.HTTPError as e:
            detail = http_error_detail(e)
            raise RuntimeError(f"直调讯飞上传失败：{e}，响应体：{detail}") from e
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"直调讯飞上传网络失败：{e}") from e

        result = self._parse_json(resp.text)
        if str(result.get("code")) != "000000":
            raise RuntimeError(f"直调讯飞上传失败：code={result.get('code')}, desc={result.get('descInfo')}")
        order_id = result["content"]["orderId"]
        logger.info("直调讯飞上传成功，订单ID=%s", order_id)
        return order_id

    # ---------- 查询 ----------

    def get_result(self, order_id: str, signature_random: str) -> dict:
        """轮询查询转写结果，直到完成或超时。返回完整响应字典。"""
        query_params = {
            "accessKeyId": self.access_key_id,
            "dateTime": self._get_local_time_with_tz(),
            "signatureRandom": signature_random,
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
                resp = requests.post(url, headers=headers, data=json.dumps({}),
                                     timeout=15, verify=False)
                resp.raise_for_status()
            except requests.exceptions.HTTPError as e:
                detail = http_error_detail(e)
                logger.warning("直调讯飞查询失败（第%d次）：%s，响应体：%s", attempt, e, detail)
                time.sleep(self.poll_interval)
                continue
            except requests.exceptions.RequestException as e:
                logger.warning("直调讯飞查询失败（第%d次）：%s", attempt, e)
                time.sleep(self.poll_interval)
                continue

            result = self._parse_json(resp.text)
            if str(result.get("code")) != "000000":
                raise RuntimeError(f"直调讯飞查询失败：code={result.get('code')}, desc={result.get('descInfo')}")

            content = result.get("content", {})
            order_info = content.get("orderInfo", {})
            status = order_info.get("status")
            fail_type = order_info.get("failType", 0)

            if status == STATUS_DONE:
                logger.info("直调讯飞转写完成（共查询 %d 次）", attempt)
                return result
            if status == STATUS_FAILED:
                raise_for_failtype(fail_type, status)
            if fail_type != 0:
                raise_for_failtype(fail_type, status)

            logger.info("直调讯飞转写处理中（第%d次，status=%s），%ds 后重试...",
                        attempt, status, self.poll_interval)
            time.sleep(self.poll_interval)

        raise TimeoutError(f"直调讯飞查询超时：已等待 {self.poll_max_wait}s，订单ID={order_id}")

    # ---------- 一站式转写 ----------

    def transcribe(self, file_url: str, language: str = "autodialect", pd: str = "",
                   timing: TimingInfo = None) -> dict:
        """下载音频 -> 上传 -> 轮询 -> 解析，返回 {"text": 纯文本, "sentences": 分段结果}。"""
        signature_random = self._generate_random_str()

        # 下载音频
        local_path = self._download_audio(file_url)
        try:
            # 上传计时
            t_upload_start = time.time()
            order_id = self.upload_audio(local_path, signature_random,
                                         language=language, pd=pd)
            t_upload_end = time.time()

            # 轮询计时
            t_process_start = time.time()
            result = self.get_result(order_id, signature_random)
            t_process_end = time.time()
        finally:
            # 清理临时文件
            try:
                os.remove(local_path)
            except OSError:
                pass

        text = parse_order_result(result)
        sentences = parse_sentences(result)

        if timing is not None:
            timing.iflytek_upload = t_upload_end - t_upload_start
            timing.iflytek_process = t_process_end - t_process_start
            timing.log_summary(label="direct-iflytek")

        logger.info("直调讯飞转写文本长度：%d，分段数：%d", len(text), len(sentences))
        return {"text": text, "sentences": sentences}

    @staticmethod
    def _parse_json(text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise RuntimeError(f"直调讯飞返回非 JSON 数据：{text[:200]}")
