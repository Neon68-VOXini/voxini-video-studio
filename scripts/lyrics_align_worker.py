"""Worker-Skript fuer das Songtext-zu-SRT-Feature (automatischer Zeitabgleich
eines eingetippten Songtexts mit der Audiodatei).

Laeuft ausschliesslich im isolierten ".venv-lyrics"-Environment (siehe
voxini_studio/core/lyrics_align_env.py), NICHT im Haupt-Environment
(".venv") - dort sind audio-separator/faster-whisper bewusst nicht
installiert, um die schlanke Haupt-App nicht mit schweren ML-Abhaengigkeiten
zu belasten.

Kommuniziert mit dem aufrufenden Prozess (voxini_studio/core/lyrics_align.py,
laeuft im Haupt-Environment) ausschliesslich ueber stdout-Zeilen und den
Exit-Code - niemals ueber einen direkten Python-Import:

  PROGRESS <0-100> <Text>   - Fortschritt (wird an das UI durchgereicht)
  ERROR <Text>              - Fehlermeldung, danach Exit-Code != 0
  (alle anderen Zeilen)     - reine Diagnose-/Logausgabe, wird ignoriert

Exit-Code 0    = Erfolg, die SRT-Datei liegt am angegebenen --output-srt-Pfad.
Exit-Code != 0 = Fehler, siehe letzte ERROR-Zeile auf stdout.

Ablauf (Portierung + Erweiterung der Alignment-Logik aus dem Schwesterprojekt
VOXini Studio, app/lyrics_align.py):
  1. Vocal-Isolation (audio-separator) - trennt Gesang vom vollen Mix, damit
     Whisper nicht durch Instrumental/Beats gestoert wird. Anders als im
     Schwesterprojekt (dort trennt bereits Demucs den Gesangs-Stem VOR
     diesem Schritt, dieser Worker trennt hier zusaetzlich Lead/Backing)
     arbeitet dieser Worker direkt auf dem vollen Songmix.
  2. Leichte Sprachaufbereitung (Bandpass + Noise-Gate + Kompression, wie im
     Schwesterprojekt) - verbessert die Erkennungsqualitaet, ohne das
     Timing zu veraendern.
  3. Transkription mit Wort-Zeitstempeln (faster-whisper).
  4. Fuzzy-Zeitabgleich zwischen eingetipptem Songtext und erkannten
     Woertern (Needleman-Wunsch-Alignment, identischer Algorithmus wie im
     Schwesterprojekt).
  5. NEU (im Schwesterprojekt nicht vorhanden, dort nur LRC/JSON): Export
     als zeilenbasierte SRT-Datei ueber das bereits vorhandene `srt`-Paket.
"""
from __future__ import annotations

import argparse
import difflib
import json
import logging
import re
import shutil
import subprocess
import time
import traceback
import unicodedata
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
ERROR_LOG = LOGS_DIR / "lyrics_worker_error.log"
DEBUG_LOG = LOGS_DIR / "lyrics_worker_debug.log"

