# -*- coding: utf-8 -*-
"""本地测试入口（不依赖 AgentArts SDK）。

直接调用转写服务对示例音频进行转写，用于在推送镜像前验证核心逻辑。
需先配置 .env 中的讯飞密钥。

运行：python main.py [音频文件路径]
"""
import sys

from service import transcribe_from_payload

DEFAULT_AUDIO = "../Ifasr_llm/audio/lfasr_涉政.wav"


def main():
    audio_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_AUDIO
    print(f"待转写音频：{audio_path}")
    print("=" * 60)
    try:
        text = transcribe_from_payload({"file_path": audio_path})
        print("转写结果：")
        print(text)
    except Exception as e:
        print(f"转写失败：{e}")
        sys.exit(1)
    print("=" * 60)


if __name__ == "__main__":
    main()
