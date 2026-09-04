"""A minimal, real local HTTP server that implements just enough of the
ComfyUI API surface (POST /prompt, GET /history/{id}, GET /queue,
POST /interrupt, POST /queue (delete), POST /upload/image, GET /view,
GET /system_stats) for ComfyUIProvider to be tested end to end against a
genuine socket - without ever touching a real ComfyUI instance, a GPU, or
the internet. Runs on 127.0.0.1 on an OS-assigned free port only, so tests
using this incur no cost and make no external network call whatsoever.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def _make_tiny_mp4() -> bytes:
    """Renders a genuinely valid (tiny) mp4 with real ffmpeg so the
    provider's download + local-upscale step has real video bytes to work
    with, exactly like a real ComfyUI output file would provide."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "tiny.mp4"
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1:r=8",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(out), "-loglevel", "error",
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return out.read_bytes()


class FakeComfyUIServer:
    """Usage:
        server = FakeComfyUIServer()
        server.start()
        ... point ComfyUIProvider(host="127.0.0.1", port=server.port) at it ...
        server.stop()

    Behavior is controlled via the public attributes below, settable before
    or during a test to simulate different scenarios (immediate success,
    delayed success after N polls, job failure, node validation errors).
    """

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.uploaded_images: dict[str, bytes] = {}
        self._tiny_mp4 = _make_tiny_mp4()

        # -- configurable behavior -----------------------------------
        self.polls_before_complete = 0
        """How many GET /history/{id} calls return "still running" before
        the job is reported complete. 0 = complete on first poll."""
        self.fail_job = False
        """If True, jobs complete with status_str == 'error' instead of
        success."""
        self.reject_with_node_errors: dict | None = None
        """If set, POST /prompt responds with this node_errors payload
        instead of accepting the job."""
        self.last_submitted_workflow: dict | None = None
        self.last_client_id: str | None = None
        self.upload_calls = 0
        self.prompt_call_count = 0

        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        assert self._httpd is not None
        return self._httpd.server_address[1]

    def start(self) -> None:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # silence stdout noise in test runs
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
                if parsed.path == "/system_stats":
                    self._send_json({"system": {"comfyui_version": "fake-0.0"}, "devices": []})
                    return
                if parsed.path == "/queue":
                    pending = [
                        [0, pid] for pid, j in server.jobs.items()
                        if j["status"] == "queued" and not j.get("cancelled")
                    ]
                    running = [
                        [0, pid] for pid, j in server.jobs.items()
                        if j["status"] == "running"
                    ]
                    self._send_json({"queue_running": running, "queue_pending": pending})
                    return
                if parsed.path.startswith("/history/"):
                    prompt_id = parsed.path.split("/history/", 1)[1]
                    job = server.jobs.get(prompt_id)
                    if job is None:
                        self._send_json({})
                        return
                    job["poll_count"] += 1
                    if job.get("cancelled"):
                        self._send_json({
                            prompt_id: {
                                "status": {"status_str": "error", "completed": False,
                                           "messages": ["cancelled"]},
                                "outputs": {},
                            }
                        })
                        return
                    if job["poll_count"] <= server.polls_before_complete:
                        self._send_json({})  # not in history yet -> still running
                        return
                    if server.fail_job:
                        self._send_json({
                            prompt_id: {
                                "status": {"status_str": "error", "completed": False,
                                           "messages": ["simulated failure"]},
                                "outputs": {},
                            }
                        })
                        return
                    self._send_json({
                        prompt_id: {
                            "status": {"status_str": "success", "completed": True, "messages": []},
                            "outputs": {
                                "58": {
                                    "videos": [
                                        {"filename": "fake_output.mp4", "subfolder": "", "type": "output"}
                                    ]
                                }
                            },
                        }
                    })
                    return
                if parsed.path == "/view":
                    qs = parse_qs(parsed.query)
                    filename = (qs.get("filename") or [""])[0]
                    if filename == "fake_output.mp4" or filename in server.uploaded_images:
                        data = server._tiny_mp4
                        self.send_response(200)
                        self.send_header("Content-Type", "video/mp4")
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                parsed = urlparse(self.path)
                if parsed.path == "/prompt":
                    server.prompt_call_count += 1
                    body = json.loads(self._read_body() or b"{}")
                    server.last_submitted_workflow = body.get("prompt")
                    server.last_client_id = body.get("client_id")
                    if server.reject_with_node_errors is not None:
                        self._send_json({"node_errors": server.reject_with_node_errors}, status=200)
                        return
                    prompt_id = uuid.uuid4().hex
                    server.jobs[prompt_id] = {"status": "queued", "poll_count": 0, "cancelled": False}
                    self._send_json({"prompt_id": prompt_id, "number": 1, "node_errors": {}})
                    return
                if parsed.path == "/interrupt":
                    for job in server.jobs.values():
                        if job["status"] in ("queued", "running"):
                            job["cancelled"] = True
                    self._send_json({})
                    return
                if parsed.path == "/queue":
                    body = json.loads(self._read_body() or b"{}")
                    for pid in body.get("delete", []):
                        if pid in server.jobs:
                            server.jobs[pid]["cancelled"] = True
                    self._send_json({})
                    return
                if parsed.path == "/upload/image":
                    server.upload_calls += 1
                    content_type = self.headers.get("Content-Type", "")
                    body = self._read_body()
                    filename = "uploaded.png"
                    if "filename=" in content_type or b"filename=" in body:
                        # extract filename from the multipart body (simple, test-only parser)
                        marker = b'filename="'
                        idx = body.find(marker)
                        if idx != -1:
                            end = body.find(b'"', idx + len(marker))
                            filename = body[idx + len(marker):end].decode("utf-8", "ignore") or filename
                    server.uploaded_images[filename] = body
                    self._send_json({"name": filename, "subfolder": "", "type": "input"})
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
