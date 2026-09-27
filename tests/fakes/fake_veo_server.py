"""A minimal, real local HTTP server implementing the OFFICIAL Gemini API
surface for Veo 3.1 (verified against Google's own docs - see
veo_provider.py's module docstring) so VeoProvider can be tested end to
end against a genuine socket. Runs on 127.0.0.1 on an OS-assigned free
port only - no real Google/Gemini account, API key, or network call is
ever involved.
"""
from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

FAKE_VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 512


class FakeVeoServer:
    def __init__(self, expected_api_key: str = "test-veo-key") -> None:
        self.expected_api_key = expected_api_key
        self.operations: dict[str, dict] = {}
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

    def _check_auth(self, handler: BaseHTTPRequestHandler) -> bool:
        return handler.headers.get("x-goog-api-key", "") == self.expected_api_key

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

                if parsed.path.startswith("/models/") and "/operations/" not in parsed.path:
                    if not server._check_auth(self):
                        self._send_json({"error": "invalid api key"}, status=401)
                        return
                    model_id = parsed.path.rsplit("/", 1)[1]
                    self._send_json({"name": f"models/{model_id}", "displayName": model_id})
                    return

                if parsed.path.startswith("/operations/"):
                    if not server._check_auth(self):
                        self._send_json({"error": "invalid api key"}, status=401)
                        return
                    op_id = parsed.path.rsplit("/", 1)[1]
                    op = server.operations.get(op_id)
                    if op is None:
                        self._send_json({"error": "not found"}, status=404)
                        return
                    op["poll_count"] += 1
                    if op["poll_count"] <= server.polls_before_complete:
                        self._send_json({"name": f"operations/{op_id}", "done": False})
                        return
                    if server.fail_task:
                        self._send_json({
                            "name": f"operations/{op_id}", "done": True,
                            "error": {"code": 500, "message": "SIMULATED_FAILURE"},
                        })
                        return
                    video_uri = f"{server.base_url}/fake-output/{op_id}.mp4"
                    self._send_json({
                        "name": f"operations/{op_id}",
                        "done": True,
                        "response": {
                            "generateVideoResponse": {
                                "generatedSamples": [{"video": {"uri": video_uri}}]
                            }
                        },
                    })
                    return

                if parsed.path.startswith("/fake-output/"):
                    if not server._check_auth(self):
                        self.send_response(401)
                        self.end_headers()
                        return
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

                if parsed.path.startswith("/models/") and parsed.path.endswith(":predictLongRunning"):
                    if not server._check_auth(self):
                        self._send_json({"error": "invalid api key"}, status=401)
                        return
                    body = json.loads(self._read_body() or b"{}")
                    server.submit_calls.append(body)
                    op_id = uuid.uuid4().hex
                    server.operations[op_id] = {"poll_count": 0}
                    self._send_json({"name": f"operations/{op_id}"})
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
