"""Audio analysis: duration, tempo and beat grid via librosa. Same technique
proven in the Universum project - kept intentionally simple for the MVP;
section/energy detection (as used for Universum's structure detection) can
be added later if the scene planner needs finer beat-snapping."""
from __future__ import annotations

import numpy as np

from voxini_studio.models.parsing import AudioAnalysis


def analyze_audio(path: str) -> AudioAnalysis:
    import librosa  # imported lazily - heavy dependency, not needed for pure UI tests

    y, sr = librosa.load(path, sr=None, mono=True)
    duration = librosa.get_duration(y=y, sr=sr)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    tempo_val = float(tempo) if np.isscalar(tempo) else float(np.asarray(tempo).reshape(-1)[0])
    return AudioAnalysis(
        duration_seconds=float(duration),
        tempo_bpm=tempo_val,
        beat_times=[float(t) for t in beat_times],
    )
