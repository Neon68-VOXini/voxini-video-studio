"""Isoliertes Python-Environment fuer das Songtext-zu-SRT-Feature (automatischer
Zeitabgleich eines eingetippten Songtexts mit der Audiodatei).

Architektur (gleiches, bewaehrtes Muster wie ".venv-vocalsplit" im
Schwesterprojekt VOXini Studio, siehe dortige app/vocal_split_env.py):
das Feature braucht audio-separator (Vocal-Isolation) und faster-whisper
(Spracherkennung) - beides schwere ML-Abhaengigkeiten (torch, onnxruntime,
ctranslate2), die die schlanke Haupt-App (PySide6 + ffmpeg) nicht belasten
sollen, wenn das Feature nie genutzt wird. Deshalb ein zweites, komplett
getrenntes venv unter ".venv-lyrics" im Projekt-Wurzelverzeichnis - wird
NICHT bei jedem Programmstart angelegt, sondern erst beim ersten
tatsaechlichen Klick auf "Automatisch synchronisieren" (siehe
core/lyrics_align.py::align_lyrics_to_audio()). Danach bleibt es bestehen
und wird bei jedem weiteren Lauf direkt wiederverwendet.

Die eigentliche Arbeit (Vocal-Isolation + Transkription + Zeitabgleich)
laeuft in scripts/lyrics_align_worker.py als eigener Unterprozess mit dem
Python-Interpreter dieses Environments - das Haupt-Environment importiert
audio_separator/faster_whisper zu keinem Zeitpunkt selbst.
"""
from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from voxini_studio.core import error_log

APP_ROOT = Path(__file__).resolve().parent.parent.parent
VENV_DIR = APP_ROOT / ".venv-lyrics"
REQUIREMENTS_FILE = APP_ROOT / "requirements-lyrics.txt"
READY_MARKER = VENV_DIR / ".ready"

# Der ML-Stack fuer dieses Feature (torch, onnxruntime, audio-separator und
# insbesondere dessen Abhaengigkeit "diffq-fixed") stellt nicht immer
# fertige Installationsdateien (Wheels) fuer die jeweils neueste
# Python-Version bereit - fehlt ein Wheel, versucht pip einen
# Quellcode-Build, der bei diffq-fixed nachweislich kaputt ist (fehlende
# .pyx-Datei im Quellpaket). Die Haupt-App kann bereits auf einer neueren
# Python-Version laufen (z.B. 3.14) als der ML-Stack unterstuetzt. Deshalb
# wird ".venv-lyrics" bewusst NICHT mit dem gleichen Interpreter wie die
# Haupt-App angelegt, sondern gezielt mit einer der folgenden, gut
# unterstuetzten Versionen (ueber den Windows "py"-Launcher gesucht, in
# dieser Reihenfolge):
COMPATIBLE_PYTHON_VERSIONS: list[str] = ["3.12", "3.11", "3.10"]

ProgressCallback = Callable[[int, str], None]

# Gleiches Prinzip wie vocal_split_env.CRITICAL_IMPORTS: prueft VOR jedem
# echten Lauf, ob alle vom Worker tatsaechlich benoetigten Module im
# isolierten Environment importierbar sind - sammelt ALLE fehlenden Module
# in einem Lauf, statt nur den ersten ImportError sichtbar zu machen.
CRITICAL_IMPORTS: list[str] = [
    "numpy",
    "torch",
    "onnxruntime",
    "librosa",
    "audioread",
    "soundfile",
    "audio_separator.separator",
    "faster_whisper",
    "rapidfuzz",
    "scipy",
    "srt",
]

# Grobe, dokumentierte SCHAETZUNG (nicht live gemessen - von hier ohne
# Netzwerkzugriff nicht verifizierbar): audio-separator selbst ist klein,
# aber torch (CPU-Build) + onnxruntime + Abhaengigkeiten liegen laut deren
# eigenen PyPI-Wheel-Groessen typischerweise bei ca. 600-900 MB, das
# UVR-Trennmodell kommt beim allerersten Lauf zusaetzlich mit ca. 60-120 MB
# hinzu. Das faster-whisper "medium"-Modell kommt beim allerersten
# Transkriptionslauf mit ca. 1,5 GB hinzu (separat, siehe lyrics_align.py).
# Wird im UI als Richtwert VOR dem Start angezeigt, ausdruecklich als
# Schaetzung markiert - nicht live gemessen.
ESTIMATED_ENV_DOWNLOAD_MB = 900
ESTIMATED_WHISPER_MODEL_MB = 1500
SEPARATOR_MODEL_SIZE_NOTE = "UVR-MDX-NET-Voc_FT.onnx, ca. 60-120 MB"
WHISPER_MODEL_SIZE_NOTE = "Whisper 'medium' (faster-whisper), ca. 1,5 GB"


