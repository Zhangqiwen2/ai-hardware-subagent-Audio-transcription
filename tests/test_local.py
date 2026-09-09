# -*- coding: utf-8 -*-
"""本地测试脚本。

包含两类测试：
  1. test_parse_result：用讯飞示例响应验证 result_parser（无需密钥/网络/SDK）。
  2. test_full_transcribe：用示例 wav 跑完整转写流程（需配置真实讯飞密钥）。

运行：python tests/test_local.py
"""
import os
import sys

# 把项目根目录加入 path，便于直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from result_parser import parse_order_result
from config import settings

# 讯飞文档中的示例响应（已处理完成，status=4）
SAMPLE_RESPONSE = {
    "code": "000000",
    "descInfo": "success",
    "content": {
        "orderResult": (
            '{"lattice":[{"json_1best":"{\\"st\\":{\\"pa\\":\\"0\\",\\"rt\\":[{\\"ws\\":[{\\"cw\\":[{\\"w\\":\\"喂\\",\\"wp\\":\\"s\\",\\"wc\\":\\"0.9806\\"}],\\"wb\\":19,\\"we\\":52},{\\"cw\\":[{\\"w\\":\\"你好\\",\\"wp\\":\\"n\\",\\"wc\\":\\"1.0000\\"}],\\"wb\\":53,\\"we\\":111}]}],\\"bg\\":\\"2390\\",\\"rl\\":\\"1\\",\\"ed\\":\\"3640\\"}}"}]}'
        ),
        "orderInfo": {
            "failType": 0,
            "status": 4,
            "orderId": "DKHJQ202003171520031715109E1FF5E50001D",
            "originalDuration": 14000,
        },
    },
}


def test_parse_result():
    """验证解析器能从示例响应中提取纯文本。"""
    text = parse_order_result(SAMPLE_RESPONSE)
    print("解析结果：", repr(text))
    assert "喂" in text and "你好" in text, f"解析结果异常：{text}"
    print("[PASS] test_parse_result")
    return text


def test_full_transcribe():
    """用示例 wav 跑完整转写流程（需真实密钥 + requests）。"""
    settings.validate()  # 缺密钥则跳过
    from service import transcribe_from_payload  # 延迟导入，避免依赖阻断解析器单测

    wav_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "Ifasr_llm", "audio", "lfasr_涉政.wav",
    )
    wav_path = os.path.abspath(wav_path)
    if not os.path.exists(wav_path):
        print(f"[SKIP] test_full_transcribe：示例音频不存在 {wav_path}")
        return

    text = transcribe_from_payload({"file_path": wav_path})
    print("转写结果：\n", text)
    assert text, "转写结果为空"
    print("[PASS] test_full_transcribe")


if __name__ == "__main__":
    print("=" * 60)
    print("1. 测试结果解析（无需密钥）")
    test_parse_result()

    print("=" * 60)
    print("2. 测试完整转写流程（需配置 IFLYTEK_* 密钥）")
    try:
        test_full_transcribe()
    except RuntimeError as e:
        print(f"[SKIP] test_full_transcribe：{e}")
    print("=" * 60)