SECTION_RE = re.compile(
    r"^(intro|outro|verse(?:\s*\d+)?|strophe(?:\s*\d+)?|pre[- ]?chorus|chorus|refrain|bridge|"
    r"final\s+chorus|instrumental|interlude|hook|break|spoken|rap)(?:\s*\([^)]*\))?\s*:?$",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[\wÄÖÜäöüßÀ-ÿ]+(?:['’\-][\wÄÖÜäöüßÀ-ÿ]+)*", re.UNICODE)


def _configure_debug_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(DEBUG_LOG, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)


def _report(percent: int, message: str) -> None:
    print(f"PROGRESS {percent} {message}", flush=True)


def _error(message: str) -> None:
    print(f"ERROR {message}", flush=True)


def _log_traceback(exc: BaseException) -> None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(ERROR_LOG, "a", encoding="utf-8") as handle:
            handle.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            handle.writelines(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:
        pass  # Logging darf den eigentlichen Fehlerpfad nicht zusaetzlich crashen lassen


def _check_ffmpeg() -> str | None:
    """audio-separator ruft ffmpeg intern als bare Befehl auf. Der aufrufende
    Prozess (lyrics_align.py) haengt den Ordner des bereits im Hauptprojekt
    gebuendelten, korrekt "ffmpeg.exe" benannten Binaries vorne ins PATH
    dieses Subprozesses - anders als im Schwesterprojekt VOXini Studio ist
    hier deshalb KEIN Umbenennungs-Shim noetig (siehe core/ffmpeg_locator.py:
    der bundled Build heisst dort bereits korrekt "ffmpeg.exe")."""
    exe = shutil.which("ffmpeg")
    if not exe:
        return "Befehl 'ffmpeg' wurde ueber PATH nicht gefunden."
    try:
        subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=15, check=True)
    except Exception as exc:
        return f"'ffmpeg -version' ({exe}) konnte nicht ausgefuehrt werden: {exc}"
    return None


# ---------------------------------------------------------------------------
# Portierte Alignment-Logik (VOXini Studio, app/lyrics_align.py) + SRT-Export
# ---------------------------------------------------------------------------

def normalize_word(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("’", "'")
    value = value.replace("ß", "ss")
    return "".join(ch for ch in value if ch == "'" or unicodedata.category(ch)[0] in {"L", "N", "M"})


def clean_lyrics_text(text: str) -> dict[str, Any]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    lines: list[dict[str, Any]] = []
    sections: list[str] = []
    in_text = False
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        lower = line.casefold()
        if lower.startswith("styles:") or lower.startswith("style:"):
            break
        if lower.startswith("title:"):
            continue
        if lower in {"text:", "lyrics:", "songtext:"}:
            in_text = True
            continue
        if SECTION_RE.fullmatch(line):
            sections.append(line.rstrip(":"))
            in_text = True
            continue
        if not in_text and ":" in line:
            continue
        words = WORD_RE.findall(line)
        if words:
            lines.append({"text": line, "words": words})
            in_text = True
    if not lines:
        raise ValueError("Im eingegebenen Songtext wurde kein Text gefunden.")
    return {"lines": lines, "sections": sections}


def lyrics_prompt(cleaned: dict[str, Any], max_chars: int = 6000) -> str:
    return "\n".join(line["text"] for line in cleaned["lines"])[:max_chars]


def prepare_vocals_for_asr(source: Path, target: Path) -> Path:
    """Erzeugt eine sprachfreundlichere Version der isolierten Gesangsspur,
    OHNE das Timing zu veraendern (Bandpass + sanftes Noise-Gate +
    Kompression)."""
    import numpy as np
    import soundfile as sf
    from scipy.signal import butter, sosfiltfilt

    audio, sr = sf.read(str(source), always_2d=True, dtype="float32")
    mono = np.mean(audio, axis=1).astype(np.float32)
    if mono.size < 16:
        raise RuntimeError("Die isolierte Gesangsspur ist leer.")
    mono -= float(np.mean(mono))
    nyq = sr / 2.0
    low = max(45.0 / nyq, 0.001)
    high = min(9000.0 / nyq, 0.98)
    sos = butter(4, [low, high], btype="bandpass", output="sos")
    mono = sosfiltfilt(sos, mono).astype(np.float32)

    frame = max(256, int(sr * 0.025))
    hop = max(128, frame // 2)
    rms = []
    for pos in range(0, max(1, mono.size - frame), hop):
        chunk = mono[pos:pos + frame]
        rms.append(float(np.sqrt(np.mean(chunk * chunk) + 1e-12)))
    floor = float(np.percentile(rms, 15)) if rms else 0.0
    threshold = max(floor * 1.8, 1e-5)
    envelope = np.abs(mono)
    gate = np.clip((envelope - threshold * 0.35) / max(threshold * 1.4, 1e-6), 0.18, 1.0)
    kernel = np.ones(max(3, int(sr * 0.012)), dtype=np.float32)
    kernel /= kernel.sum()
    gate = np.convolve(gate, kernel, mode="same")
    mono *= gate.astype(np.float32)

    drive = 3.2
    mono = np.tanh(mono * drive) / np.tanh(drive)
    peak = float(np.max(np.abs(mono)))
    if peak > 0:
        mono *= 0.94 / peak
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(target), mono, sr, subtype="PCM_16")
    return target


def transcribe_words(
    audio_path: Path,
    model_name: str,
    model_dir: Path,
    language: str,
    initial_prompt: str | None = None,
) -> tuple[list[dict[str, Any]], float]:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8", download_root=str(model_dir))
    segments, info = model.transcribe(
        str(audio_path),
        language=language or None,
        beam_size=12,
        best_of=10,
        patience=1.4,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 180, "speech_pad_ms": 420},
        word_timestamps=True,
        condition_on_previous_text=True,
        initial_prompt=(initial_prompt or None),
        temperature=[0.0, 0.2],
        compression_ratio_threshold=2.6,
        log_prob_threshold=-1.2,
        no_speech_threshold=0.7,
    )
    result: list[dict[str, Any]] = []
    duration = 0.0
    for segment in segments:
        duration = max(duration, float(getattr(segment, "end", 0.0) or 0.0))
        for word in segment.words or []:
            token = (word.word or "").strip()
            norm = normalize_word(token)
            if norm and word.start is not None and word.end is not None:
                result.append({
                    "text": token, "norm": norm,
                    "start": float(word.start), "end": float(word.end),
                    "probability": float(getattr(word, "probability", 0.0) or 0.0),
                })
    if not result:
        detected = getattr(info, "language", None) or "unbekannt"
        raise RuntimeError(
            f"Die Gesangsspur lieferte keine Woerter mit Zeitstempeln (erkannte Sprache: {detected})."
        )
    return result, duration


def _interpolate_missing(times: list[dict[str, float] | None], duration: float) -> list[dict[str, float]]:
    n = len(times)
    known = [i for i, item in enumerate(times) if item is not None]
    if not known:
        step = max(duration / max(n, 1), 0.18)
        return [{"start": i * step, "end": min(duration, (i + 1) * step)} for i in range(n)]

    first = known[0]
    first_start = times[first]["start"]  # type: ignore[index]
    if first:
        step = max(first_start / first, 0.12)
        for i in range(first):
            start = max(0.0, first_start - step * (first - i))
            times[i] = {"start": start, "end": min(first_start, start + step * 0.85)}

    for left, right in zip(known, known[1:]):
        if right - left <= 1:
            continue
        left_end = times[left]["end"]  # type: ignore[index]
        right_start = times[right]["start"]  # type: ignore[index]
        missing = right - left - 1
        available = max(0.08 * missing, right_start - left_end)
        step = available / (missing + 1)
        for offset, i in enumerate(range(left + 1, right), start=1):
            start = left_end + step * offset
            times[i] = {"start": start, "end": min(right_start, start + max(step * 0.78, 0.07))}

    last = known[-1]
    last_end = times[last]["end"]  # type: ignore[index]
    tail = n - last - 1
    if tail:
        step = max((max(duration, last_end + tail * 0.2) - last_end) / tail, 0.14)
        for offset, i in enumerate(range(last + 1, n), start=1):
            start = last_end + step * (offset - 1)
            times[i] = {"start": start, "end": start + step * 0.85}

    output: list[dict[str, float]] = []
    cursor = 0.0
    for item in times:
        assert item is not None
        start = max(cursor, float(item["start"]))
        end = max(start + 0.04, float(item["end"]))
        output.append({"start": round(start, 3), "end": round(end, 3)})
        cursor = start + 0.01
    return output


def _word_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    try:
        from rapidfuzz.fuzz import ratio
        return float(ratio(a, b)) / 100.0
    except Exception:
        return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()


def _fuzzy_alignment(a: list[str], b: list[str]) -> list[tuple[int, int, float]]:
    """Needleman-Wunsch-Alignment, abgestimmt auf bekannten Songtext."""
    n, m = len(a), len(b)
    gap = -0.46
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    bt = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i * gap; bt[i][0] = "u"
    for j in range(1, m + 1):
        dp[0][j] = j * gap; bt[0][j] = "l"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            sim = _word_similarity(a[i - 1], b[j - 1])
            diag = dp[i - 1][j - 1] + (1.6 * sim - 0.54)
            up = dp[i - 1][j] + gap
            left = dp[i][j - 1] + gap
            best = max(diag, up, left)
            dp[i][j] = best
            bt[i][j] = "d" if best == diag else ("u" if best == up else "l")
    pairs: list[tuple[int, int, float]] = []
    i, j = n, m
    while i > 0 or j > 0:
        move = bt[i][j]
        if move == "d":
            sim = _word_similarity(a[i - 1], b[j - 1])
            if sim >= 0.56:
                pairs.append((i - 1, j - 1, sim))
            i -= 1; j -= 1
        elif move == "u":
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def align_lyrics(cleaned: dict[str, Any], recognized: list[dict[str, Any]], duration: float) -> dict[str, Any]:
    lyric_words: list[dict[str, Any]] = []
    for line_index, line in enumerate(cleaned["lines"]):
        for word_index, word in enumerate(line["words"]):
            lyric_words.append({"text": word, "norm": normalize_word(word), "lineIndex": line_index, "wordIndex": word_index})
    lyric_norm = [item["norm"] for item in lyric_words]
    recognized_norm = [item["norm"] for item in recognized]
    pairs = _fuzzy_alignment(lyric_norm, recognized_norm)
    times: list[dict[str, float] | None] = [None] * len(lyric_words)
    matched = 0
    weighted_match = 0.0
    probabilities = 0.0
    for li, ri, similarity in pairs:
        times[li] = {"start": recognized[ri]["start"], "end": recognized[ri]["end"]}
        matched += 1
        weighted_match += similarity
        probabilities += float(recognized[ri].get("probability", 0.0))
    final_times = _interpolate_missing(times, duration)

    lines: list[dict[str, Any]] = []
    cursor = 0
    for index, line in enumerate(cleaned["lines"]):
        count = len(line["words"])
        items = []
        for word, timing in zip(line["words"], final_times[cursor:cursor + count]):
            items.append({"text": word, **timing})
        cursor += count
        lines.append({"index": index, "text": line["text"], "start": items[0]["start"], "end": items[-1]["end"], "words": items})
    confidence = weighted_match / max(len(lyric_words), 1)
    coverage = matched / max(len(lyric_words), 1)
    recognition_quality = probabilities / max(matched, 1)
    return {
        "lines": lines,
        "matchedWords": matched,
        "totalWords": len(lyric_words),
        "matchRatio": round(confidence, 3),
        "coverageRatio": round(coverage, 3),
        "recognitionQuality": round(recognition_quality, 3),
    }


def write_srt(path: Path, aligned: dict[str, Any]) -> None:
    """NEU gegenueber dem Schwesterprojekt (dort nur LRC/JSON): schreibt eine
    zeilenbasierte SRT-Datei ueber das im Hauptprojekt bereits vorhandene
    `srt`-Paket - kompatibel mit dem bestehenden core/srt_parser.py."""
    import srt as srt_lib

    subtitles = []
    for i, line in enumerate(aligned["lines"], start=1):
        subtitles.append(
            srt_lib.Subtitle(
                index=i,
                start=timedelta(seconds=max(0.0, line["start"])),
                end=timedelta(seconds=max(line["start"] + 0.3, line["end"])),
                content=line["text"],
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(srt_lib.compose(subtitles), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Songtext-zu-SRT Zeitabgleich (isoliertes Environment)")
    parser.add_argument("--audio", required=True, help="Pfad zur Songaudiodatei (voller Mix)")
    parser.add_argument("--lyrics-file", required=True, help="Pfad zu einer TXT-Datei mit dem eingegebenen Songtext")
    parser.add_argument("--staging-dir", required=True, help="Zielordner fuer Zwischendateien")
    parser.add_argument("--separator-model-dir", required=True, help="Cache-Ordner fuer das Vocal-Trennmodell")
    parser.add_argument("--separator-model-filename", default="UVR-MDX-NET-Voc_FT.onnx")
    parser.add_argument("--whisper-model-dir", required=True, help="Cache-Ordner fuer das Whisper-Modell")
    parser.add_argument("--whisper-model-name", default="medium")
    parser.add_argument("--language", default="de", help="Leer = automatische Spracherkennung")
    parser.add_argument("--output-srt", required=True, help="Zielpfad der erzeugten SRT-Datei")
    args = parser.parse_args()

    _configure_debug_logging()

    audio = Path(args.audio)
    lyrics_file = Path(args.lyrics_file)
    staging_dir = Path(args.staging_dir)
    output_srt = Path(args.output_srt)

    if not audio.exists():
        _error(f"Audiodatei nicht gefunden: {audio}")
        return 4
    if not lyrics_file.exists():
        _error(f"Songtext-Datei nicht gefunden: {lyrics_file}")
        return 4

    staging_dir.mkdir(parents=True, exist_ok=True)
    Path(args.separator_model_dir).mkdir(parents=True, exist_ok=True)
    Path(args.whisper_model_dir).mkdir(parents=True, exist_ok=True)

    ffmpeg_problem = _check_ffmpeg()
    if ffmpeg_problem:
        _error(f"FFmpeg-Preflight fehlgeschlagen: {ffmpeg_problem}")
        return 5
    _report(2, f"FFmpeg gefunden: {shutil.which('ffmpeg')}")

    try:
        cleaned = clean_lyrics_text(lyrics_file.read_text(encoding="utf-8"))
    except Exception as exc:
        _error(f"Songtext konnte nicht gelesen werden: {exc}")
        return 6

    try:
        from audio_separator.separator import Separator
    except ImportError as exc:
        _error(f"Die Bibliothek 'audio-separator' ist im isolierten Environment nicht verfuegbar: {exc}")
        return 2

    # Kompatibilitaets-Patch: audio-separator 0.44.5 ruft intern
    # librosa.get_duration(filename=...) auf. Neuere librosa-Versionen haben
    # das "filename"-Keyword entfernt (nur noch "path="), was beim Schreiben
    # jeder Ausgabedatei mit "TypeError: get_duration() got an unexpected
    # keyword argument 'filename'" abbricht - ohne dass audio-separator
    # selbst dafuer etwas kann. Da requirements-lyrics.txt bewusst KEINE
    # librosa-Version festlegt (sie kommt als transitive Abhaengigkeit von
    # audio-separator mit), wird hier stattdessen der Aufruf selbst
    # kompatibel gemacht, statt eine moeglicherweise mit anderen Paketen
    # kollidierende librosa-Version zu erzwingen.
    try:
        import librosa as _librosa
        _original_get_duration = _librosa.get_duration

        def _compat_get_duration(*_args, **_kwargs):
            if "filename" in _kwargs and "path" not in _kwargs:
                _kwargs["path"] = _kwargs.pop("filename")
            return _original_get_duration(*_args, **_kwargs)

        _librosa.get_duration = _compat_get_duration
    except Exception as exc:
        logging.getLogger(__name__).debug(f"librosa-Kompatibilitaets-Patch uebersprungen: {exc}")

    try:
        _report(5, "Vocal-Isolation: Modell wird geladen (ggf. einmaliger Download)")
        separator = Separator(
            output_dir=str(staging_dir),
            model_file_dir=str(args.separator_model_dir),
            output_format="WAV",
            log_level=logging.DEBUG,
        )
        separator.load_model(model_filename=args.separator_model_filename)

        _report(15, "Gesang wird vom Instrumental getrennt")
        output_files = separator.separate(
            str(audio), custom_output_names={"Vocals": "vocals_isolated", "Instrumental": "_instrumental_scratch"},
        )
        logging.getLogger(__name__).debug(f"separator.separate() Rueckgabe: {output_files!r}")

        isolated_vocals = staging_dir / "vocals_isolated.wav"
        if not isolated_vocals.exists():
            found = ", ".join(p.name for p in staging_dir.glob("*")) or "(keine Dateien)"
            _error(f"Vocal-Isolation ist gelaufen, aber die erwartete Datei fehlt. Gefunden: {found}")
            return 3

        _report(30, "Gesangsspur wird fuer die Spracherkennung aufbereitet")
        prepared = prepare_vocals_for_asr(isolated_vocals, staging_dir / "vocals_prepared.wav")

        _report(40, "Songtext wird analysiert")
        prompt = lyrics_prompt(cleaned)

        _report(45, f"Spracherkennung laeuft (Modell: {args.whisper_model_name}, kann einige Minuten dauern)")
        recognized, duration = transcribe_words(
            prepared, args.whisper_model_name, Path(args.whisper_model_dir),
            args.language, initial_prompt=prompt,
        )

        _report(85, "Songtext wird mit der Spracherkennung zeitlich abgeglichen")
        aligned = align_lyrics(cleaned, recognized, duration)
        logging.getLogger(__name__).debug(
            f"Alignment: {aligned['matchedWords']}/{aligned['totalWords']} Woerter zugeordnet, "
            f"matchRatio={aligned['matchRatio']}, coverageRatio={aligned['coverageRatio']}"
        )

        _report(95, "SRT-Datei wird geschrieben")
        write_srt(output_srt, aligned)

        if not output_srt.exists():
            _error("SRT-Datei konnte nicht geschrieben werden.")
            return 7

        _report(100, "Fertig")
        return 0
    except Exception as exc:  # noqa: BLE001 - bewusst breit: als ERROR-Zeile an den Hauptprozess melden statt crashen zu lassen
        _log_traceback(exc)
        _error(f"Unerwarteter Fehler beim Zeitabgleich: {exc} (voller Traceback: logs/lyrics_worker_error.log)")
        return 1
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
