# -*- coding: utf-8 -*-
"""转写结果解析模块。

讯飞返回的 orderResult 是一个「字符串里套 JSON，JSON 里又套字符串」的多层嵌套结构：
    response.content.orderResult  (str)  ->  {"lattice":[{"json_1best": "<str>"}, ...]}
    json_1best                    (str)  ->  {"st":{"rt":[{"ws":[{"cw":[{"w":"文字", ...}]}]}]}}

本模块将其逐层解析，提取所有识别词 w 拼接成纯文本。
"""
import json
import re
from typing import Any


def _safe_json_loads(text: str) -> Any:
    """容错的 JSON 解析：先直接解析，失败则尝试去除多余转义后再解析。

    讯飞部分返回存在额外转义（\\\\ -> \\），直接 json.loads 会失败，
    这里作为兜底处理。
    """
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        cleaned = re.sub(r"\\\\", r"\\", text)
        return json.loads(cleaned)


def parse_order_result(api_response: dict) -> str:
    """从完整的 API 响应中提取转写纯文本。

    参数:
        api_response: /v2/getResult 接口返回的完整响应字典

    返回:
        所有识别词拼接后的纯文本字符串
    """
    order_result_str = (
        api_response.get("content", {}).get("orderResult", "") or ""
    )
    if not order_result_str:
        return ""

    order_result = _safe_json_loads(order_result_str)
    if not isinstance(order_result, dict):
        return ""

    words: list[str] = []
    # 优先使用 lattice（顺滑后结果），其次 lattice2（原始结果）
    for key in ("lattice", "lattice2"):
        lattice = order_result.get(key)
        if not lattice:
            continue
        words = _extract_words_from_lattice(lattice)
        if words:
            break
    return "".join(words)


def _extract_words_from_lattice(lattice: list) -> list[str]:
    """从 lattice 数组中提取所有词 w。"""
    words: list[str] = []
    for lattice_item in lattice:
        json_1best_str = lattice_item.get("json_1best") if isinstance(lattice_item, dict) else None
        if not json_1best_str:
            continue
        json_1best = _safe_json_loads(json_1best_str)
        if not isinstance(json_1best, dict):
            continue
        st = json_1best.get("st")
        if not isinstance(st, dict):
            continue
        for rt_item in st.get("rt", []) or []:
            if not isinstance(rt_item, dict):
                continue
            for ws_item in rt_item.get("ws", []) or []:
                if not isinstance(ws_item, dict):
                    continue
                for cw_item in ws_item.get("cw", []) or []:
                    if isinstance(cw_item, dict) and "w" in cw_item:
                        words.append(cw_item["w"])
    return words
