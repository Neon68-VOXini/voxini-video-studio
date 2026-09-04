"""End-to-end proof: the complete pipeline - audio analysis, SRT parsing,
prompt-script parsing, scene planning, Mock-provider generation for every
scene, and final FFmpeg assembly/export with a title card and soft
subtitles - run against the user's real "WITHOUT EVER HAVING YOU" song,
SRT and full directorial prompt script.

The final assembly step is run at a deliberately small resolution (via a
monkeypatched aspect-ratio dimension table) purely to keep this test's
runtime reasonable; it exercises the exact same code path as a full
1920x1080 export (same frame-accurate crossfade-chain math, same padding,
muxing and subtitle handling), just producing far fewer pixels per frame.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from voxini_studio.core import ffmpeg_locator
from voxini_studio.core.audio_analysis import analyze_audio
from voxini_studio.core.generation_service import generate_scenes
from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.core.scene_planner import DEFAULT_MAX_CLIP_SECONDS, build_scenes
from voxini_studio.core.script_parser import parse_prompt_script
from voxini_studio.core.srt_parser import parse_srt_file
from voxini_studio.models.project import AspectRatio, SceneStatus
from voxini_studio.providers.mock_provider import MockProvider

FIXTURES = Path(__file__).parent / "fixtures"
AUDIO_FIXTURE = FIXTURES / "WITHOUT EVER HAVING YOU.wav"
SRT_FIXTURE = FIXTURES / "WITHOUT EVER HAVING YOU.srt"
PROMPT_FIXTURE = FIXTURES / "without_ever_having_you_prompt.txt"

_REQUIRED = [AUDIO_FIXTURE, SRT_FIXTURE, PROMPT_FIXTURE]

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in _REQUIRED) or not ffmpeg_locator.is_available(),
    reason="real fixtures and/or ffmpeg not available",
)


def _probe_duration(path) -> float:
    result = subprocess.run(
        [ffmpeg_locator.ffprobe_path(), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def _probe_video_stream(path):
    result = subprocess.run(
        [
            ffmpeg_locator.ffprobe_path(), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "default=nw=1:nk=1", str(path),
        ],
        capture_output=True, text=True,
    )
    lines = result.stdout.strip().splitlines()
    return int(lines[0]), int(lines[1])


def _has_audio_stream(path) -> bool:
    result = subprocess.run(
        [ffmpeg_locator.ffprobe_path(), "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def _has_subtitle_stream(path) -> bool:
    result = subprocess.run(
        [ffmpeg_locator.ffprobe_path(), "-v", "error", "-select_streams", "s", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def test_full_pipeline_end_to_end_real_song(monkeypatch):
    # This is the slow full-pipeline test: real audio analysis + ~43 mock
    # generations + a ~44-input crossfade chain (downscaled for speed).
    project_dir = tempfile.mkdtemp(prefix="voxini_e2e_")
    try:
        pm = ProjectManager()
        pm.create_new(project_dir, "Without Ever Having You")
        pm.import_audio(str(AUDIO_FIXTURE))
        pm.import_srt(str(SRT_FIXTURE))
        pm.import_prompt(str(PROMPT_FIXTURE))

        # --- Phase 2: audio + SRT + script parsing -----------------------
        analysis = analyze_audio(str(pm.resolve(pm.project.audio_path)))
        pm.project.audio_duration_seconds = analysis.duration_seconds
        pm.project.tempo_bpm = analysis.tempo_bpm
        pm.project.beat_times = analysis.beat_times
        assert 315.0 < analysis.duration_seconds < 325.0

        srt_lines = parse_srt_file(str(pm.resolve(pm.project.srt_path)))
        assert len(srt_lines) > 50

        parsed_script = parse_prompt_script(pm.resolve(pm.project.full_prompt_path).read_text(encoding="utf-8"))
        assert len(parsed_script.sections) == 14

        # --- Phase 4: characters, exactly as specified in the prompt -----
        emma = pm.add_character(
            "Emma",
            "25 years old, long dark-brown hair, clear green eyes, fair skin, strong natural eyebrows",
        )
        axel = pm.add_character(
            "Axel", "28 years old, shoulder-length wavy dark hair, blue-gray eyes, short dark stubble"
        )

        # --- Phase 3: scene planning --------------------------------------
        scenes = build_scenes(parsed_script, srt_lines, max_clip_seconds=DEFAULT_MAX_CLIP_SECONDS)
        assert len(scenes) > 0
        assert scenes[0].start_seconds == pytest.approx(0.0)
        assert scenes[-1].end_seconds == pytest.approx(318.0)
        pm.project.scenes = scenes
        pm.save()

        # --- Phase 5: generate every scene through the Mock provider -----
        provider = MockProvider()
        versions = generate_scenes(pm, pm.project.scenes, provider)
        assert len(versions) == len(scenes)
        statuses = {s.status for s in pm.project.scenes}
        assert statuses == {SceneStatus.DONE}, f"unexpected statuses: {statuses}"

        # --- Phase 6: assembly/export, downscaled purely for test speed --
        import voxini_studio.core.ffmpeg_assembly as assembly_mod

        monkeypatch.setattr(
            assembly_mod,
            "_ASPECT_DIMS",
            {
                AspectRatio.WIDESCREEN: (480, 270),
                AspectRatio.VERTICAL: (270, 480),
                AspectRatio.SQUARE: (360, 360),
            },
        )

        pm.project.title_text = "WITHOUT EVER HAVING YOU"
        pm.project.subtitle_mode = "soft"
        pm.save()

        out_path = pm.paths.export_dir / "WITHOUT_EVER_HAVING_YOU_e2e.mp4"
        result = assembly_mod.assemble_video(pm, AspectRatio.WIDESCREEN, out_path)

        assert result.exists()
        assert result.stat().st_size > 100_000

        final_duration = _probe_duration(result)
        assert final_duration == pytest.approx(analysis.duration_seconds, abs=0.5)

        width, height = _probe_video_stream(result)
        assert (width, height) == (480, 270)

        assert _has_audio_stream(result)
        assert _has_subtitle_stream(result)

        print(
            f"\nE2E OK: {len(scenes)} scenes, {final_duration:.1f}s final duration, "
            f"characters={list(pm.project.characters.keys())}"
        )
    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
