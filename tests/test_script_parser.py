from pathlib import Path

from voxini_studio.core.script_parser import parse_prompt_script, parse_timecode

FIXTURE = Path(__file__).parent / "fixtures" / "without_ever_having_you_prompt.txt"


def test_parse_timecode_mm_ss():
    assert parse_timecode("00:07") == 7.0
    assert parse_timecode("05:18") == 318.0
    assert parse_timecode("01:25") == 85.0


def test_parse_timecode_h_mm_ss():
    assert parse_timecode("1:02:03") == 3723.0


def test_parse_real_script_section_count_and_order():
    text = FIXTURE.read_text(encoding="utf-8")
    parsed = parse_prompt_script(text)

    assert len(parsed.sections) == 14
    first = parsed.sections[0]
    assert first.label == "INSTRUMENTAL OPENING"
    assert first.start_seconds == 0.0
    assert first.end_seconds == 7.0

    last = parsed.sections[-1]
    assert last.label == "FINAL INSTRUMENTAL SECONDS"
    assert last.start_seconds == 310.0
    assert last.end_seconds == 318.0

    # sections must be contiguous and strictly increasing
    for a, b in zip(parsed.sections, parsed.sections[1:]):
        assert a.end_seconds == b.start_seconds
        assert b.start_seconds < b.end_seconds


def test_parse_real_script_preamble_and_trailing_notes_split_out():
    text = FIXTURE.read_text(encoding="utf-8")
    parsed = parse_prompt_script(text)

    assert "FORMAT AND STYLE" in parsed.preamble
    assert "CHARACTER CONTINUITY" in parsed.preamble
    assert "Emma is 25 years old" in parsed.preamble

    assert "COLOR AND CAMERA DIRECTION" in parsed.trailing_notes
    assert "STRICT CONTINUITY AND AUDIO RULES" in parsed.trailing_notes

    last_section = parsed.sections[-1]
    assert "COLOR AND CAMERA DIRECTION" not in last_section.body
    assert "streetlights" in last_section.body


def test_parse_real_script_body_content_spot_check():
    text = FIXTURE.read_text(encoding="utf-8")
    parsed = parse_prompt_script(text)
    outro = next(s for s in parsed.sections if s.label == "OUTRO – BEHIND THE GLASS")
    assert "I love you" in outro.body
    assert outro.duration == 21.0


def test_parse_handles_plain_hyphen_separator():
    text = "00:00-00:05 | TEST\nSome body text.\n"
    parsed = parse_prompt_script(text)
    assert len(parsed.sections) == 1
    assert parsed.sections[0].label == "TEST"
    assert parsed.sections[0].start_seconds == 0.0
    assert parsed.sections[0].end_seconds == 5.0
