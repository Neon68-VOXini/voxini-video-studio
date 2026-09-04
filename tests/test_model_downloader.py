"""Tests for the model downloader against a real local fake HTTP file
server bound to 127.0.0.1 on a free port - no real internet access, no
Hugging Face request, zero cost. Verifies the exact behaviors the setup
wizard depends on: real size via HEAD, streamed download with progress,
mid-download cancellation leaving no partial file at the final name, and
clean error handling for a missing file."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from voxini_studio.core.model_downloader import (
    DownloadError,
    download_file,
    format_size,
    remote_file_size,
)

FILE_BYTES = b"VOXINI-TEST-MODEL-BYTES" * 10000  # a few hundred KB


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_HEAD(self):
        if self.path == "/model.safetensors":
            self.send_response(200)
            self.send_header("Content-Length", str(len(FILE_BYTES)))
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/model.safetensors":
            self.send_response(200)
            self.send_header("Content-Length", str(len(FILE_BYTES)))
            self.end_headers()
            # write in small pieces so cancel_check has multiple chances to fire
            for i in range(0, len(FILE_BYTES), 4096):
                self.wfile.write(FILE_BYTES[i:i + 4096])
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _url(server, path: str) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


def test_remote_file_size_returns_real_size(server):
    size = remote_file_size(_url(server, "/model.safetensors"))
    assert size == len(FILE_BYTES)


def test_remote_file_size_returns_none_for_missing_file(server):
    size = remote_file_size(_url(server, "/does-not-exist"))
    assert size is None


def test_download_file_writes_full_content(server, tmp_path):
    dest = tmp_path / "model.safetensors"
    result = download_file(_url(server, "/model.safetensors"), dest)
    assert result == dest
    assert dest.exists()
    assert dest.read_bytes() == FILE_BYTES
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_download_file_reports_progress(server, tmp_path):
    dest = tmp_path / "model.safetensors"
    calls = []
    download_file(
        _url(server, "/model.safetensors"), dest,
        progress_callback=lambda done, total: calls.append((done, total)),
        chunk_size=4096,
    )
    assert len(calls) > 1
    assert calls[-1][0] == len(FILE_BYTES)
    assert calls[-1][1] == len(FILE_BYTES)


def test_download_file_cancel_leaves_no_final_file(server, tmp_path):
    dest = tmp_path / "model.safetensors"
    call_count = {"n": 0}

    def cancel_after_first_chunk():
        call_count["n"] += 1
        return call_count["n"] > 1

    with pytest.raises(DownloadError):
        download_file(
            _url(server, "/model.safetensors"), dest,
            cancel_check=cancel_after_first_chunk, chunk_size=4096,
        )
    assert not dest.exists()
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_download_file_404_raises_download_error(server, tmp_path):
    dest = tmp_path / "missing.safetensors"
    with pytest.raises(DownloadError):
        download_file(_url(server, "/does-not-exist"), dest)
    assert not dest.exists()


def test_format_size_gb_and_mb():
    assert format_size(2 * 1024 ** 3).endswith("GB")
    assert format_size(50 * 1024 ** 2).endswith("MB")
    assert format_size(None) == "unbekannte Größe"
    assert format_size(0) == "unbekannte Größe"
