"""Tests for RunwayProvider against a real local fake Runway HTTP server
(tests/fakes/fake_runway_server.py) bound to 127.0.0.1 on a free port.
No real Runway account, API key, or internet access is used or required -
every request goes to our own in-process fake, matching the officially
documented Runway API response shapes. Zero real network calls, zero cost.
"""
from __future__ import annotations

import pytest

from voxini_studio.core import credentials
from voxini_studio.models.project import AspectRatio, Scene
from voxini_studio.providers.base import GenerationRequest
from voxini_studio.providers.runway_provider import (
    RunwayAPIError,
    RunwayJobCancelled,
    RunwayProvider,
)

from tests.fakes.fake_runway_server import FakeRunwayServer

API_KEY = "test-runway-key"


@pytest.fixture()
def server():
    s = FakeRunwayServer(expected_api_key=API_KEY)
    s.start()
    yield s
    s.stop()


@pytest.fixture()
def provider(server):
    return RunwayProvider(model_id="gen4.5", api_key=API_KEY, base_url=server.base_url)


def _scene(duration: float = 5.0) -> Scene:
    return Scene(order=1, label="TEST", start_seconds=0.0, end_seconds=duration, prompt_text="A neon city at night")


def _make_test_png(tmp_path) -> str:
    from PIL import Image

    path = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), color=(5, 5, 5)).save(path)
    return str(path)


# -- auth / connection ---------------------------------------------------

def test_check_connection_success_returns_org_info(provider):
    info = provider.check_connection()
    assert info["creditBalance"] == 5000


def test_check_connection_wrong_key_raises_401(server):
    p = RunwayProvider(api_key="wrong-key", base_url=server.base_url)
    with pytest.raises(RunwayAPIError):
        p.check_connection()


def test_check_connection_no_key_raises_without_network_call(server, monkeypatch):
    monkeypatch.setattr(credentials, "get_runway_api_key", lambda: None)
    p = RunwayProvider(api_key=None, base_url=server.base_url)
    with pytest.raises(RunwayAPIError):
        p.check_connection()


def test_credit_balance_reads_org_info(provider):
    assert provider.credit_balance() == 5000


