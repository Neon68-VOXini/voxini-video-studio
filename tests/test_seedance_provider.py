"""Tests for SeedanceProvider against a real local fake BytePlus/Ark HTTP
server (tests/fakes/fake_seedance_server.py) bound to 127.0.0.1 on a free
port. No real BytePlus/Volcengine account, API key, or internet access is
used or required - every request goes to our own in-process fake
implementing the ASSUMED schema documented in seedance_provider.py's
module docstring. Zero real network calls, zero cost.
"""
from __future__ import annotations

import pytest

from voxini_studio.core import credentials
from voxini_studio.models.project import AspectRatio, Scene
from voxini_studio.providers.base import GenerationRequest
from voxini_studio.providers.seedance_provider import (
    SeedanceAPIError,
    SeedanceProvider,
)

from tests.fakes.fake_seedance_server import FakeSeedanceServer

API_KEY = "test-seedance-key"


@pytest.fixture()
def server():
    s = FakeSeedanceServer(expected_api_key=API_KEY)
    s.start()
    yield s
    s.stop()


@pytest.fixture()
def provider(server):
    return SeedanceProvider(
        model_id="doubao-seedance-1-0-lite-i2v-250428", api_key=API_KEY, base_url=server.base_url
    )


def _scene(duration: float = 5.0) -> Scene:
    return Scene(order=1, label="TEST", start_seconds=0.0, end_seconds=duration, prompt_text="Two friends at a cafe")


def _make_test_png(tmp_path, name="ref.png") -> str:
    from PIL import Image

    path = tmp_path / name
    Image.new("RGB", (64, 64), color=(5, 5, 5)).save(path)
    return str(path)


# -- auth --------------------------------------------------------------

def test_no_key_raises_without_network_call(server, monkeypatch):
    monkeypatch.setattr(credentials, "get_api_key", lambda provider_id: None)
    p = SeedanceProvider(api_key=None, base_url=server.base_url)
    with pytest.raises(SeedanceAPIError):
        p.submit_task(prompt="x", image_paths=[], ratio="16:9", duration=5)


def test_provider_falls_back_to_stored_credentials(server, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    credentials.set_api_key("seedance", API_KEY)
    p = SeedanceProvider(api_key=None, base_url=server.base_url)
    task_id = p.submit_task(prompt="x", image_paths=[], ratio="16:9", duration=5)
    assert task_id


# -- generation ----------------------------------------------------------

def test_generate_success_downloads_video(provider, tmp_path):
    ref1 = _make_test_png(tmp_path, "char1.png")
    ref2 = _make_test_png(tmp_path, "char2.png")
    dest = tmp_path / "out.mp4"
    request = GenerationRequest(
        scene=_scene(),
        resolved_prompt="Two friends meet in a cafe",
        character_reference_paths=[ref1, ref2],
        aspect_ratio=AspectRatio.WIDESCREEN,
        dest_path=dest,
    )
    result = provider.generate(request)
    assert result.success, result.error_message
    assert dest.exists()
    assert dest.stat().st_size > 0


def test_generate_sends_text_and_image_content_items(provider, server, tmp_path):
    ref1 = _make_test_png(tmp_path, "char1.png")
    dest = tmp_path / "out.mp4"
    request = GenerationRequest(
        scene=_scene(), resolved_prompt="A scene", character_reference_paths=[ref1],
        aspect_ratio=AspectRatio.WIDESCREEN, dest_path=dest,
    )
    provider.generate(request)
    assert len(server.submit_calls) == 1
    content = server.submit_calls[0]["content"]
    types = [c["type"] for c in content]
    assert types[0] == "text"
    assert "image_url" in types


def test_generate_without_reference_images_still_submits_text_to_video(provider, tmp_path):
    dest = tmp_path / "out.mp4"
    request = GenerationRequest(
        scene=_scene(), resolved_prompt="A skyline at dusk", character_reference_paths=[],
        aspect_ratio=AspectRatio.WIDESCREEN, dest_path=dest,
    )
    result = provider.generate(request)
    assert result.success, result.error_message


def test_generate_handles_task_failure(provider, server, tmp_path):
    server.fail_task = True
    ref = _make_test_png(tmp_path)
    dest = tmp_path / "out.mp4"
    request = GenerationRequest(
        scene=_scene(), resolved_prompt="x", character_reference_paths=[ref],
        aspect_ratio=AspectRatio.WIDESCREEN, dest_path=dest,
    )
    result = provider.generate(request)
    assert not result.success


def test_reference_image_limit_caps_at_model_max(provider, server, tmp_path):
    paths = [_make_test_png(tmp_path, f"c{i}.png") for i in range(6)]
    dest = tmp_path / "out.mp4"
    request = GenerationRequest(
        scene=_scene(), resolved_prompt="x", character_reference_paths=paths,
        aspect_ratio=AspectRatio.WIDESCREEN, dest_path=dest,
    )
    provider.generate(request)
    content = server.submit_calls[-1]["content"]
    image_items = [c for c in content if c["type"] == "image_url"]
    assert len(image_items) == provider.max_simultaneous_references
