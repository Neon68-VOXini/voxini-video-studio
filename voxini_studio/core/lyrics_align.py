"""Songtext-zu-SRT: orchestriert den isolierten Lyrics-Align-Worker-Prozess
(scripts/lyrics_align_worker.py) vom Haupt-Environment aus.

Nimmt einen vom Nutzer eingetippten Songtext plus die bereits importierte
Audiodatei des Projekts entgegen, laesst den Zeitabgleich im isolierten
".venv-lyrics"-Environment laufen (siehe core/lyrics_align_env.py) und liefert
eine fertige SRT-Datei - die anschliessend ueber die bestehende
Korrekturansicht (ui/lyrics_sync_dialog.py) geprueft/angepasst werden kann,
bevor sie wie eine normal importierte SRT-Datei ins Projekt uebernommen wird.

Dieses Modul importiert audio_separator/faster_whisper zu keinem Zeitpunkt
selbst - nur der Worker-Subprozess (im isolierten Environment) tut das.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

from voxini_studio.core import ffmpeg_locator, lyrics_align_env

WORKER_SCRIPT = lyrics_align_env.APP_ROOT / "scripts" / "lyrics_align_worker.py"

DEFAULT_WHISPER_MODEL = "medium"
DEFAULT_SEPARATOR_MODEL = "UVR-MDX-NET-Voc_FT.onnx"

ProgressCallback = Callable[[int, str], None]


class LyricsAlignError(RuntimeError):
    pass


class LyricsAlignCancelled(LyricsAlignError):
    """Wird ausgeloest, wenn cancel_event waehrend Environment-Einrichtung,
    Preflight oder dem laufenden Worker-Subprozess gesetzt wird."""


def _noop_progress(_value: int, _stage: str) -> None:
    return None


def models_root() -> Path:
    # Projektuebergreifender, gemeinsamer Modell-Cache (nicht pro Projekt),
    # analog zum ComfyUI-Modellordner - Whisper/Trennmodell muessen nicht je
    # Projekt neu heruntergeladen werden.
    return lyrics_align_env.APP_ROOT / "models" / "lyrics-align"


def align_lyrics_to_audio(
    lyrics_text: str,
    audio_path: Path,
    dest_srt_path: Path,
    language: str = "de",
    whisper_model: str = DEFAULT_WHISPER_MODEL,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Fuehrt den kompletten Zeitabgleich durch und schreibt das Ergebnis
    direkt nach dest_srt_path. Laeuft typischerweise mehrere Minuten
    (haengt von Songlaenge, CPU und ob das Environment/die Modelle schon
    eingerichtet sind ab) - vom Aufrufer als Hintergrund-Thread einzuplanen.

    cancel_event: wird vor und waehrend jeder Phase (Environment-Einrichtung,
    Preflight, Worker-Subprozess) geprueft; bei Abbruch wird der Subprozess
    terminiert und LyricsAlignCancelled ausgeloest."""
    report = progress or _noop_progress
    if not lyrics_text.strip():
        raise LyricsAlignError("Bitte zuerst einen Songtext eingeben.")
    if not audio_path.exists():
        raise LyricsAlignError(f"Audiodatei nicht gefunden: {audio_path}")
    if not WORKER_SCRIPT.exists():
        raise LyricsAlignError(
            f"Worker-Skript fehlt: {WORKER_SCRIPT}. Bitte vollstaendiges Release erneut installieren."
        )

    if cancel_event is not None and cancel_event.is_set():
        raise LyricsAlignCancelled("Vor dem Start abgebrochen.")

    try:
        venv_python = lyrics_align_env.ensure_lyrics_env(progress=report, cancel_event=cancel_event)
    except lyrics_align_env.LyricsEnvCancelled as exc:
        raise LyricsAlignCancelled(str(exc)) from exc
    except lyrics_align_env.LyricsEnvError as exc:
        raise LyricsAlignError(
            f"Isoliertes Environment fuer den Zeitabgleich konnte nicht eingerichtet werden: {exc}"
        ) from exc

    if cancel_event is not None and cancel_event.is_set():
        raise LyricsAlignCancelled("Nach Environment-Einrichtung abgebrochen.")

    report(38, "Preflight: Abhaengigkeiten werden geprueft")
    try:
        lyrics_align_env.run_dependency_preflight(venv_python, cancel_event)
    except lyrics_align_env.LyricsEnvCancelled as exc:
        raise LyricsAlignCancelled(str(exc)) from exc
    except lyrics_align_env.LyricsPreflightError as exc:
        raise LyricsAlignError(str(exc)) from exc

    staging_root = Path(tempfile.mkdtemp(prefix="voxini_lyrics_"))
    lyrics_file = staging_root / "lyrics.txt"
    lyrics_file.write_text(lyrics_text, encoding="utf-8")

    separator_model_dir = models_root() / "audio-separator"
    whisper_model_dir = models_root() / "whisper"
    separator_model_dir.mkdir(parents=True, exist_ok=True)
    whisper_model_dir.mkdir(parents=True, exist_ok=True)

    command = [
        str(venv_python), "-u", str(WORKER_SCRIPT),
        "--audio", str(audio_path),
        "--lyrics-file", str(lyrics_file),
        "--staging-dir", str(staging_root / "work"),
        "--separator-model-dir", str(separator_model_dir),
        "--separator-model-filename", DEFAULT_SEPARATOR_MODEL,
        "--whisper-model-dir", str(whisper_model_dir),
        "--whisper-model-name", whisper_model,
        "--language", language,
        "--output-srt", str(dest_srt_path),
    ]

    # Das Hauptprojekt bringt bereits ein korrekt "ffmpeg.exe" benanntes
    # FFmpeg-Binary mit (core/ffmpeg_locator.py) - dessen Ordner wird dem
    # Worker-Subprozess vorangestellt im PATH mitgegeben, damit
    # audio-separator es findet, ohne selbst eine eigene FFmpeg-Installation
    # zu brauchen (anders als im Schwesterprojekt VOXini Studio ist hier
    # kein Umbenennungs-Shim noetig, da der bundled Build hier schon
    # korrekt "ffmpeg.exe" heisst).
    bundled_ffmpeg = Path(ffmpeg_locator.ffmpeg_path())
    env = os.environ.copy()
    if bundled_ffmpeg.is_file():
        env["PATH"] = str(bundled_ffmpeg.parent) + os.pathsep + env.get("PATH", "")

    watcher_stop = threading.Event()
    cancelled_by_user = threading.Event()

    def _watch_cancel(proc_ref: subprocess.Popen) -> None:
        while not watcher_stop.is_set():
            if cancel_event is not None and cancel_event.is_set():
                cancelled_by_user.set()
                try:
                    proc_ref.terminate()
                except Exception:
                    pass
                return
            watcher_stop.wait(0.5)

    try:
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1, env=env,
        )
        watcher = threading.Thread(target=_watch_cancel, args=(proc,), daemon=True)
        watcher.start()
        assert proc.stdout is not None
        last_error: str | None = None
        # Fortschrittsbereich 40-100: 0-40 gehoert der (meist einmaligen)
        # Environment-Einrichtung/dem Preflight oben.
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("PROGRESS "):
                rest = line[len("PROGRESS "):]
                value_str, _, stage = rest.partition(" ")
                try:
                    value = int(value_str)
                except ValueError:
                    continue
                scaled = 40 + max(0, min(100, value)) * 60 // 100
                report(scaled, stage or "Zeitabgleich laeuft")
            elif line.startswith("ERROR "):
                last_error = line[len("ERROR "):]

        code = proc.wait()
        watcher_stop.set()
        if cancelled_by_user.is_set():
            raise LyricsAlignCancelled("Zeitabgleich wurde vom Nutzer abgebrochen.")
        if code != 0:
            detail = last_error or f"Worker-Prozess wurde mit Fehlercode {code} beendet."
            raise LyricsAlignError(detail)

        if not dest_srt_path.exists():
            raise LyricsAlignError("Der Worker-Prozess hat Erfolg gemeldet, aber die SRT-Datei fehlt.")

        report(100, "Songtext-Zeitabgleich fertig")
        return dest_srt_path
    finally:
        watcher_stop.set()
        shutil.rmtree(staging_root, ignore_errors=True)
