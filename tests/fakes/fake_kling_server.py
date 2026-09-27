"""A minimal, real local HTTP server implementing the ASSUMED Kling AI API
surface (see kling_provider.py's module docstring for the honesty caveat
on how this schema was derived) so KlingProvider can be tested end to end
against a genuine socket. Runs on 127.0.0.1 on an OS-assigned free port
only - no real Kling account, API key, or network call is ever involved.
"""
from __future__ import annotations

import base64
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

FAKE_VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 512


class FakeKlingServer:
    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.polls_before_complete = 0
        self.fail_task = False
        self.submit_calls: list[dict] = []

        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        assert self._httpd is not None
        return self._httpd.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def _send_json(self, payload: dict, status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_body(self) -> bytes:
                length = int(self.headers.get("Content-Length", 0))
                return self.rfile.read(length) if length else b""

            def _check_auth(self) -> bool:
                auth = self.headers.get("Authorization", "")
                return auth.startswith("Bearer ") and len(auth.split(".")) >= 1

            def do_GET(self):
                parsed = urlparse(self.path)

                if parsed.path == "/account/costs":
                    if not self._check_auth():
                        self._send_json({"error": "invalid token"}, status=401)
                        return
                    self._send_json({"code": 0, "data": {"total": 0}})
                    return

                if parsed.path.startswith("/v1/videos/multi-image2video/"):
                    if not self._check_auth():
                        self._send_json({"error": "invalid token"}, status=401)
                        return
                    task_id = parsed.path.rsplit("/", 1)[1]
                    task = server.tasks.get(task_id)
                    if task is None:
                        self._send_json({"error": "not found"}, status=404)
                        return
                    task["poll_count"] += 1
                    if task["poll_count"] <= server.polls_before_complete:
                        self._send_json({"data": {"task_id": task_id, "task_status": "processing"}})
                        return
                    if server.fail_task:
                        self._send_json({
                            "data": {"task_id": task_id, "task_status": "failed",
                                     "task_status_msg": "SIMULATED_FAILURE"}
                        })
                        return
                    output_url = f"{server.base_url}/fake-output/{task_id}.mp4"
                    self._send_json({
                        "data": {
                            "task_id": task_id,
                            "task_status": "succeed",
                            "task_result": {"videos": [{"url": output_url}]},
                        }
                    })
                    return

                if parsed.path.startswith("/fake-output/"):
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp4")
                    self.send_header("Content-Length", str(len(FAKE_VIDEO_BYTES)))
                    self.end_headers()
                    self.wfile.write(FAKE_VIDEO_BYTES)
                    return

                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                parsed = urlparse(self.path)

                if parsed.path == "/v1/videos/multi-image2video":
                    if not self._check_auth():
                        self._send_json({"error": "invalid token"}, status=401)
                        return
                    body = json.loads(self._read_body() or b"{}")
                    server.submit_calls.append(body)
                    task_id = uuid.uuid4().hex
                    server.tasks[task_id] = {"poll_count": 0}
                    self._send_json({"data": {"task_id": task_id, "task_status": "submitted"}})
                    return

                self.send_response(404)
                self.end_headers()

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
