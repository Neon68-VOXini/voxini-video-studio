"""Isoliertes Python-Environment fuer die automatische Gesichtskontrolle nach
der Generierung (Task #638 - siehe docs/Referenzbindung_Luecken_und_Plan.md,
Punkt 7).

Zweck: nach jeder erfolgreichen Clip-Generierung pruefen, ob das erzeugte
Gesicht tatsaechlich noch zum freigegebenen Charakter-Referenzbild passt -
damit VOXini Video Studio nicht die gleichen Identitaets-Drift-Probleme
bekommt wie beim realen "Willkommen bei Neon68"-Video, wo sich ein
Charaktergesicht von Szene zu Szene leicht gegenueber der Referenz
veraendert hat, ohne dass es automatisch aufgefallen waere.

Architektur (exakt das gleiche, bewaehrte Muster wie ".venv-lyrics", siehe
core/lyrics_align_env.py - dort im Detail begruendet): die Gesichtserkennung
braucht insightface + onnxruntime, schwere und eigenstaendige
Abhaengigkeiten, die die schlanke Haupt-App (PySide6 + ffmpeg) nicht
belasten sollen, wenn das Feature nie aktiviert wird. Deshalb ein drittes,
komplett getrenntes venv unter ".venv-faceverify" im
Projekt-Wurzelverzeichnis - wird NICHT automatisch angelegt, sondern erst
wenn Neon68 die Gesichtskontrolle in den Projekteinstellungen aktiviert UND
das Environment darueber explizit einrichtet (Fortschrittsanzeige). Ist es
zum Zeitpunkt einer Generierung aktiviert, aber noch nicht eingerichtet,
generiert VOXini den Clip trotzdem ganz normal (siehe generation_service.
generate_scene()) und vermerkt nur einen ehrlichen Hinweis - es wird NIE
mitten in einem laufenden (moeglicherweise unbeaufsichtigten Overnight-)
Batch-Auftrag stillschweigend ein mehrere hundert MB grosses Environment
heruntergeladen.

Die eigentliche Arbeit (Modell laden, Gesichter erkennen, Embeddings
vergleichen) laeuft in scripts/face_verify_worker.py als eigener
Unterprozess mit dem Python-Interpreter dieses Environments - das
Haupt-Environment importiert insightface/onnxruntime zu keinem Zeitpunkt
selbst.
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
VENV_DIR = APP_ROOT / ".venv-faceverify"
REQUIREMENTS_FILE = APP_ROOT / "requirements-faceverify.txt"
READY_MARKER = VENV_DIR / ".ready"

# Gleiche Vorsichtsmassnahme wie bei .venv-lyrics: onnxruntime/insightface
# stellen nicht immer sofort fertige Installationsdateien fuer die jeweils
# neueste Python-Version bereit. Gleiche Versionsliste wie dort, da bereits
# als verlaesslich verfuegbar bestaetigt.
COMPATIBLE_PYTHON_VERSIONS: list[str] = ["3.12", "3.11", "3.10"]

ProgressCallback = Callable[[int, str], None]

CRITICAL_IMPORTS: list[str] = [
    "numpy",
    "onnxruntime",
    "cv2",
    "insightface",
    "insightface.app",
    "PIL",
]

# Grobe, dokumentierte SCHAETZUNG (nicht live gemessen - von hier ohne
# Netzwerkzugriff nicht verifizierbar): insightface + onnxruntime (CPU) +
# opencv-python-headless liegen laut deren eigenen PyPI-Wheel-Groessen
# typischerweise bei ca. 150-250 MB. Das buffalo_l-Modellpaket selbst
# (Neon68-Entscheidung, siehe requirements-faceverify.txt) kommt beim
# allerersten tatsaechlichen Pruefungslauf zusaetzlich mit ca. 280 MB hinzu.
ESTIMATED_ENV_DOWNLOAD_MB = 250
ESTIMATED_MODEL_MB = 280
MODEL_SIZE_NOTE = "InsightFace 'buffalo_l', ca. 280 MB"


class FaceVerifyEnvError(RuntimeError):
    pass


class FaceVerifyPreflightError(FaceVerifyEnvError):
    """Sammel-Fehlermeldung des Dependency-Preflights: listet ALLE
    fehlenden/kaputten Module eines Laufs auf, nicht nur das erste."""


class FaceVerifyEnvCancelled(FaceVerifyEnvError):
    """Wird ausgeloest, wenn cancel_event WAEHREND der venv-Erstellung/
    pip-Install/Preflight gesetzt wird - nicht erst danach."""


def _noop_progress(_value: int, _stage: str) -> None:
    return None


def _find_compatible_python() -> str:
    """Siehe lyrics_align_env._find_compatible_python() - identisches
    Vorgehen ueber den Windows "py"-Launcher, gleiche Begruendung."""
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
    raise FaceVerifyEnvError(
        "Fuer die Gesichtskontrolle wird eine kompatible Python-Version "
        f"({', '.join(COMPATIBLE_PYTHON_VERSIONS)}) benoetigt - keine davon "
        "wurde auf diesem Rechner gefunden (geprueft ueber den Windows "
        "'py'-Launcher: 'py -3.12', 'py -3.11', 'py -3.10'). Bitte Python "
        "3.12 von https://www.python.org/downloads/ installieren (Haekchen "
        "bei 'Add python.exe to PATH' im Setup setzen) und danach erneut "
        "versuchen."
    )


def _python_version_of(python_path: Path) -> str | None:
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
    UND requirements-faceverify.txt seitdem unveraendert ist. Ein bloss
    existierender venv-Ordner zaehlt bewusst NICHT als bereit."""
    if not (venv_python_path().exists() and READY_MARKER.exists()):
        return False
    marker_contents = READY_MARKER.read_text(encoding="utf-8", errors="replace")
    return f"RequirementsHash: {_requirements_hash()}" in marker_contents


