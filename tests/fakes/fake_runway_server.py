"""A minimal, real local HTTP server implementing just enough of the
official Runway Dev API surface (POST /v1/text_to_video, POST
/v1/image_to_video, POST /v1/uploads + presigned upload target, GET/DELETE
/v1/tasks/{id}, GET /v1/organization) for RunwayProvider to be tested end to
end against a genuine socket. Runs on 127.0.0.1 on an OS-assigned free port
only - no real Runway account, API key, or network call is ever involved,
so this suite makes zero real (and zero paid) API calls, per the project's
"simulierte API-Antworten fuer Tests" requirement.
"""
from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

FAKE_VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 512  # not a real mp4, just bytes to download


class FakeRunwayServer:
    def __init__(self, expected_api_key: str = "test-runway-key") -> None:
        self.expected_api_key = expected_api_key
        self.tasks: dict[str, dict] = {}
        self.uploads: dict[str, bytes] = {}

        # -- configurable behavior ------------------------------------
        self.polls_before_complete = 0
        self.fail_task = False
        self.credit_balance = 5000
        self.submit_calls: list[dict] = []
        self.upload_calls = 0
        self.cancel_calls: list[str] = []

        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        assert self._httpd is not None
        return self._httpd.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _check_auth(self, handler: BaseHTTPRequestHandler) -> bool:
        auth = handler.headers.get("Authorization", "")
        return auth == f"Bearer {self.expected_api_key}"

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

            def do_GET(self):
                parsed = urlparse(self.path)

                if parsed.path == "/v1/organization":
                    if not server._check_auth(self):
                        self._send_json({"error": "invalid api key"}, status=401)
                        return
                    self._send_json({
                        "tier": {"maxMonthlyCreditSpend": 100, "models": {}},
                        "creditBalance": server.credit_balance,
                        "usage": {"models": {}},
                    })
                    return

                if parsed.path.startswith("/v1/tasks/"):
                    if not server._check_auth(self):
                        self._send_json({"error": "invalid api key"}, status=401)
                        return
                    task_id = parsed.path.split("/v1/tasks/", 1)[1]
                    task = server.tasks.get(task_id)
                    if task is None:
                        self._send_json({"error": "not found"}, status=404)
                        return
                    task["poll_count"] += 1
                    if task.get("cancelled"):
                        self._send_json({"id": task_id, "status": "FAILED", "failure": "CANCELLED"})
                        return
                    if task["poll_count"] <= server.polls_before_complete:
                        self._send_json({"id": task_id, "status": "RUNNING"})
                        return
                    if server.fail_task:
                        self._send_json({"id": task_id, "status": "FAILED", "failure": "INTERNAL.SIMULATED"})
                        return
                    output_url = f"{server.base_url}/fake-output/{task_id}.mp4"
                    self._send_json({"id": task_id, "status": "SUCCEEDED", "output": [output_url]})
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

            def do_DELETE(self):
                parsed = urlparse(self.path)
                if parsed.path.startswith("/v1/tasks/"):
                    task_id = parsed.path.split("/v1/tasks/", 1)[1]
                    server.cancel_calls.append(task_id)
                    if task_id in server.tasks:
                        server.tasks[task_id]["cancelled"] = True
                    self._send_json({})
                    return
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                parsed = urlparse(self.path)

                if parsed.path in ("/v1/text_to_video", "/v1/image_to_video"):
                    if not server._check_auth(self):
                        self._send_json({"error": "invalid api key"}, status=401)
                        return
                    body = json.loads(self._read_body() or b"{}")
                    server.submit_calls.append({"endpoint": parsed.path, "body": body})
                    task_id = uuid.uuid4().hex
                    server.tasks[task_id] = {"poll_count": 0, "cancelled": False}
                    self._send_json({"id": task_id, "status": "PENDING"})
                    return

                if parsed.path == "/v1/uploads":
                    if not server._check_auth(self):
                        self._send_json({"error": "invalid api key"}, status=401)
                        return
                    body = json.loads(self._read_body() or b"{}")
                    token = uuid.uuid4().hex
                    self._send_json({
                        "uploadUrl": f"{server.base_url}/fake-upload/{token}",
                        "fields": {"token": token},
                        "runwayUri": f"runway://fake/{token}",
                    })
                    return

                if parsed.path.startswith("/fake-upload/"):
                    server.upload_calls += 1
                    body = self._read_body()
                    token = parsed.path.split("/fake-upload/", 1)[1]
                    server.uploads[token] = body
                    self._send_json({}, status=200)
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
