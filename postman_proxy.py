#!/usr/bin/env python3
"""Postman 代理：Postman 用 Bearer 调本地端口，脚本自动加 IAM 签名转发到 AgentArts。

用法：
  export HUAWEICLOUD_SDK_AK='你的AK'
  export HUAWEICLOUD_SDK_SK='你的SK'
  python3 postman_proxy.py

然后 Postman 调:
  POST http://localhost:9800/invocations
  Authorization: Bearer dummy
  Body: {"inputs": {...}}
"""
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

from agentarts.sdk.service.runtime_client import RuntimeClient
from agentarts.sdk.service.http_client import SignMode

GATEWAY = "https://defaultgw-grstqnldg5.cn-southwest-2.huaweicloud-agentarts.com"
RUNTIME = "runtime-fdwpvnrb-luyin"
REGION = "cn-southwest-2"
PORT = 9800

client = RuntimeClient(
    data_endpoint=GATEWAY,
    verify_ssl=True,
    sign_mode=SignMode.V11_HMAC_SHA256,
    region_id=REGION,
)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/invocations":
            self.send_error(404, "Not Found")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")

        # 从请求头取 session id
        session_id = self.headers.get("X-Hw-Agentarts-Session-Id", "postman-sess")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        try:
            result = client.invoke_agent(
                agent_name=RUNTIME,
                session_id=session_id,
                payload=json.dumps(payload, ensure_ascii=False),
                timeout=300,
            )
            resp_body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        except Exception as e:
            err = json.dumps({"error": str(e)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(err)

    def log_message(self, format, *args):
        # 简化日志
        print(f"[{self.log_date_time_string()}] {args[0]}")


if __name__ == "__main__":
    ak = os.environ.get("HUAWEICLOUD_SDK_AK", "")
    sk = os.environ.get("HUAWEICLOUD_SDK_SK", "")
    if not ak or not sk:
        print("❌ 请先 export HUAWEICLOUD_SDK_AK / HUAWEICLOUD_SDK_SK")
        sys.exit(1)

    print(f"Postman 代理已启动: http://localhost:{PORT}/invocations")
    print(f"转发目标: {GATEWAY}/runtimes/{RUNTIME}/invocations")
    print(f"认证: IAM V11 (自动签名)")
    print()
    print("Postman 配置:")
    print(f"  URL:    http://localhost:{PORT}/invocations")
    print(f"  Header: Authorization: Bearer dummy")
    print(f"  Header: X-Hw-Agentarts-Session-Id: test-001")
    print()

    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