def environment_info() -> dict:
    """Fuer die UI-Anzeige VOR dem Einrichten: ob das isolierte Environment
    schon eingerichtet ist und, falls nicht, die dokumentierte
    Groessen-Schaetzung. Das buffalo_l-Modell selbst wird unabhaengig davon
    separat gezaehlt, da es auch bei bereits eingerichtetem Environment noch
    fehlen kann (Download beim allerersten tatsaechlichen Pruefungslauf)."""
    ready = is_ready()
    return {
        "ready": ready,
        "venvExists": venv_python_path().exists(),
        "estimatedEnvDownloadMb": 0 if ready else ESTIMATED_ENV_DOWNLOAD_MB,
        "modelNote": MODEL_SIZE_NOTE,
        "note": (
            "Isoliertes Environment fuer die Gesichtskontrolle bereits "
            "eingerichtet - kein erneuter Download der Bibliotheken noetig."
            if ready else
            "Beim Einrichten werden insightface, onnxruntime und "
            f"Abhaengigkeiten (Schaetzung, nicht live gemessen: ca. "
            f"{ESTIMATED_ENV_DOWNLOAD_MB} MB) heruntergeladen. Beim ersten "
            f"tatsaechlichen Pruefungslauf kommt zusaetzlich das "
            f"Gesichtsmodell ({MODEL_SIZE_NOTE}) hinzu."
        ),
    }


