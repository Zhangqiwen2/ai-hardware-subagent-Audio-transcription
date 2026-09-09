#!/usr/bin/env python3
"""HTTP 直调转写Agent（使用 agentarts SDK 的 RuntimeClient，自动处理 V11 签名）。

和 `agentarts invoke` 走同一套签名代码，保证能通。

用法：
  export HUAWEICLOUD_SDK_AK='你的AK'
  export HUAWEICLOUD_SDK_SK='你的SK'
  python3 invoke.py -o query_capabilities                           # 能力查询
  python3 invoke.py -o chat_completions -f <音频URL>                 # 同步转写
  python3 invoke.py -o create_response -f <音频URL>                  # 异步创建
  python3 invoke.py -o fetch_response -i <response_id>              # 查询结果
"""
import json
import os
import sys
import argparse

from agentarts.sdk.service.runtime_client import RuntimeClient
from agentarts.sdk.service.http_client import SignMode

GATEWAY_DOMAIN = "defaultgw-grstqnldg5.cn-southwest-2.huaweicloud-agentarts.com"
RUNTIME_NAME = "asr-agent"
REGION = "cn-southwest-2"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--operation", default="query_capabilities")
    parser.add_argument("-f", "--file-url")
    parser.add_argument("-i", "--response-id")
    parser.add_argument("-s", "--session", default="sess-001")
    args = parser.parse_args()

    ak = os.environ.get("HUAWEICLOUD_SDK_AK", "")
    sk = os.environ.get("HUAWEICLOUD_SDK_SK", "")
    if not ak or not sk:
        print("请先 export HUAWEICLOUD_SDK_AK / HUAWEICLOUD_SDK_SK"); sys.exit(1)

    # 构建 payload（统一 inputs 包装格式）
    inputs = {"operation": args.operation}
    if args.operation in ("chat_completions", "create_response"):
        if not args.file_url:
            print("错误：需要 -f <音频URL>"); sys.exit(1)
        inputs["file_url"] = [args.file_url]
    elif args.operation == "fetch_response" and args.response_id:
        inputs["response_id"] = args.response_id
    payload = {"inputs": inputs}

    # 使用 agentarts SDK 的 RuntimeClient（自动 V11 签名）
    data_endpoint = "https://{}".format(GATEWAY_DOMAIN)
    client = RuntimeClient(
        data_endpoint=data_endpoint,
        verify_ssl=True,
        sign_mode=SignMode.V11_HMAC_SHA256,
        region_id=REGION,
    )

    print(">>> operation={} session={}".format(args.operation, args.session))
    print(">>> endpoint={}".format(data_endpoint))

    try:
        result = client.invoke_agent(
            agent_name=RUNTIME_NAME,
            session_id=args.session,
            payload=json.dumps(payload, ensure_ascii=False),
            timeout=300,
        )
        print("<<< {}".format(json.dumps(result, ensure_ascii=False, indent=2)))
    except Exception as e:
        print("<<< 失败: {}".format(e))
        sys.exit(1)


if __name__ == "__main__":
    main()