class LyricsEnvError(RuntimeError):
    pass


class LyricsPreflightError(LyricsEnvError):
    """Praezise Sammel-Fehlermeldung des Dependency-Preflights: listet ALLE
    fehlenden/kaputten Module eines Laufs auf, nicht nur das erste."""


class LyricsEnvCancelled(LyricsEnvError):
    """Wird ausgeloest, wenn cancel_event WAEHREND der venv-Erstellung/
    pip-Install/Preflight gesetzt wird - nicht erst danach."""


def _noop_progress(_value: int, _stage: str) -> None:
    return None


def _find_compatible_python() -> str:
    """Sucht ueber den Windows "py"-Launcher nach einer fuer den ML-Stack
    (torch/onnxruntime/audio-separator/diffq-fixed) kompatiblen
    Python-Version (siehe COMPATIBLE_PYTHON_VERSIONS oben). Auf
    Nicht-Windows-Systemen (nur fuer lokale Entwicklung/Tests relevant)
    wird der aktuelle Interpreter verwendet, da es dort keinen
    "py"-Launcher gibt."""
    if platform.system() != "Windows":
        return sys.executable
    for version in COMPATIBLE_PYTHON_VERSIONS:
        try:
            result = subprocess.run(
                ["py", f"-{version}", "-c", "import sys; print(sys.executable)"],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            continue
        if result.returncode == 0:
            path = result.stdout.strip()
            if path:
                return path
    raise LyricsEnvError(
        "Fuer den Songtext-Zeitabgleich wird eine kompatible Python-Version "
        f"({', '.join(COMPATIBLE_PYTHON_VERSIONS)}) benoetigt - keine davon "
        "wurde auf diesem Rechner gefunden (geprueft ueber den Windows "
        "'py'-Launcher: 'py -3.12', 'py -3.11', 'py -3.10'). Die aktuell "
        "installierte Python-Version ist dafuer zu neu: mehrere benoetigte "
        "Pakete (u.a. 'diffq-fixed', eine Abhaengigkeit von "
        "audio-separator) stellen dafuer noch keine fertigen "
        "Installationsdateien bereit. Bitte Python 3.12 von "
        "https://www.python.org/downloads/ installieren (Haekchen bei "
        "'Add python.exe to PATH' im Setup setzen) und danach erneut "
        "versuchen."
    )


def _python_version_of(python_path: Path) -> str | None:
    """Gibt die Version (z.B. '3.12') des angegebenen Interpreters zurueck,
    oder None, falls er nicht ausfuehrbar ist."""
    try:
        result = subprocess.run(
            [str(python_path), "-c",
             "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def venv_python_path() -> Path:
    if platform.system() == "Windows":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _requirements_hash() -> str:
    if not REQUIREMENTS_FILE.exists():
        return ""
    return hashlib.sha256(REQUIREMENTS_FILE.read_bytes()).hexdigest()


def is_ready() -> bool:
    """True nur, wenn ein FRUEHERES Setup wirklich vollstaendig durchgelaufen
    ist (Marker-Datei, wird erst nach erfolgreichem Preflight geschrieben)
    UND requirements-lyrics.txt seitdem unveraendert ist. Ein bloss
    existierender venv-Ordner (z.B. aus einem abgebrochenen Setup-Versuch)
    zaehlt bewusst NICHT als bereit."""
    if not (venv_python_path().exists() and READY_MARKER.exists()):
        return False
    marker_contents = READY_MARKER.read_text(encoding="utf-8", errors="replace")
    return f"RequirementsHash: {_requirements_hash()}" in marker_contents


def environment_info() -> dict:
    """Fuer die UI-Anzeige VOR dem Start: ob das isolierte Environment schon
    eingerichtet ist (dann faellt kein Download mehr fuer die Bibliotheken
    an) und, falls nicht, die dokumentierte Groessen-Schaetzung. Das
    Whisper-Modell selbst wird unabhaengig davon separat gezaehlt, da es
    auch bei bereits eingerichtetem Environment noch fehlen kann (Download
    beim allerersten tatsaechlichen Transkriptionslauf)."""
    ready = is_ready()
    return {
        "ready": ready,
        "venvExists": venv_python_path().exists(),
        "estimatedEnvDownloadMb": 0 if ready else ESTIMATED_ENV_DOWNLOAD_MB,
        "separatorModelNote": SEPARATOR_MODEL_SIZE_NOTE,
        "whisperModelNote": WHISPER_MODEL_SIZE_NOTE,
        "note": (
            "Isoliertes Environment bereits eingerichtet - kein erneuter Download "
            "der Bibliotheken noetig."
            if ready else
            "Beim ersten Start werden audio-separator und Abhaengigkeiten "
            f"(Schaetzung, nicht live gemessen: ca. {ESTIMATED_ENV_DOWNLOAD_MB} MB) "
            "heruntergeladen. Beim ersten tatsaechlichen Synchronisierungslauf "
            f"kommen zusaetzlich das Vocal-Trennmodell ({SEPARATOR_MODEL_SIZE_NOTE}) "
            f"und das Spracherkennungsmodell ({WHISPER_MODEL_SIZE_NOTE}) hinzu."
        ),
    }


def run_dependency_preflight(
    venv_python: Path | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Prueft VOR jedem echten Lauf (nicht nur beim allerersten Setup), ob
    alle vom Worker tatsaechlich benoetigten Module im isolierten
    Environment importierbar sind - sammelt ALLE fehlenden Module in einem
    einzigen Lauf. Wirft LyricsPreflightError mit einer vollstaendigen,
    konkreten Liste, wenn etwas fehlt."""
    python = venv_python or venv_python_path()
    if not python.exists():
        raise LyricsPreflightError(
            f"Preflight nicht moeglich: Python-Interpreter fehlt ({python})."
        )
    if cancel_event is not None and cancel_event.is_set():
        raise LyricsEnvCancelled("Vor dem Preflight abgebrochen.")

    probe = (
        "import importlib, sys\n"
        f"modules = {CRITICAL_IMPORTS!r}\n"
        "missing = []\n"
        "for m in modules:\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "    except Exception as exc:\n"
        "        missing.append(f'{m}: {type(exc).__name__}: {exc}')\n"
        "if missing:\n"
        "    print('PREFLIGHT_MISSING')\n"
        "    for line in missing:\n"
        "        print(line)\n"
        "    sys.exit(1)\n"
        "print('PREFLIGHT_OK')\n"
    )
    try:
        proc = subprocess.Popen(
            [str(python), "-c", probe],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace",
        )
    except Exception as exc:
        raise LyricsPreflightError(f"Preflight konnte nicht ausgefuehrt werden: {exc}") from exc

    cancelled = threading.Event()
    watcher_stop = threading.Event()

    def _watch() -> None:
        while not watcher_stop.is_set():
            if cancel_event is not None and cancel_event.is_set():
                cancelled.set()
                try:
                    proc.terminate()
                except Exception:
                    pass
                return
            watcher_stop.wait(0.3)

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()
    try:
        stdout, _ = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, _ = proc.communicate()
        watcher_stop.set()
        raise LyricsPreflightError(
            "Preflight hat das Zeitlimit (60s) ueberschritten - Prozess beendet."
        )
    finally:
        watcher_stop.set()
    returncode = proc.returncode

    if cancelled.is_set():
        raise LyricsEnvCancelled("Preflight vom Nutzer abgebrochen.")

    log_path = error_log.log_dir() / "lyrics_preflight.log"
    with open(log_path, "a", encoding="utf-8") as log_handle:
        log_handle.write(f"\n--- Preflight {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log_handle.write(stdout or "")

    if returncode != 0 or "PREFLIGHT_MISSING" in (stdout or ""):
        details = (stdout or "").strip() or f"Exit-Code {returncode}"
        raise LyricsPreflightError(
            "Dependency-Preflight fehlgeschlagen - folgende Module sind im "
            f"isolierten Environment nicht (korrekt) verfuegbar:\n{details}\n"
            f"Details auch in {log_path}."
        )


def _run_logged(
    command: list[str],
    log_handle,
    step_label: str,
    cancel_event: threading.Event | None = None,
) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise LyricsEnvCancelled(f"Vor Schritt '{step_label}' abgebrochen.")

    log_handle.write(f"\n--- {step_label} ---\n")
    log_handle.write(f"Befehl: {command}\n")
    log_handle.flush()
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", bufsize=1,
    )

    cancelled = threading.Event()
    watcher_stop = threading.Event()

    def _watch() -> None:
        while not watcher_stop.is_set():
            if cancel_event is not None and cancel_event.is_set():
                cancelled.set()
                try:
                    proc.terminate()
                except Exception:
                    pass
                return
            watcher_stop.wait(0.3)

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()

    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            log_handle.write(line)
            log_handle.flush()
        code = proc.wait()
    finally:
        watcher_stop.set()

    if cancelled.is_set():
        log_handle.write(f"Schritt '{step_label}' wurde vom Nutzer abgebrochen (Prozess beendet).\n")
        raise LyricsEnvCancelled(f"Schritt '{step_label}' wurde abgebrochen.")

    if code != 0:
        raise LyricsEnvError(
            f"Schritt '{step_label}' ist mit Fehlercode {code} fehlgeschlagen. "
            f"Details: {error_log.log_dir() / 'lyrics_env_setup.log'}"
        )


def ensure_lyrics_env(
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Stellt sicher, dass ".venv-lyrics" existiert und audio-separator +
    faster-whisper installiert sind. Gibt den Pfad zum Python-Interpreter
    dieses Environments zurueck. Legt es beim allerersten Aufruf einmalig
    an (kann mehrere Minuten dauern) - bei jedem weiteren Aufruf wird direkt
    der vorhandene Pfad zurueckgegeben, ohne pip erneut zu bemuehen."""
    report = progress or _noop_progress

    if is_ready():
        return venv_python_path()

    if cancel_event is not None and cancel_event.is_set():
        raise LyricsEnvCancelled("Vor Environment-Einrichtung abgebrochen.")

    if not REQUIREMENTS_FILE.exists():
        raise LyricsEnvError(
            f"requirements-lyrics.txt fehlt ({REQUIREMENTS_FILE}). "
            "Bitte vollstaendiges Release erneut installieren."
        )

    log_dir = error_log.log_dir()
    log_path = log_dir / "lyrics_env_setup.log"
    READY_MARKER.unlink(missing_ok=True)

    with open(log_path, "a", encoding="utf-8") as log_handle:
        log_handle.write("\n============================================================\n")
        log_handle.write(f"Lyrics-Environment-Setup gestartet: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

        report(5, "Isoliertes Environment wird einmalig eingerichtet (kann mehrere Minuten dauern)")

        compatible_python = _find_compatible_python()
        log_handle.write(f"Kompatibler Python-Interpreter fuer .venv-lyrics: {compatible_python}\n")

        if VENV_DIR.exists():
            existing_python = venv_python_path()
            existing_version = _python_version_of(existing_python) if existing_python.exists() else None
            if existing_version not in COMPATIBLE_PYTHON_VERSIONS:
                log_handle.write(
                    f"Vorhandenes .venv-lyrics verwendet eine inkompatible oder "
                    f"nicht mehr lauffaehige Python-Version ({existing_version or 'unbekannt'}) "
                    "- wird geloescht und mit einer kompatiblen Version neu angelegt.\n"
                )
                shutil.rmtree(VENV_DIR, ignore_errors=True)

        if not VENV_DIR.exists():
            # OHNE --system-site-packages, sonst waere die Isolation von
            # etwaigen zukuenftigen Versionsanforderungen des Haupt-
            # Environments hinfaellig.
            _run_logged([compatible_python, "-m", "venv", str(VENV_DIR)], log_handle, "venv anlegen", cancel_event)

        venv_python = venv_python_path()
        if not venv_python.exists():
            raise LyricsEnvError(
                f"venv wurde angelegt, aber Python-Interpreter fehlt: {venv_python}"
            )

        report(15, "pip wird im isolierten Environment aktualisiert")
        _run_logged([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], log_handle, "pip aktualisieren", cancel_event)

        report(20, f"audio-separator und faster-whisper werden installiert (einmalig, Schaetzung: ca. {ESTIMATED_ENV_DOWNLOAD_MB} MB)")
        _run_logged(
            [str(venv_python), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)],
            log_handle, "requirements-lyrics.txt installieren", cancel_event,
        )

        report(35, "Preflight: installierte Abhaengigkeiten werden geprueft")
        log_handle.write("\n--- Preflight nach Installation ---\n")
        try:
            run_dependency_preflight(venv_python, cancel_event)
        except LyricsPreflightError as exc:
            log_handle.write(f"Preflight fehlgeschlagen: {exc}\n")
            raise
        except LyricsEnvCancelled:
            log_handle.write("Preflight vom Nutzer abgebrochen.\n")
            raise
        log_handle.write("Preflight erfolgreich - alle kritischen Module importierbar.\n")

        READY_MARKER.write_text(
            f"Eingerichtet: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Python: {venv_python}\n"
            f"RequirementsHash: {_requirements_hash()}\n",
            encoding="utf-8",
        )
        log_handle.write("Setup erfolgreich abgeschlossen.\n")

    report(40, "Environment bereit")
    return venv_python_path()