def test_provider_falls_back_to_stored_credentials(server, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    credentials.set_runway_api_key(API_KEY)
    p = RunwayProvider(api_key=None, base_url=server.base_url)
    info = p.check_connection()
    assert info["creditBalance"] == 5000


# -- cost estimation -------------------------------------------------------

def test_estimate_cost_gen4_turbo():
    p = RunwayProvider(model_id="gen4_turbo", api_key=API_KEY)
    # 5 credits/sec * 5s * $0.01 = $0.25
    assert p.estimate_cost(5.0) == pytest.approx(0.25)


def test_estimate_cost_uses_closest_allowed_duration_for_veo3_1():
    p = RunwayProvider(model_id="veo3.1", api_key=API_KEY)
    # veo3.1 only allows durations 4/6/8, so a 3s scene rounds up to 4s
    assert p.estimate_cost(3.0) == pytest.approx(40.0 * 4 * 0.01)


def test_price_per_second_reflects_selected_model():
    cheap = RunwayProvider(model_id="gen4_turbo", api_key=API_KEY)
    expensive = RunwayProvider(model_id="veo3.1", api_key=API_KEY)
    assert cheap.price_per_second < expensive.price_per_second


# -- upload ----------------------------------------------------------------

def test_upload_image_returns_runway_uri(provider, server, tmp_path):
    img = _make_test_png(tmp_path)
    uri = provider.upload_image(img)
    assert uri.startswith("runway://")
    assert server.upload_calls == 1


def test_upload_image_missing_file_raises(provider, tmp_path):
    with pytest.raises(RunwayAPIError):
        provider.upload_image(tmp_path / "missing.png")


# -- submission / polling / cancellation -----------------------------------

def test_submit_text_to_video_returns_task_id(provider, server):
    task_id = provider.submit_text_to_video("a sunset", ratio="1280:720", duration=5)
    assert task_id
    assert server.submit_calls[-1]["endpoint"] == "/v1/text_to_video"
    assert server.submit_calls[-1]["body"]["model"] == "gen4.5"


def test_submit_text_to_video_rejects_image_only_model(provider):
    with pytest.raises(RunwayAPIError):
        provider.submit_text_to_video("a sunset", ratio="1280:720", duration=5, model="gen4_turbo")


def test_submit_image_to_video_sends_prompt_image(provider, server):
    task_id = provider.submit_image_to_video(
        "animate this", image_uri="runway://fake/abc", ratio="1280:720", duration=5
    )
    assert task_id
    assert server.submit_calls[-1]["body"]["promptImage"] == "runway://fake/abc"


def test_wait_for_task_polls_until_succeeded(provider, server):
    server.polls_before_complete = 2
    task_id = provider.submit_text_to_video("x", ratio="1280:720", duration=5)
    calls = {"n": 0}
    task = provider.wait_for_task(task_id, poll_interval=0, sleep=lambda _: calls.__setitem__("n", calls["n"] + 1))
    assert task["status"] == "SUCCEEDED"
    assert calls["n"] >= 2


def test_wait_for_task_raises_on_failure(provider, server):
    server.fail_task = True
    task_id = provider.submit_text_to_video("x", ratio="1280:720", duration=5)
    with pytest.raises(RunwayAPIError):
        provider.wait_for_task(task_id, poll_interval=0, sleep=lambda _: None)


def test_wait_for_task_cancel_check_cancels(provider, server):
    task_id = provider.submit_text_to_video("x", ratio="1280:720", duration=5)
    with pytest.raises(RunwayJobCancelled):
        provider.wait_for_task(task_id, poll_interval=0, sleep=lambda _: None, cancel_check=lambda: True)
    assert task_id in server.cancel_calls


# -- full generate() end-to-end --------------------------------------------

def test_generate_text_to_video_success(provider, tmp_path):
    dest = tmp_path / "clip.mp4"
    scene = _scene(duration=5.0)
    request = GenerationRequest(
        scene=scene, resolved_prompt=scene.prompt_text, character_reference_paths=[],
        aspect_ratio=AspectRatio.WIDESCREEN, dest_path=dest,
    )
    result = provider.generate(request)
    assert result.success is True, result.error_message
    assert dest.exists() and dest.stat().st_size > 0
    assert result.actual_cost == pytest.approx(12.0 * 5 * 0.01)


def test_generate_image_to_video_uploads_reference(provider, server, tmp_path):
    img = _make_test_png(tmp_path)
    dest = tmp_path / "clip_i2v.mp4"
    scene = _scene(duration=5.0)
    request = GenerationRequest(
        scene=scene, resolved_prompt=scene.prompt_text, character_reference_paths=[img],
        aspect_ratio=AspectRatio.VERTICAL, dest_path=dest,
    )
    result = provider.generate(request)
    assert result.success is True, result.error_message
    assert server.upload_calls == 1
    assert server.submit_calls[-1]["endpoint"] == "/v1/image_to_video"


def test_generate_gen4_turbo_without_image_fails_clearly(server, tmp_path):
    p = RunwayProvider(model_id="gen4_turbo", api_key=API_KEY, base_url=server.base_url)
    scene = _scene()
    request = GenerationRequest(
        scene=scene, resolved_prompt=scene.prompt_text, character_reference_paths=[],
        aspect_ratio=AspectRatio.WIDESCREEN, dest_path=tmp_path / "clip.mp4",
    )
    result = p.generate(request)
    assert result.success is False
    assert "Referenzbild" in result.error_message


def test_generate_missing_api_key_reports_failure_not_raise(server, monkeypatch, tmp_path):
    monkeypatch.setattr(credentials, "get_runway_api_key", lambda: None)
    p = RunwayProvider(model_id="gen4.5", api_key=None, base_url=server.base_url)
    scene = _scene()
    request = GenerationRequest(
        scene=scene, resolved_prompt=scene.prompt_text, character_reference_paths=[],
        aspect_ratio=AspectRatio.WIDESCREEN, dest_path=tmp_path / "clip.mp4",
    )
    result = p.generate(request)
    assert result.success is False
    assert "Schluessel" in result.error_message or "API" in result.error_message


def test_generate_server_failure_is_reported_not_raised(server, tmp_path):
    server.fail_task = True
    p = RunwayProvider(model_id="gen4.5", api_key=API_KEY, base_url=server.base_url)
    scene = _scene()
    request = GenerationRequest(
        scene=scene, resolved_prompt=scene.prompt_text, character_reference_paths=[],
        aspect_ratio=AspectRatio.WIDESCREEN, dest_path=tmp_path / "clip.mp4",
    )
    result = p.generate(request)
    assert result.success is False
    assert result.error_message


def test_provider_info_marks_paid_and_requires_key():
    p = RunwayProvider(api_key=API_KEY)
    info = p.info()
    assert info.requires_api_key is True
    assert info.price_per_second > 0
