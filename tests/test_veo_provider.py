"""Tests for VeoProvider against a real local fake Gemini-API HTTP server
(tests/fakes/fake_veo_server.py) bound to 127.0.0.1 on a free port,
implementing the OFFICIAL documented schema (see veo_provider.py's module
docstring) for the Veo 3.1 predictLongRunning/operation flow. No real
Google/Gemini account, API key, or internet access is used or required.
Zero real network calls, zero cost.
"""
from __future__ import annotations

import pytest

from voxini_studio.core import credentials
from voxini_studio.models.project import AspectRatio, Scene
from voxini_studio.providers.base import GenerationRequest
from voxini_studio.providers.veo_provider import VeoAPIError, VeoProvider

from tests.fakes.fake_veo_server import FakeVeoServer

API_KEY = "test-veo-key"


@pytest.fixture()
def server():
    s = FakeVeoServer(expected_api_key=API_KEY)
    s.start()
    yield s
    s.stop()


@pytest.fixture()
def provider(server):
    return VeoProvider(model_id="veo-3.1-generate-preview", api_key=API_KEY, base_url=server.base_url)


def _scene(duration: float = 8.0) -> Scene:
    return Scene(order=1, label="TEST", start_seconds=0.0, end_seconds=duration, prompt_text="Two people in dialogue")


def _make_test_png(tmp_path, name="ref.png") -> str:
    from PIL import Image

    path = tmp_path / name
    Image.new("RGB", (64, 64), color=(5, 5, 5)).save(path)
    return str(path)


# -- auth --------------------------------------------------------------

def test_no_key_raises_without_network_call(server, monkeypatch):
    monkeypatch.setattr(credentials, "get_api_key", lambda provider_id: None)
    p = VeoProvider(api_key=None, base_url=server.base_url)
    with pytest.raises(VeoAPIError):
        p.submit(prompt="x", image_paths=[], aspect_ratio="16:9")


def test_wrong_key_raises_401(server):
    p = VeoProvider(api_key="wrong", base_url=server.base_url)
    with pytest.raises(VeoAPIError):
        p.submit(prompt="x", image_paths=[], aspect_ratio="16:9")


def test_provider_falls_back_to_stored_credentials(server, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    credentials.set_api_key("veo", API_KEY)
    p = VeoProvider(api_key=None, base_url=server.base_url)
    op_name = p.submit(prompt="x", image_paths=[], aspect_ratio="16:9")
    assert op_name.startswith("operations/")


# -- generation ----------------------------------------------------------

def test_generate_success_downloads_video(provider, tmp_path):
    ref1 = _make_test_png(tmp_path, "char1.png")
    ref2 = _make_test_png(tmp_path, "char2.png")
    dest = tmp_path / "out.mp4"
    request = GenerationRequest(
        scene=_scene(),
        resolved_prompt="Two friends meet in a park",
        character_reference_paths=[ref1, ref2],
        aspect_ratio=AspectRatio.WIDESCREEN,
        dest_path=dest,
    )
    result = provider.generate(request)
    assert result.success, result.error_message
    assert dest.exists()
    assert dest.stat().st_size > 0


def test_generate_sends_reference_images_field(provider, server, tmp_path):
    ref1 = _make_test_png(tmp_path, "char1.png")
    ref2 = _make_test_png(tmp_path, "char2.png")
    ref3 = _make_test_png(tmp_path, "char3.png")
    ref4 = _make_test_png(tmp_path, "char4.png")  # should be capped at 3
    dest = tmp_path / "out.mp4"
    request = GenerationRequest(
        scene=_scene(), resolved_prompt="A scene", character_reference_paths=[ref1, ref2, ref3, ref4],
        aspect_ratio=AspectRatio.WIDESCREEN, dest_path=dest,
    )
    provider.generate(request)
    body = server.submit_calls[-1]
    instance = body["instances"][0]
    assert len(instance["referenceImages"]) == 3
    assert body["parameters"]["durationSeconds"] == "8"
    assert body["parameters"]["personGeneration"] == "allow_adult"


def test_generate_without_reference_images_uses_allow_all(provider, server, tmp_path):
    dest = tmp_path / "out.mp4"
    request = GenerationRequest(
        scene=_scene(), resolved_prompt="A skyline at dusk", character_reference_paths=[],
        aspect_ratio=AspectRatio.WIDESCREEN, dest_path=dest,
    )
    result = provider.generate(request)
    assert result.success, result.error_message
    body = server.submit_calls[-1]
    assert body["parameters"]["personGeneration"] == "allow_all"
    assert "referenceImages" not in body["instances"][0]


def test_generate_handles_task_failure(provider, server, tmp_path):
    server.fail_task = True
    dest = tmp_path / "out.mp4"
    request = GenerationRequest(
        scene=_scene(), resolved_prompt="x", character_reference_paths=[],
        aspect_ratio=AspectRatio.WIDESCREEN, dest_path=dest,
    )
    result = provider.generate(request)
    assert not result.success


def test_polling_waits_for_done(provider, server, tmp_path):
    server.polls_before_complete = 3
    op_name = provider.submit(prompt="x", image_paths=[], aspect_ratio="16:9")
    calls = []
    op = provider.wait_for_operation(op_name, sleep=lambda s: calls.append(s), poll_interval=0.01)
    assert op["done"] is True
    assert len(calls) == 3
