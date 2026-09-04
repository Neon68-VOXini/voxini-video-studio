"""Tests for ComfyUIProvider against a real local fake ComfyUI HTTP server
(tests/fakes/fake_comfyui_server.py) bound to 127.0.0.1 on a free port. No
real ComfyUI, GPU, or internet access is involved or required - every
request in these tests goes to our own in-process fake server, so this
suite makes zero external network calls and costs nothing to run, per the
project's hard "no paid/real API calls during development" constraint.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from voxini_studio.models.project import AspectRatio, Scene
from voxini_studio.providers.base import GenerationRequest
from voxini_studio.providers.comfyui_provider import (
    ComfyUIAPIError,
    ComfyUIJobCancelled,
    ComfyUIProvider,
)

from tests.fakes.fake_comfyui_server import FakeComfyUIServer


@pytest.fixture()
def server():
    s = FakeComfyUIServer()
    s.start()
    yield s
    s.stop()


@pytest.fixture()
def provider(server, tmp_path):
    return ComfyUIProvider(
        host="127.0.0.1", port=server.port, resolution="480p",
        upscale_to_1080p=True, workflows_dir=None,
    )


def _scene(duration: float = 3.0) -> Scene:
    return Scene(order=1, label="TEST", start_seconds=0.0, end_seconds=duration, prompt_text="A calm lake at dawn")


def _make_test_png(tmp_path) -> str:
    from PIL import Image

    path = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), color=(10, 20, 30)).save(path)
    return str(path)


# -- connection --------------------------------------------------------

def test_check_connection_success(provider):
    data = provider.check_connection()
    assert data["system"]["comfyui_version"] == "fake-0.0"


def test_check_connection_unreachable_raises():
    p = ComfyUIProvider(host="127.0.0.1", port=1)  # nothing listens on port 1
    with pytest.raises(ComfyUIAPIError):
        p.check_connection()


# -- workflow templates --------------------------------------------------

def test_load_text_to_video_template_has_no_image_node(provider):
    tpl = provider.load_workflow_template(image_mode=False)
    assert "56" not in tpl
    assert "start_image" not in json.dumps(tpl["55"])


def test_load_image_to_video_template_has_image_node(provider):
    tpl = provider.load_workflow_template(image_mode=True)
    assert tpl["56"]["class_type"] == "LoadImage"
    assert tpl["55"]["inputs"]["start_image"] == ["56", 0]


def test_fill_template_substitutes_all_tokens(provider):
    tpl = provider.load_workflow_template(image_mode=False)
    filled = provider._fill_template(
        tpl, prompt="a red fox", negative_prompt="blurry", width=832, height=480,
        frames=49, fps=24, seed=42, filename_prefix="voxini_test",
    )
    assert filled["6"]["inputs"]["text"] == "a red fox"
    assert filled["7"]["inputs"]["text"] == "blurry"
    assert filled["55"]["inputs"]["width"] == 832
    assert filled["55"]["inputs"]["height"] == 480
    assert filled["55"]["inputs"]["length"] == 49
    assert filled["3"]["inputs"]["seed"] == 42
    assert filled["58"]["inputs"]["filename_prefix"] == "voxini_test"
    assert "_comment" not in filled


# -- job submission / polling / cancellation -----------------------------

def test_submit_job_returns_prompt_id(provider):
    tpl = provider.load_workflow_template(image_mode=False)
    filled = provider._fill_template(
        tpl, prompt="x", negative_prompt="y", width=832, height=480,
        frames=25, fps=24, seed=1, filename_prefix="p",
    )
    prompt_id = provider.submit_job(filled)
    assert prompt_id


def test_submit_job_raises_on_node_errors(provider, server):
    server.reject_with_node_errors = {"55": {"errors": ["bad width"]}}
    tpl = provider.load_workflow_template(image_mode=False)
    filled = provider._fill_template(
        tpl, prompt="x", negative_prompt="y", width=832, height=480,
        frames=25, fps=24, seed=1, filename_prefix="p",
    )
    with pytest.raises(ComfyUIAPIError):
        provider.submit_job(filled)


def test_wait_for_job_polls_until_complete(provider, server):
    server.polls_before_complete = 3
    tpl = provider.load_workflow_template(image_mode=False)
    filled = provider._fill_template(
        tpl, prompt="x", negative_prompt="y", width=832, height=480,
        frames=25, fps=24, seed=1, filename_prefix="p",
    )
    prompt_id = provider.submit_job(filled)
    calls = {"n": 0}

    def fake_sleep(_):
        calls["n"] += 1

    entry = provider.wait_for_job(prompt_id, poll_interval=0, sleep=fake_sleep)
    assert entry["status"]["status_str"] == "success"
    assert calls["n"] >= 3


def test_wait_for_job_raises_on_server_failure(provider, server):
    server.fail_job = True
    tpl = provider.load_workflow_template(image_mode=False)
    filled = provider._fill_template(
        tpl, prompt="x", negative_prompt="y", width=832, height=480,
        frames=25, fps=24, seed=1, filename_prefix="p",
    )
    prompt_id = provider.submit_job(filled)
    with pytest.raises(ComfyUIAPIError):
        provider.wait_for_job(prompt_id, poll_interval=0, sleep=lambda _: None)


def test_wait_for_job_cancel_check_stops_and_cancels(provider, server):
    tpl = provider.load_workflow_template(image_mode=False)
    filled = provider._fill_template(
        tpl, prompt="x", negative_prompt="y", width=832, height=480,
        frames=25, fps=24, seed=1, filename_prefix="p",
    )
    prompt_id = provider.submit_job(filled)
    with pytest.raises(ComfyUIJobCancelled):
        provider.wait_for_job(
            prompt_id, poll_interval=0, sleep=lambda _: None, cancel_check=lambda: True
        )
    assert server.jobs[prompt_id]["cancelled"] is True


def test_cancel_job_marks_job_cancelled(provider, server):
    tpl = provider.load_workflow_template(image_mode=False)
    filled = provider._fill_template(
        tpl, prompt="x", negative_prompt="y", width=832, height=480,
        frames=25, fps=24, seed=1, filename_prefix="p",
    )
    prompt_id = provider.submit_job(filled)
    provider.cancel_job(prompt_id)
    assert server.jobs[prompt_id]["cancelled"] is True


# -- image upload ---------------------------------------------------------

def test_upload_image_returns_server_filename(provider, server, tmp_path):
    img_path = _make_test_png(tmp_path)
    name = provider.upload_image(img_path)
    assert name
    assert server.upload_calls == 1
    assert name in server.uploaded_images


def test_upload_image_missing_file_raises(provider, tmp_path):
    with pytest.raises(ComfyUIAPIError):
        provider.upload_image(tmp_path / "does_not_exist.png")


# -- download / upscale ----------------------------------------------------

def test_download_output_writes_bytes(provider, tmp_path):
    dest = tmp_path / "out.mp4"
    provider._download_output("fake_output.mp4", "", "output", dest)
    assert dest.exists()
    assert dest.stat().st_size > 0


def test_upscale_local_produces_1080_height(provider, tmp_path):
    src = tmp_path / "in.mp4"
    provider._download_output("fake_output.mp4", "", "output", src)
    dest = tmp_path / "out_1080.mp4"
    provider._upscale_local(src, dest, target_height=1080)
    assert dest.exists()
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=height", "-of", "json", str(dest)],
        capture_output=True, text=True,
    )
    height = json.loads(probe.stdout)["streams"][0]["height"]
    assert height == 1080


# -- full generate() end-to-end -------------------------------------------

def test_generate_text_to_video_success(provider, tmp_path):
    dest = tmp_path / "clip.mp4"
    scene = _scene(duration=2.0)
    request = GenerationRequest(
        scene=scene, resolved_prompt=scene.prompt_text, character_reference_paths=[],
        aspect_ratio=AspectRatio.WIDESCREEN, dest_path=dest,
    )
    result = provider.generate(request)
    assert result.success is True, result.error_message
    assert result.actual_cost == 0.0
    assert dest.exists()


def test_generate_image_to_video_uploads_reference(provider, server, tmp_path):
    img_path = _make_test_png(tmp_path)
    dest = tmp_path / "clip_i2v.mp4"
    scene = _scene(duration=2.0)
    request = GenerationRequest(
        scene=scene, resolved_prompt=scene.prompt_text,
        character_reference_paths=[img_path],
        aspect_ratio=AspectRatio.VERTICAL, dest_path=dest,
    )
    result = provider.generate(request)
    assert result.success is True, result.error_message
    assert server.upload_calls == 1
    assert "56" in server.last_submitted_workflow
    assert server.last_submitted_workflow["55"]["inputs"]["start_image"] == ["56", 0]


def test_generate_failure_is_reported_not_raised(tmp_path):
    p = ComfyUIProvider(host="127.0.0.1", port=1)  # unreachable
    scene = _scene()
    request = GenerationRequest(
        scene=scene, resolved_prompt=scene.prompt_text, character_reference_paths=[],
        aspect_ratio=AspectRatio.WIDESCREEN, dest_path=tmp_path / "clip.mp4",
    )
    result = p.generate(request)
    assert result.success is False
    assert result.error_message


def test_generate_no_upscale_downloads_raw(server, tmp_path):
    p = ComfyUIProvider(host="127.0.0.1", port=server.port, upscale_to_1080p=False)
    dest = tmp_path / "clip_raw.mp4"
    scene = _scene(duration=1.0)
    request = GenerationRequest(
        scene=scene, resolved_prompt=scene.prompt_text, character_reference_paths=[],
        aspect_ratio=AspectRatio.SQUARE, dest_path=dest,
    )
    result = p.generate(request)
    assert result.success is True, result.error_message
    assert dest.exists()


def test_provider_info_marks_free_and_local():
    p = ComfyUIProvider()
    info = p.info()
    assert info.price_per_second == 0.0
    assert info.requires_api_key is False
    assert AspectRatio.VERTICAL in info.supported_aspect_ratios


# -- identity-scene pipeline (SDXL + InstantID, see
# Project.comfyui_identity_scene_mode) -------------------------------------

def test_load_identity_template_has_expected_nodes(provider):
    tpl = provider.load_identity_template()
    assert tpl["1"]["class_type"] == "CheckpointLoaderSimple"
    assert tpl["2"]["class_type"] == "InstantIDModelLoader"
    assert tpl["3"]["class_type"] == "InstantIDFaceAnalysis"
    assert tpl["4"]["class_type"] == "ControlNetLoader"
    assert tpl["5"]["class_type"] == "LoadImage"
    assert tpl["8"]["class_type"] == "ApplyInstantID"
    assert tpl["12"]["class_type"] == "SaveImage"


def test_fill_identity_template_substitutes_checkpoint_and_tokens(provider):
    tpl = provider.load_identity_template()
    filled = provider._fill_template(
        tpl, prompt="on a neon stage", negative_prompt="blurry",
        width=1216, height=704, seed=7, filename_prefix="voxini_identity_test",
        image_filename="face.png", checkpoint_name="my_sdxl.safetensors",
    )
    assert filled["1"]["inputs"]["ckpt_name"] == "my_sdxl.safetensors"
    assert filled["5"]["inputs"]["image"] == "face.png"
    assert filled["6"]["inputs"]["text"] == "on a neon stage"
    assert filled["7"]["inputs"]["text"] == "blurry"
    assert filled["9"]["inputs"]["width"] == 1216
    assert filled["9"]["inputs"]["height"] == 704
    assert filled["10"]["inputs"]["seed"] == 7
    assert filled["12"]["inputs"]["filename_prefix"] == "voxini_identity_test"
    assert "_comment" not in filled


def test_generate_with_identity_scene_mode_runs_two_stage_pipeline(server, tmp_path):
    """With identity_scene_mode on, a scene with a reference photo must
    submit TWO ComfyUI jobs (stage 1 identity image, stage 2 image-to-video)
    and upload TWO images (the face reference, then the freshly generated
    identity-scene image) - not one, like plain image-to-video does. The
    fake server doesn't model InstantID's real node graph (it just always
    returns the same canned 'fake_output.mp4' history entry regardless of
    the submitted workflow), so this only verifies the two-stage plumbing
    (submit/poll/download/upload wiring, VRAM free between stages), not
    actual InstantID output quality - that needs a real ComfyUI + the
    InstantID custom node, which this sandbox does not have."""
    p = ComfyUIProvider(
        host="127.0.0.1", port=server.port, resolution="480p",
        identity_scene_mode=True, identity_checkpoint="sd_xl_base_1.0.safetensors",
    )
    img_path = _make_test_png(tmp_path)
    dest = tmp_path / "clip_identity.mp4"
    scene = _scene(duration=2.0)
    request = GenerationRequest(
        scene=scene, resolved_prompt=scene.prompt_text,
        character_reference_paths=[img_path],
        aspect_ratio=AspectRatio.WIDESCREEN, dest_path=dest,
    )
    result = p.generate(request)
    assert result.success is True, result.error_message
    assert dest.exists()
    assert server.prompt_call_count == 2
    assert server.upload_calls == 2
    # stage 2's workflow (the last one submitted) must be the plain
    # image-to-video graph, fed with the stage-1 output - not the raw
    # reference photo directly, and not the identity-stage graph itself.
    assert "56" in server.last_submitted_workflow
    assert server.last_submitted_workflow["55"]["inputs"]["start_image"] == ["56", 0]


def test_generate_identity_scene_mode_off_uploads_reference_directly_once(server, tmp_path):
    """Regression guard: with identity_scene_mode left at its default
    (False), behaviour must be byte-for-byte the old single-stage
    image-to-video path - one job, one upload - so existing projects are
    completely unaffected by this feature's addition."""
    p = ComfyUIProvider(host="127.0.0.1", port=server.port, resolution="480p")
    assert p.identity_scene_mode is False
    img_path = _make_test_png(tmp_path)
    dest = tmp_path / "clip_plain.mp4"
    scene = _scene(duration=2.0)
    request = GenerationRequest(
        scene=scene, resolved_prompt=scene.prompt_text,
        character_reference_paths=[img_path],
        aspect_ratio=AspectRatio.WIDESCREEN, dest_path=dest,
    )
    result = p.generate(request)
    assert result.success is True, result.error_message
    assert server.prompt_call_count == 1
    assert server.upload_calls == 1
