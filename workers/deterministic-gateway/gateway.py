from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from typing import Any

API_KEY = "papertrans-deterministic-e2e-key"
MODEL = "papertrans-deterministic-ja-v1"
MAX_BODY_BYTES = 128 * 1024


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _translated_marker(source: str) -> str:
    del source
    return "PaperTransの決定論的E2E翻訳です。"


def _request_content(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict) or value.get("model") != MODEL:
        return None
    messages = value.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    last = messages[-1]
    if (
        not isinstance(last, dict)
        or last.get("role") != "user"
        or not isinstance(last.get("content"), str)
    ):
        return None
    prompt = last["content"]
    marker = "Input:\n\n"
    source = prompt.rsplit(marker, 1)[-1] if marker in prompt else prompt
    return value["model"], _translated_marker(source)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _send(self, status: int, value: object) -> None:
        body = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if (
            self.path != "/v1/chat/completions"
            or self.headers.get("Authorization") != f"Bearer {API_KEY}"
            or self.headers.get("Transfer-Encoding") is not None
        ):
            self._send(404, {"error": {"message": "request refused", "type": "invalid_request_error"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY_BYTES:
            self._send(413, {"error": {"message": "request refused", "type": "invalid_request_error"}})
            return
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(400, {"error": {"message": "request refused", "type": "invalid_request_error"}})
            return
        parsed = _request_content(value)
        if parsed is None:
            self._send(400, {"error": {"message": "request refused", "type": "invalid_request_error"}})
            return
        model, content = parsed
        self._send(
            200,
            {
                "id": "chatcmpl-papertrans-deterministic",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )


def main() -> None:
    server = HTTPServer(("0.0.0.0", 8080), Handler)
    server.serve_forever(poll_interval=0.25)


if __name__ == "__main__":
    main()
