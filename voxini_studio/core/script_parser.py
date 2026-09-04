"""Parser for the timecode-based directorial prompt script format used by
VOXini Video Studio, e.g.:

    00:00–00:07 | INSTRUMENTAL OPENING
    Begin on complete black. A narrow line of cold blue light...

    00:07–00:28 | INTRO
    As Emma sings, "Today you're going to say, I do,"...

Accepts both '-' and '-' (en dash) as the time-range separator, and MM:SS
or H:MM:SS timecodes. Text before the first timecoded header (format/style,
character continuity notes) is kept as `preamble`. Trailing ALL-CAPS
sections after the last timecode (e.g. 'COLOR AND CAMERA DIRECTION',
'STRICT CONTINUITY AND AUDIO RULES') are detected and split out into
`trailing_notes` instead of being treated as part of the final scene.
"""
from __future__ import annotations

import re

from voxini_studio.models.parsing import ParsedScript, ScriptSection

_TIME = r"\d{1,3}(?::\d{2}){1,2}"
_HEADER_RE = re.compile(
    rf"^\s*(?P<start>{_TIME})\s*[–‒\-]\s*(?P<end>{_TIME})\s*\|\s*(?P<label>.+?)\s*$"
)
# A trailing global-notes header: a short, ALL-CAPS line with no digits/timecode,
# e.g. "COLOR AND CAMERA DIRECTION" or "STRICT CONTINUITY AND AUDIO RULES".
_GLOBAL_HEADER_RE = re.compile(r"^[A-Z][A-Z0-9 ,/'\-]{3,80}$")


def parse_timecode(value: str) -> float:
    parts = [int(p) for p in value.strip().split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes * 60 + seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return float(hours * 3600 + minutes * 60 + seconds)
    raise ValueError(f"Unrecognized timecode: {value!r}")


def _looks_like_global_header(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) < 4:
        return False
    if _HEADER_RE.match(stripped):
        return False
    if not _GLOBAL_HEADER_RE.match(stripped):
        return False
    # must be "shouty" - no lowercase letters at all, distinguishes a real
    # section title from a normal capitalized sentence
    return stripped == stripped.upper() and any(c.isalpha() for c in stripped)


def parse_prompt_script(text: str) -> ParsedScript:
    lines = text.splitlines()

    preamble_lines: list[str] = []
    sections: list[dict] = []
    current: dict | None = None

    for raw_line in lines:
        match = _HEADER_RE.match(raw_line)
        if match:
            if current is not None:
                sections.append(current)
            current = {
                "label": match.group("label").strip(),
                "start_seconds": parse_timecode(match.group("start")),
                "end_seconds": parse_timecode(match.group("end")),
                "body_lines": [],
            }
            continue

        if current is None:
            preamble_lines.append(raw_line)
        else:
            current["body_lines"].append(raw_line)

    if current is not None:
        sections.append(current)

    trailing_notes_lines: list[str] = []
    if sections:
        last = sections[-1]
        body = last["body_lines"]
        split_at = None
        for i, line in enumerate(body):
            if _looks_like_global_header(line):
                split_at = i
                break
        if split_at is not None:
            trailing_notes_lines = body[split_at:]
            last["body_lines"] = body[:split_at]

    parsed_sections = [
        ScriptSection(
            label=s["label"],
            start_seconds=s["start_seconds"],
            end_seconds=s["end_seconds"],
            body="\n".join(s["body_lines"]).strip(),
        )
        for s in sections
    ]

    return ParsedScript(
        preamble="\n".join(preamble_lines).strip(),
        sections=parsed_sections,
        trailing_notes="\n".join(trailing_notes_lines).strip(),
    )