def run_dependency_preflight(
    venv_python: Path | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Siehe lyrics_align_env.run_dependency_preflight() - identisches
    Vorgehen: sammelt ALLE fehlenden Module in einem Lauf statt nur den
    ersten ImportError sichtbar zu machen."""
    python = venv_python or venv_python_path()
    if not python.exists():
        raise FaceVerifyPreflightError(
            f"Preflight nicht moeglich: Python-Interpreter fehlt ({python})."
        )
    if cancel_event is not None and cancel_event.is_set():
        raise FaceVerifyEnvCancelled("Vor dem Preflight abgebrochen.")

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
        raise FaceVerifyPreflightError(f"Preflight konnte nicht ausgefuehrt werden: {exc}") from exc

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
        raise FaceVerifyPreflightError(
            "Preflight hat das Zeitlimit (60s) ueberschritten - Prozess beendet."
        )
    finally:
        watcher_stop.set()
    returncode = proc.returncode

    if cancelled.is_set():
        raise FaceVerifyEnvCancelled("Preflight vom Nutzer abgebrochen.")

    log_path = error_log.log_dir() / "faceverify_preflight.log"
    with open(log_path, "a", encoding="utf-8") as log_handle:
        log_handle.write(f"\n--- Preflight {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log_handle.write(stdout or "")

    if returncode != 0 or "PREFLIGHT_MISSING" in (stdout or ""):
        details = (stdout or "").strip() or f"Exit-Code {returncode}"
        raise FaceVerifyPreflightError(
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
        raise FaceVerifyEnvCancelled(f"Vor Schritt '{step_label}' abgebrochen.")

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
        raise FaceVerifyEnvCancelled(f"Schritt '{step_label}' wurde abgebrochen.")

    if code != 0:
        raise FaceVerifyEnvError(
            f"Schritt '{step_label}' ist mit Fehlercode {code} fehlgeschlagen. "
            f"Details: {error_log.log_dir() / 'faceverify_env_setup.log'}"
        )


def ensure_faceverify_env(
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Stellt sicher, dass ".venv-faceverify" existiert und insightface +
    onnxruntime installiert sind. Gibt den Pfad zum Python-Interpreter
    dieses Environments zurueck. Wird NUR durch eine explizite Nutzeraktion
    in den Projekteinstellungen aufgerufen (siehe ui/, "Gesichtskontrolle
    einrichten") - NIE automatisch/stillschweigend waehrend einer
    Generierung, siehe Moduldocstring."""
    report = progress or _noop_progress

    if is_ready():
        return venv_python_path()

    if cancel_event is not None and cancel_event.is_set():
        raise FaceVerifyEnvCancelled("Vor Environment-Einrichtung abgebrochen.")

    if not REQUIREMENTS_FILE.exists():
        raise FaceVerifyEnvError(
            f"requirements-faceverify.txt fehlt ({REQUIREMENTS_FILE}). "
            "Bitte vollstaendiges Release erneut installieren."
        )

    log_dir = error_log.log_dir()
    log_path = log_dir / "faceverify_env_setup.log"
    READY_MARKER.unlink(missing_ok=True)

    with open(log_path, "a", encoding="utf-8") as log_handle:
        log_handle.write("\n============================================================\n")
        log_handle.write(f"Faceverify-Environment-Setup gestartet: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

        report(5, "Isoliertes Environment wird einmalig eingerichtet (kann mehrere Minuten dauern)")

        compatible_python = _find_compatible_python()
        log_handle.write(f"Kompatibler Python-Interpreter fuer .venv-faceverify: {compatible_python}\n")

        if VENV_DIR.exists():
            existing_python = venv_python_path()
            existing_version = _python_version_of(existing_python) if existing_python.exists() else None
            if existing_version not in COMPATIBLE_PYTHON_VERSIONS:
                log_handle.write(
                    f"Vorhandenes .venv-faceverify verwendet eine inkompatible oder "
                    f"nicht mehr lauffaehige Python-Version ({existing_version or 'unbekannt'}) "
                    "- wird geloescht und mit einer kompatiblen Version neu angelegt.\n"
                )
                shutil.rmtree(VENV_DIR, ignore_errors=True)

        if not VENV_DIR.exists():
            _run_logged([compatible_python, "-m", "venv", str(VENV_DIR)], log_handle, "venv anlegen", cancel_event)

        venv_python = venv_python_path()
        if not venv_python.exists():
            raise FaceVerifyEnvError(
                f"venv wurde angelegt, aber Python-Interpreter fehlt: {venv_python}"
            )

        report(15, "pip wird im isolierten Environment aktualisiert")
        _run_logged([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], log_handle, "pip aktualisieren", cancel_event)

        report(20, f"insightface und onnxruntime werden installiert (einmalig, Schaetzung: ca. {ESTIMATED_ENV_DOWNLOAD_MB} MB)")
        _run_logged(
            [str(venv_python), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)],
            log_handle, "requirements-faceverify.txt installieren", cancel_event,
        )

        report(35, "Preflight: installierte Abhaengigkeiten werden geprueft")
        log_handle.write("\n--- Preflight nach Installation ---\n")
        try:
            run_dependency_preflight(venv_python, cancel_event)
        except FaceVerifyPreflightError as exc:
            log_handle.write(f"Preflight fehlgeschlagen: {exc}\n")
            raise
        except FaceVerifyEnvCancelled:
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
