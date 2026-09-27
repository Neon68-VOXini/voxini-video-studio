"""Tests for KlingProvider against a real local fake Kling HTTP server
(tests/fakes/fake_kling_server.py) bound to 127.0.0.1 on a free port. No
real Kling account, API key, or internet access is used or required -
every request goes to our own in-process fake implementing the ASSUMED
schema documented in kling_provider.py's module docstring (see its
honesty note: Kling's own docs page could not be fetched with real content
during development). Zero real network calls, zero cost.
"""
from __future__ import annotations

import pytest

from voxini_studio.core import credentials
from voxini_studio.models.project import AspectRatio, Scene
from voxini_studio.providers.base import GenerationRequest
from voxini_studio.providers.kling_provider import (
    KlingAPIError,
    KlingProvider,
    build_jwt,
)

from tests.fakes.fake_kling_server import FakeKlingServer


@pytest.fixture()
def server():
    s = FakeKlingServer()
    s.start()
    yield s
    s.stop()


@pytest.fixture()
def provider(server):
    return KlingProvider(
        model_id="kling-v3-0", access_key="ak_test", secret_key="sk_test", base_url=server.base_url
    )


def _scene(duration: float = 5.0) -> Scene:
    return Scene(order=1, label="TEST", start_seconds=0.0, end_seconds=duration, prompt_text="Two people talking")


def _make_test_png(tmp_path, name="ref.png") -> str:
    from PIL import Image

    path = tmp_path / name
    Image.new("RGB", (64, 64), color=(5, 5, 5)).save(path)
    return str(path)


# -- auth --------------------------------------------------------------

def test_build_jwt_has_three_dot_separated_parts():
    token = build_jwt("ak", "sk")
    assert token.count(".") == 2


def test_check_connection_success(provider):
    info = provider.check_connection()
    assert info["code"] == 0


def test_no_credentials_raises_without_network_call(server, monkeypatch):
    monkeypatch.setattr(credentials, "get_api_key", lambda provider_id: None)
    p = KlingProvider(access_key=None, secret_key=None, base_url=server.base_url)
    with pytest.raises(KlingAPIError):
        p.check_connection()


def test_provider_falls_back_to_stored_credentials(server, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    credentials.set_api_key("kling", "ak_stored:sk_stored")
    p = KlingProvider(access_key=None, secret_key=None, base_url=server.base_url)
    info = p.check_connection()
    assert info["code"] == 0


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
    assert result.actual_cost > 0


def test_generate_sends_all_reference_images(provider, server, tmp_path):
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
    provider.generate(request)
    assert len(server.submit_calls) == 1
    body = server.submit_calls[0]
    assert "image_0" in body and "image_1" in body


def test_generate_without_reference_image_fails_cleanly(provider, tmp_path):
    dest = tmp_path / "out.mp4"
    request = GenerationRequest(
        scene=_scene(),
        resolved_prompt="A city skyline",
        character_reference_paths=[],
        aspect_ratio=AspectRatio.WIDESCREEN,
        dest_path=dest,
    )
    result = provider.generate(request)
    assert not result.success
    assert not dest.exists()


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
    assert "fehlgeschlagen" in result.error_message.lower() or "SIMULATED_FAILURE" in result.error_message


def test_generate_polls_until_ready(provider, server, tmp_path):
    server.polls_before_complete = 2
    ref = _make_test_png(tmp_path)

    task_id = provider.submit_multi_image_to_video(
        prompt="x", image_paths=[ref], aspect_ratio="16:9", duration=5,
    )
    calls = []
    task = provider.wait_for_task(task_id, sleep=lambda s: calls.append(s), poll_interval=0.01)
    assert task["task_status"] == "succeed"
    assert len(calls) == 2


def test_model_max_references_caps_images(server):
    p = KlingProvider(model_id="kling-v3-0", access_key="ak", secret_key="sk", base_url=server.base_url)
    assert p.max_simultaneous_references == 3
    p2 = KlingProvider(model_id="kling-v1-6", access_key="ak", secret_key="sk", base_url=server.base_url)
    assert p2.max_simultaneous_references == 4
