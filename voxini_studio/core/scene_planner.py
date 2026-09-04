"""Turns a ParsedScript + SRT lines into the Scene list that becomes part of
the saved Project. Splits script sections that are longer than the given
per-clip duration cap (most external video-gen providers cap clips around
5-10s) into evenly-sized sub-scenes, since the storyboard/generation/export
pipeline downstream always operates on individual short clips.
"""
from __future__ import annotations

import math

from voxini_studio.models.parsing import ParsedScript, SubtitleLine
from voxini_studio.models.project import Scene
from voxini_studio.core.srt_parser import lines_in_range

DEFAULT_MAX_CLIP_SECONDS = 8.0


def build_scenes(
    parsed_script: ParsedScript,
    srt_lines: list[SubtitleLine] | None = None,
    max_clip_seconds: float = DEFAULT_MAX_CLIP_SECONDS,
) -> list[Scene]:
    srt_lines = srt_lines or []
    scenes: list[Scene] = []
    order = 0

    for section in parsed_script.sections:
        n_parts = max(1, math.ceil(section.duration / max_clip_seconds)) if section.duration > 0 else 1
        part_duration = section.duration / n_parts

        for part_index in range(n_parts):
            start = section.start_seconds + part_index * part_duration
            end = section.start_seconds + (part_index + 1) * part_duration
            if part_index == n_parts - 1:
                end = section.end_seconds  # avoid float-drift on the last slice

            label = section.label if n_parts == 1 else f"{section.label} ({part_index + 1}/{n_parts})"
            lyric_lines = lines_in_range(srt_lines, start, end)

            scenes.append(
                Scene(
                    order=order,
                    label=label,
                    start_seconds=round(start, 3),
                    end_seconds=round(end, 3),
                    prompt_text=section.body,
                    lyric_lines=lyric_lines,
                )
            )
            order += 1

    return scenes
