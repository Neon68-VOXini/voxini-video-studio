import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from voxini_studio.core import ffmpeg_locator
from voxini_studio.core.ffmpeg_assembly import AssemblyError, assemble_video, ready_scenes
from voxini_studio.core.generation_service import generate_scenes
from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.models.project import AspectRatio, Scene
from voxini_studio.providers.mock_provider import MockProvider


pytestmark = pytest.mark.skipif(not ffmpeg_locator.is_available(), reason="ffmpeg not available")


def _probe(path, *entries):
    result = subprocess.run(
        [
            ffmpeg_locator.ffprobe_path(), "-v", "error", "-select_streams", "v:0",
            "-show_entries", ",".join(entries), "-of", "default=nw=1:nk=1", str(path),
        ],
        capture_output=True, text=True,
    )
    return result.stdout.strip().splitlines()


def _probe_duration(path) -> float:
    result = subprocess.run(
        [ffmpeg_locator.ffprobe_path(), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def _has_subtitle_stream(path) -> bool:
    result = subprocess.run(
        [ffmpeg_locator.ffprobe_path(), "-v", "error", "-select_streams", "s", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp(prefix="voxini_assembly_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def small_project(project_dir):
    """3 short scenes, all generated via MockProvider, plus a short
    synthetic audio track matching their total duration (fast, deterministic
    - the real 5:21 song is exercised separately in the Phase 7 E2E test)."""
    pm = ProjectManager()
    pm.create_new(project_dir, "Assembly Test")

    scenes = [
        Scene(order=0, label="OPENING", start_seconds=0.0, end_seconds=2.0, prompt_text="black, rain on glass"),
        Scene(order=1, label="MEMORY", start_seconds=2.0, end_seconds=4.5, prompt_text="sunlit river walk"),
        Scene(order=2, label="OUTRO", start_seconds=4.5, end_seconds=7.0, prompt_text="rain-covered glass, empty"),
    ]
    pm.project.scenes.extend(scenes)
    total_duration = scenes[-1].end_seconds

    generate_scenes(pm, scenes, MockProvider())
    assert all(s.status.value == "done" for s in scenes)

    srt_path = Path(pm.paths.srt_dir) / "test.srt"
    pm.paths.srt_dir.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nHello there\n\n"
        "2\n00:00:02,000 --> 00:00:04,500\nSecond line\n",
        encoding="utf-8",
    )
    pm.project.srt_path = srt_path.relative_to(pm.paths.root).as_posix()

    audio_path = Path(pm.paths.audio_dir)
    pm.paths.audio_dir.mkdir(parents=True, exist_ok=True)
    audio_file = audio_path / "test_audio.wav"
    subprocess.run(
        [
            ffmpeg_locator.ffmpeg_path(), "-y", "-f", "lavfi",
            "-i", f"sine=frequency=440:duration={total_duration}",
            str(audio_file), "-loglevel", "error",
        ],
        check=True,
    )
    pm.project.audio_path = audio_file.relative_to(pm.paths.root).as_posix()

    pm.save()
    return pm, total_duration


def test_ready_scenes_returns_only_accepted(small_project):
    pm, _ = small_project
    ready = ready_scenes(pm)
    assert len(ready) == 3
    assert [s.label for s, _ in ready] == ["OPENING", "MEMORY", "OUTRO"]


def test_assemble_video_widescreen_matches_audio_duration(small_project):
    pm, total_duration = small_project
    out = pm.paths.export_dir / "out_16x9.mp4"
    result = assemble_video(pm, AspectRatio.WIDESCREEN, out, mux_audio=True)
    assert result.exists()

    duration = _probe_duration(result)
    assert duration == pytest.approx(total_duration, abs=0.3)

    w, h = _probe(result, "stream=width,height")
    assert (w, h) == ("1920", "1080")


def test_assemble_video_vertical_and_square_resolutions(small_project):
    pm, _ = small_project

    out_v = pm.paths.export_dir / "out_9x16.mp4"
    assemble_video(pm, AspectRatio.VERTICAL, out_v, mux_audio=False)
    w, h = _probe(out_v, "stream=width,height")
    assert (w, h) == ("1080", "1920")

    out_s = pm.paths.export_dir / "out_1x1.mp4"
    assemble_video(pm, AspectRatio.SQUARE, out_s, mux_audio=False)
    w, h = _probe(out_s, "stream=width,height")
    assert (w, h) == ("1080", "1080")


def test_assemble_video_with_title_card_is_longer(small_project):
    pm, total_duration = small_project
    out_no_title = pm.paths.export_dir / "no_title.mp4"
    assemble_video(pm, AspectRatio.WIDESCREEN, out_no_title, mux_audio=False)
    dur_no_title = _probe_duration(out_no_title)

    pm.project.title_text = "WITHOUT EVER HAVING YOU"
    out_title = pm.paths.export_dir / "with_title.mp4"
    assemble_video(pm, AspectRatio.WIDESCREEN, out_title, mux_audio=False)
    dur_title = _probe_duration(out_title)

    assert dur_title > dur_no_title + 2.0


def test_soft_subtitle_mode_embeds_subtitle_stream(small_project):
    pm, _ = small_project
    pm.project.subtitle_mode = "soft"
    out = pm.paths.export_dir / "soft_subs.mp4"
    assemble_video(pm, AspectRatio.WIDESCREEN, out, mux_audio=True)
    assert _has_subtitle_stream(out)


def test_none_subtitle_mode_has_no_subtitle_stream(small_project):
    pm, _ = small_project
    pm.project.subtitle_mode = "none"
    out = pm.paths.export_dir / "no_subs.mp4"
    assemble_video(pm, AspectRatio.WIDESCREEN, out, mux_audio=True)
    assert not _has_subtitle_stream(out)


def test_burned_subtitle_mode_succeeds(small_project):
    pm, _ = small_project
    pm.project.subtitle_mode = "burned"
    out = pm.paths.export_dir / "burned_subs.mp4"
    result = assemble_video(pm, AspectRatio.WIDESCREEN, out, mux_audio=True)
    assert result.exists()
    assert not _has_subtitle_stream(out)  # burned-in, not a separate stream


def test_assemble_raises_when_no_accepted_clips(project_dir):
    pm = ProjectManager()
    pm.create_new(project_dir, "Empty")
    pm.project.scenes.append(Scene(order=0, label="X", start_seconds=0.0, end_seconds=2.0))
    with pytest.raises(AssemblyError):
        assemble_video(pm, AspectRatio.WIDESCREEN, Path(project_dir) / "export" / "x.mp4", mux_audio=False)
