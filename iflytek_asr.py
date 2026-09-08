# -*- coding: utf-8 -*-
"""讯飞录音文件转写大模型 — 共享工具模块。

本模块仅保留网关路径与直调路径共用的异常、常量与工具函数。
直调讯飞的客户端（XfyunAsrClient）已移除，所有转写均走网关路径（gateway_asr.py）。
"""
import logging
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
