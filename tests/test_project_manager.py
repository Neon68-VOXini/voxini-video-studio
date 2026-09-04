"""Headless tests for the project data model and project manager -
no Qt involved, runs anywhere."""
import shutil
import tempfile
from pathlib import Path

import pytest

from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.models.project import AspectRatio, Character, Scene, SceneStatus


@pytest.fixture
def tmp_project_dir():
    d = tempfile.mkdtemp(prefix="voxini_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_files(tmp_path):
    audio = tmp_path / "song.wav"
    audio.write_bytes(b"RIFF....WAVEfmt ")
    srt = tmp_path / "song.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("00:00-00:07 | INTRO\nSome scene text.", encoding="utf-8")
    img = tmp_path / "ref.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    return {"audio": audio, "srt": srt, "prompt": prompt, "img": img}


def test_create_and_reopen_project(tmp_project_dir, sample_files):
    pm = ProjectManager()
    project = pm.create_new(tmp_project_dir, "Without Ever Having You")
    assert project.name == "Without Ever Having You"
    assert Path(pm.paths.project_file).exists()

    pm.import_audio(sample_files["audio"])
    pm.import_srt(sample_files["srt"])
    pm.import_prompt(sample_files["prompt"])

    char = pm.add_character("Emma", "25, long dark-brown hair, green eyes")
    pm.import_character_reference(char.id, sample_files["img"])
    pm.save()

    assert project.audio_path.startswith("media/audio")
    assert project.srt_path.startswith("media/srt")
    assert project.full_prompt_path.startswith("media/prompt")
    assert len(project.characters[char.id].reference_image_paths) == 1

    # re-open in a fresh manager and confirm everything round-trips
    pm2 = ProjectManager()
    reopened = pm2.open(tmp_project_dir)
    assert reopened.name == "Without Ever Having You"
    assert reopened.audio_path == project.audio_path
    assert len(reopened.characters) == 1
    reopened_char = list(reopened.characters.values())[0]
    assert reopened_char.name == "Emma"
    assert len(reopened_char.reference_image_paths) == 1

    # imported files must actually exist on disk inside the project folder
    assert pm2.resolve(reopened.audio_path).exists()
    assert pm2.resolve(reopened_char.reference_image_paths[0]).exists()


def test_stored_media_paths_always_use_forward_slashes_even_on_windows(tmp_project_dir, sample_files):
    """project.voxproj must be portable across OSes: a relative path saved
    on Windows must use '/' like on any other OS, never a literal '\\'."""
    pm = ProjectManager()
    project = pm.create_new(tmp_project_dir, "Portable Paths Test")

    pm.import_audio(sample_files["audio"])
    pm.import_srt(sample_files["srt"])
    pm.import_prompt(sample_files["prompt"])
    char = pm.add_character("Emma", "25, long dark-brown hair, green eyes")
    pm.import_character_reference(char.id, sample_files["img"])
    pm.save()

    assert "\\" not in project.audio_path
    assert "\\" not in project.srt_path
    assert "\\" not in project.full_prompt_path
    assert "\\" not in project.characters[char.id].reference_image_paths[0]

    # the raw project.voxproj JSON on disk must never contain a literal
    # backslash-separated "media\..." path either (this is the exact defect
    # a Windows test run surfaced: paths were written with '\' instead of
    # '/'). A JSON-escaped backslash would appear as the two characters \\ .
    raw_json = Path(pm.paths.project_file).read_text(encoding="utf-8")
    assert "media\\\\" not in raw_json
    assert 'media/' in raw_json


def test_scene_resolved_prompt_includes_character_block():
    emma = Character(name="Emma", description="25, dark-brown hair, green eyes")
    axel = Character(name="Axel", description="28, wavy dark hair, blue-gray eyes")
    scene = Scene(
        label="INTRO",
        start_seconds=7.0,
        end_seconds=28.0,
        prompt_text="Emma sits alone in the last row.",
        character_ids=[emma.id, axel.id],
    )
    characters = {emma.id: emma, axel.id: axel}
    resolved = scene.resolved_prompt(characters)
    assert "Emma: 25, dark-brown hair, green eyes" in resolved
    assert "Axel: 28, wavy dark hair, blue-gray eyes" in resolved
    assert "Emma sits alone in the last row." in resolved


def test_scene_duration_and_status_default():
    scene = Scene(start_seconds=10.0, end_seconds=17.5)
    assert scene.duration == pytest.approx(7.5)
    assert scene.status == SceneStatus.PLANNED
    assert scene.accepted_version() is None


def test_project_default_aspect_ratio():
    pm = ProjectManager()
    with tempfile.TemporaryDirectory() as d:
        project = pm.create_new(d, "Test")
        assert project.default_aspect_ratio == AspectRatio.WIDESCREEN


def test_project_defaults_to_local_free_provider():
    pm = ProjectManager()
    with tempfile.TemporaryDirectory() as d:
        project = pm.create_new(d, "Test")
        assert project.default_provider_id == "comfyui"
        assert project.runway_budget_limit == 0.0
        assert project.runway_spent_total == 0.0


def test_scene_provider_override_resolution():
    pm = ProjectManager()
    with tempfile.TemporaryDirectory() as d:
        project = pm.create_new(d, "Test")
        scene_default = Scene(order=0, start_seconds=0.0, end_seconds=1.0)
        scene_override = Scene(order=1, start_seconds=1.0, end_seconds=2.0, provider_override="runway")
        assert project.provider_for_scene(scene_default) == "comfyui"
        assert project.provider_for_scene(scene_override) == "runway"
