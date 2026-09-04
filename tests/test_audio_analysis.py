from pathlib import Path

import pytest

from voxini_studio.core.audio_analysis import analyze_audio

FIXTURE = Path(__file__).parent / "fixtures" / "WITHOUT EVER HAVING YOU.wav"


@pytest.mark.skipif(not FIXTURE.exists(), reason="real audio fixture not present")
def test_analyze_real_song_duration_and_tempo():
    analysis = analyze_audio(str(FIXTURE))
    # song is ~5:21 (321s); allow a little slack for decoder rounding
    assert 315.0 < analysis.duration_seconds < 325.0
    assert analysis.tempo_bpm > 0
    assert len(analysis.beat_times) > 10
    # beats must be sorted and within the track
    assert analysis.beat_times == sorted(analysis.beat_times)
    assert analysis.beat_times[-1] <= analysis.duration_seconds
