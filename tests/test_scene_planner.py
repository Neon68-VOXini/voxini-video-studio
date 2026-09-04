from pathlib import Path

import pytest

from voxini_studio.core.scene_planner import build_scenes
from voxini_studio.core.script_parser import parse_prompt_script
from voxini_studio.core.srt_parser import parse_srt_file
from voxini_studio.models.parsing import ParsedScript, ScriptSection

PROMPT_FIXTURE = Path(__file__).parent / "fixtures" / "without_ever_having_you_prompt.txt"
SRT_FIXTURE = Path(__file__).parent / "fixtures" / "WITHOUT EVER HAVING YOU.srt"


def test_short_section_stays_one_scene():
    parsed = ParsedScript(
        sections=[ScriptSection(label="INTRO", start_seconds=0.0, end_seconds=7.0, body="hello")]
    )
    scenes = build_scenes(parsed, max_clip_seconds=8.0)
    assert len(scenes) == 1
    assert scenes[0].label == "INTRO"
    assert scenes[0].duration == pytest.approx(7.0)
    assert scenes[0].order == 0


def test_long_section_splits_into_even_subscenes():
    parsed = ParsedScript(
        sections=[ScriptSection(label="VERSE 1", start_seconds=0.0, end_seconds=43.0, body="text")]
    )
    scenes = build_scenes(parsed, max_clip_seconds=8.0)
    # 43s / 8s -> ceil = 6 parts
    assert len(scenes) == 6
    assert scenes[0].label == "VERSE 1 (1/6)"
    assert scenes[-1].label == "VERSE 1 (6/6)"
    # contiguous, no gaps or overlaps
    for a, b in zip(scenes, scenes[1:]):
        assert a.end_seconds == pytest.approx(b.start_seconds)
    assert scenes[0].start_seconds == pytest.approx(0.0)
    assert scenes[-1].end_seconds == pytest.approx(43.0)
    # every sub-scene should be no longer than the cap (give a hair of slack for rounding)
    assert all(s.duration <= 8.0 + 1e-6 for s in scenes)


def test_order_increments_across_sections():
    parsed = ParsedScript(
        sections=[
            ScriptSection(label="A", start_seconds=0.0, end_seconds=5.0, body="a"),
            ScriptSection(label="B", start_seconds=5.0, end_seconds=25.0, body="b"),
        ]
    )
    scenes = build_scenes(parsed, max_clip_seconds=8.0)
    orders = [s.order for s in scenes]
    assert orders == list(range(len(scenes)))


@pytest.mark.skipif(
    not (PROMPT_FIXTURE.exists() and SRT_FIXTURE.exists()), reason="real fixtures not present"
)
def test_real_song_scene_plan_covers_full_duration_and_gets_lyrics():
    text = PROMPT_FIXTURE.read_text(encoding="utf-8")
    parsed = parse_prompt_script(text)
    srt_lines = parse_srt_file(str(SRT_FIXTURE))

    scenes = build_scenes(parsed, srt_lines, max_clip_seconds=8.0)

    assert scenes[0].start_seconds == pytest.approx(0.0)
    assert scenes[-1].end_seconds == pytest.approx(318.0)
    for a, b in zip(scenes, scenes[1:]):
        assert a.end_seconds == pytest.approx(b.start_seconds, abs=0.01)

    # the INTRO section (7-28s) should have picked up its SRT lyric lines
    intro_scenes = [s for s in scenes if s.label.startswith("INTRO")]
    assert intro_scenes
    assert any(s.lyric_lines for s in intro_scenes)

    # every scene must respect the clip length cap
    assert all(s.duration <= 8.0 + 1e-6 for s in scenes)
