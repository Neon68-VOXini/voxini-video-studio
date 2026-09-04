import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from voxini_studio.core import ffmpeg_locator
from voxini_studio.core.generation_service import (
    budget_check,
    estimate_costs,
    generate_scene,
    generate_scenes,
    resolve_provider,
)
from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.models.project import Scene, SceneStatus
from voxini_studio.providers.comfyui_provider import ComfyUIProvider
from voxini_studio.providers.mock_provider import MockProvider
from voxini_studio.providers.registry import available_providers, get_provider
from voxini_studio.providers.runway_provider import RunwayProvider


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp(prefix="voxini_gen_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def pm(project_dir):
    manager = ProjectManager()
    manager.create_new(project_dir, "Test")
    return manager


def _has_ffmpeg() -> bool:
    return ffmpeg_locator.is_available()


def test_registry_has_mock_provider():
    providers = available_providers()
    assert any(p.id == "mock" for p in providers)
    assert get_provider("mock").id == "mock"


def test_estimate_costs_uses_provider_rate():
    provider = MockProvider()
    scenes = [Scene(start_seconds=0.0, end_seconds=5.0), Scene(start_seconds=0.0, end_seconds=10.0)]
    costs = estimate_costs(scenes, provider)
    assert costs[0][1] == pytest.approx(5.0 * provider.price_per_second)
    assert costs[1][1] == pytest.approx(10.0 * provider.price_per_second)


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg not available")
def test_generate_scene_produces_clip_and_marks_done(pm):
    scene = Scene(order=0, label="TEST SCENE", start_seconds=0.0, end_seconds=2.0, prompt_text="a test scene")
    pm.project.scenes.append(scene)
    provider = MockProvider()

    version = generate_scene(pm, scene, provider)

    assert scene.status == SceneStatus.DONE
    assert version.accepted is True
    assert version.error_message == ""
    clip_path = pm.resolve(version.file_path)
    assert clip_path.exists()
    assert clip_path.stat().st_size > 0

    probe = subprocess.run(
        [ffmpeg_locator.ffprobe_path(), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(clip_path)],
        capture_output=True,
        text=True,
    )
    duration = float(probe.stdout.strip())
    assert duration == pytest.approx(2.0, abs=0.2)


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg not available")
def test_generate_scenes_batch_and_regeneration_creates_new_version(pm):
    scene_a = Scene(order=0, label="A", start_seconds=0.0, end_seconds=1.5, prompt_text="scene a")
    scene_b = Scene(order=1, label="B", start_seconds=1.5, end_seconds=3.5, prompt_text="scene b")
    pm.project.scenes.extend([scene_a, scene_b])
    provider = MockProvider()

    versions = generate_scenes(pm, [scene_a, scene_b], provider)
    assert len(versions) == 2
    assert scene_a.status == SceneStatus.DONE
    assert scene_b.status == SceneStatus.DONE
    assert len(scene_a.versions) == 1

    # regenerate scene_a - should append a second version and keep only the
    # newest one accepted
    generate_scene(pm, scene_a, provider)
    assert len(scene_a.versions) == 2
    accepted = [v for v in scene_a.versions if v.accepted]
    assert len(accepted) == 1
    assert accepted[0] is scene_a.versions[-1]


def test_character_reference_paths_are_resolved_for_request(monkeypatch, pm):
    char = pm.add_character("Emma", "25, dark hair")
    scene = Scene(order=0, label="EMMA SCENE", start_seconds=0.0, end_seconds=1.0, character_ids=[char.id])
    pm.project.scenes.append(scene)

    captured = {}

    class SpyProvider(MockProvider):
        def generate(self, request):
            captured["prompt"] = request.resolved_prompt
            captured["refs"] = request.character_reference_paths
            return super().generate(request)

    if _has_ffmpeg():
        generate_scene(pm, scene, SpyProvider())
        assert "Emma" in captured["prompt"]


# -- hybrid provider resolution & budget gate --------------------------------

def test_resolve_provider_defaults_to_comfyui(pm):
    scene = Scene(order=0, label="A", start_seconds=0.0, end_seconds=1.0)
    pm.project.scenes.append(scene)
    provider = resolve_provider(pm, scene)
    assert isinstance(provider, ComfyUIProvider)


def test_resolve_provider_respects_scene_override(pm):
    pm.project.default_provider_id = "comfyui"
    scene = Scene(order=0, label="A", start_seconds=0.0, end_seconds=1.0, provider_override="runway")
    pm.project.scenes.append(scene)
    provider = resolve_provider(pm, scene)
    assert isinstance(provider, RunwayProvider)


def test_resolve_provider_uses_project_comfyui_settings(pm):
    pm.project.comfyui_host = "192.168.1.50"
    pm.project.comfyui_port = 9999
    scene = Scene(order=0, label="A", start_seconds=0.0, end_seconds=1.0)
    pm.project.scenes.append(scene)
    provider = resolve_provider(pm, scene)
    assert provider.host == "192.168.1.50"
    assert provider.port == 9999


def test_budget_check_allows_local_providers_regardless_of_limit(pm):
    pm.project.runway_budget_limit = 0.01
    provider = MockProvider()
    assert budget_check(pm, provider, 1000.0) is None


def test_budget_check_blocks_runway_job_over_limit(pm):
    pm.project.runway_budget_limit = 1.0
    pm.project.runway_spent_total = 0.9
    provider = RunwayProvider(model_id="gen4_turbo", api_key="x")
    msg = budget_check(pm, provider, 0.5)
    assert msg is not None
    assert "Budget" in msg


def test_budget_check_allows_runway_job_within_limit(pm):
    pm.project.runway_budget_limit = 10.0
    pm.project.runway_spent_total = 1.0
    provider = RunwayProvider(model_id="gen4_turbo", api_key="x")
    assert budget_check(pm, provider, 0.5) is None


def test_budget_check_no_limit_set_never_blocks(pm):
    pm.project.runway_budget_limit = 0.0
    provider = RunwayProvider(model_id="gen4_turbo", api_key="x")
    assert budget_check(pm, provider, 999.0) is None


def test_generate_scene_blocked_by_budget_never_calls_provider(pm):
    pm.project.runway_budget_limit = 0.01
    pm.project.runway_spent_total = 0.0
    scene = Scene(order=0, label="A", start_seconds=0.0, end_seconds=5.0, prompt_text="x")
    pm.project.scenes.append(scene)

    calls = {"n": 0}

    class SpyRunway(RunwayProvider):
        def generate(self, request):
            calls["n"] += 1
            raise AssertionError("provider.generate() must not be called when budget is exceeded")

    version = generate_scene(pm, scene, SpyRunway(model_id="gen4_turbo", api_key="x"))
    assert calls["n"] == 0
    assert scene.status == SceneStatus.FAILED
    assert "Budget" in version.error_message


def test_generate_scene_records_actual_cost_and_spend_for_runway(pm):
    scene = Scene(order=0, label="A", start_seconds=0.0, end_seconds=3.0, prompt_text="x")
    pm.project.scenes.append(scene)

    class FakeSuccessRunway(RunwayProvider):
        def generate(self, request):
            from voxini_studio.providers.base import GenerationResult
            Path(request.dest_path).parent.mkdir(parents=True, exist_ok=True)
            Path(request.dest_path).write_bytes(b"\x00")
            return GenerationResult(success=True, file_path=str(request.dest_path), actual_cost=1.23)

    provider = FakeSuccessRunway(model_id="gen4_turbo", api_key="x")
    version = generate_scene(pm, scene, provider)
    assert version.actual_cost == pytest.approx(1.23)
    assert pm.project.runway_spent_total == pytest.approx(1.23)

    # a second job accumulates on top of the first
    scene2 = Scene(order=1, label="B", start_seconds=3.0, end_seconds=6.0, prompt_text="y")
    pm.project.scenes.append(scene2)
    generate_scene(pm, scene2, provider)
    assert pm.project.runway_spent_total == pytest.approx(2.46)
