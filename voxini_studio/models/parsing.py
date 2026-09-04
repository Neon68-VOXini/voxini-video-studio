"""Plain data models produced by the Phase 2 parsers (audio analysis, SRT,
timecode-based prompt script). Kept separate from models/project.py because
these are intermediate/derived data, not part of the persisted project file
itself (the scene planner turns them into Scene objects that do get saved)."""
from __future__ import annotations

from pydantic import BaseModel


class AudioAnalysis(BaseModel):
    duration_seconds: float
    tempo_bpm: float
    beat_times: list[float]


class SubtitleLine(BaseModel):
    index: int
    start_seconds: float
    end_seconds: float
    text: str


class ScriptSection(BaseModel):
    label: str
    start_seconds: float
    end_seconds: float
    body: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


class ParsedScript(BaseModel):
    preamble: str = ""
    """Free text before the first timecoded section (format/style notes,
    character continuity blocks, etc). Not a scene - kept for reference."""
    sections: list[ScriptSection]
    trailing_notes: str = ""
    """Free text after the last timecoded section's actual scene description
    (e.g. 'COLOR AND CAMERA DIRECTION', 'STRICT CONTINUITY AND AUDIO RULES').
    Split out automatically so it doesn't contaminate the final scene body."""
