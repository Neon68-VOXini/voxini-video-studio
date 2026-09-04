from pathlib import Path

import pytest

from voxini_studio.core.srt_parser import lines_in_range, parse_srt_file

FIXTURE = Path(__file__).parent / "fixtures" / "WITHOUT EVER HAVING YOU.srt"


def _norm(text: str) -> str:
    """Collapse the source SRT's deliberate multi-space lyric styling and
    normalize curly quotes, so content checks aren't whitespace-sensitive."""
    return " ".join(text.replace("’", "'").split())


@pytest.mark.skipif(not FIXTURE.exists(), reason="real SRT fixture not present")
def test_parse_real_srt_basic_shape():
    lines = parse_srt_file(str(FIXTURE))
    assert len(lines) > 50
    first = lines[0]
    assert first.index == 1
    assert first.start_seconds == pytest.approx(7.021, abs=0.01)
    assert "Intro" in first.text

    last = lines[-1]
    assert "hear me anymore" in _norm(last.text)


@pytest.mark.skipif(not FIXTURE.exists(), reason="real SRT fixture not present")
def test_lines_in_range_matches_intro_window():
    lines = parse_srt_file(str(FIXTURE))
    # 00:07-00:28 is the INTRO section in the script
    in_range = lines_in_range(lines, 7.0, 28.0)
    assert any("last row" in _norm(t) for t in in_range)
    assert len(in_range) > 0


def test_lines_in_range_empty_for_untouched_window():
    from voxini_studio.models.parsing import SubtitleLine

    lines = [SubtitleLine(index=1, start_seconds=10.0, end_seconds=12.0, text="hello")]
    assert lines_in_range(lines, 0.0, 5.0) == []
    assert lines_in_range(lines, 11.0, 13.0) == ["hello"]
