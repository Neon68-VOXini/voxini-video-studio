"""SRT subtitle parsing."""
from __future__ import annotations

from pathlib import Path

from voxini_studio.models.parsing import SubtitleLine


def parse_srt_file(path: str) -> list[SubtitleLine]:
    import srt as srt_lib  # lazy import

    content = Path(path).read_text(encoding="utf-8-sig")
    lines: list[SubtitleLine] = []
    for sub in srt_lib.parse(content):
        lines.append(
            SubtitleLine(
                index=sub.index,
                start_seconds=sub.start.total_seconds(),
                end_seconds=sub.end.total_seconds(),
                text=sub.content.strip(),
            )
        )
    return lines


def lines_in_range(lines: list[SubtitleLine], start: float, end: float) -> list[str]:
    """SRT line texts whose interval overlaps [start, end)."""
    return [ln.text for ln in lines if ln.start_seconds < end and ln.end_seconds > start]
