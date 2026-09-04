"""Fuehrt den eigentlichen Update-Austausch der laufenden .exe durch - erst
NACH expliziter Bestaetigung durch den Nutzer im Update-Dialog (siehe
update_dialog.py). Sichert die bisherige .exe immer zuerst als
".exe.vorherige_version_backup" (gleiche Namenskonvention wie beim
Schwesterprojekt VOXini Studio), sodass ueber revert_to_previous_version()
jederzeit ein Rueckschritt moeglich ist. Funktioniert nur fuer die
tatsaechlich gebaute/gepackte .exe (sys.frozen) - im
Python-Entwicklungsbetrieb (python -m voxini_studio, kein PyInstaller-
Build) gibt es keine .exe zum Ersetzen, siehe is_running_as_frozen_exe()."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional

from voxini_studio.core.model_downloader import DownloadError, download_file

_BACKUP_SUFFIX = ".vorherige_version_backup"
_DOWNLOAD_SUFFIX = ".update_download"


class UpdateInstallError(RuntimeError):
    pass


def is_running_as_frozen_exe() -> bool:
    """True nur, wenn dies tatsaechlich die PyInstaller-gebaute .exe ist
    (nicht der Python-Entwicklungsbetrieb) - Download/Installation/Rollback
    ergeben nur dann Sinn, siehe voxini_studio.spec (PyInstaller-Onefile)."""
    return bool(getattr(sys, "frozen", False))


def current_exe_path() -> Path:
    if not is_running_as_frozen_exe():
        raise UpdateInstallError(
            "Kein Update möglich: Dies ist der Python-Entwicklungsbetrieb, keine gebaute .exe."
        )
    return Path(sys.executable)


def backup_path_for(exe_path: Path) -> Path:
    return exe_path.with_name(exe_path.name + _BACKUP_SUFFIX)


def download_update(
    download_url: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Path:
    """Laedt die neue .exe NEBEN die laufende .exe herunter (temporaerer
    Name "<EXE-Name>.update_download"), OHNE die laufende Datei bereits
    anzufassen - erst install_downloaded_update() tauscht nach
    erfolgreichem Download tatsaechlich um. Nutzt dieselbe .part-Datei-
    Sicherheit wie model_downloader.download_file() (siehe core/
    model_downloader.py) - ein abgebrochener/fehlgeschlagener Download
    laesst nie eine kaputt aussehende fertige Datei zurueck."""
    exe_path = current_exe_path()
    dest = exe_path.with_name(exe_path.name + _DOWNLOAD_SUFFIX)
    try:
        download_file(
            download_url,
            dest,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
    except DownloadError as exc:
        raise UpdateInstallError(str(exc)) from exc
    return dest


def install_downloaded_update(downloaded_path: Path) -> None:
    """Tauscht die laufende .exe gegen die heruntergeladene neue Version:
    aktuelle .exe -> ".exe.vorherige_version_backup" (ein vorhandenes
    aelteres Backup wird dabei ersetzt - es gibt bewusst nur EINE
    Rueckfallstufe, keine Versionshistorie), dann wird die heruntergeladene
    Datei an die Stelle der bisherigen .exe verschoben.

    WICHTIG: Windows kann die Datei einer laufenden .exe nicht
    ueberschreiben, aber SEHR WOHL umbenennen/verschieben (der laufende
    Prozess haelt lediglich ein Datei-Handle, keinen Namens-Lock) - genau
    das nutzt dieser Tausch aus. Die Anwendung muss danach trotzdem neu
    gestartet werden, damit der naechste Start tatsaechlich die neue Datei
    ausfuehrt (siehe update_dialog.py, Neustart-Angebot)."""
    exe_path = current_exe_path()
    backup = backup_path_for(exe_path)
    if backup.exists():
        backup.unlink()
    os.rename(exe_path, backup)
    try:
        os.rename(downloaded_path, exe_path)
    except OSError:
        # Umbenennen der neuen Datei fehlgeschlagen - alten Stand sofort
        # wiederherstellen, statt den Nutzer ohne startfaehige .exe dastehen
        # zu lassen.
        os.rename(backup, exe_path)
        raise


def can_revert_to_previous_version() -> bool:
    if not is_running_as_frozen_exe():
        return False
    return backup_path_for(current_exe_path()).exists()


def revert_to_previous_version() -> None:
    """Macht install_downloaded_update() rueckgaengig: die aktuelle (neue)
    .exe wird durch das ".exe.vorherige_version_backup" ersetzt. Die
    verworfene "neue" Version wird geloescht, nicht aufgehoben - es gibt
    weiterhin nur eine einzige Rueckfallstufe. Auch hier gilt: die
    Anwendung muss danach neu gestartet werden."""
    exe_path = current_exe_path()
    backup = backup_path_for(exe_path)
    if not backup.exists():
        raise UpdateInstallError("Keine vorherige Version zum Zurückkehren gefunden.")
    os.remove(exe_path)
    os.rename(backup, exe_path)


def relaunch_and_exit() -> None:
    """Startet die (nach einem Update/Rollback jetzt am Zielpfad liegende)
    .exe als neuen, unabhaengigen Prozess und beendet den aktuellen -
    fuer den optionalen "Jetzt neu starten"-Button im Update-Dialog. Nur
    aufrufbar, wenn is_running_as_frozen_exe() True ist.

    WICHTIG (per Design von install_downloaded_update()): zum Zeitpunkt
    dieses Aufrufs wurde die urspruengliche .exe-Datei des GERADE
    LAUFENDEN Prozesses bereits auf ".exe.vorherige_version_backup"
    umbenannt (Windows erlaubt das Umbenennen einer laufenden .exe, siehe
    Kommentar dort). Ohne Gegenmassnahme wuerde subprocess.Popen() die
    komplette aktuelle Prozessumgebung an die neue .exe vererben -
    einschliesslich PyInstallers privater _PYI_*-Variablen. Der neue
    Prozess wuerde sich dadurch faelschlich fuer einen "Worker-Subprozess
    derselben Instanz" halten (PyInstaller-Onefile-Konvention: gleiche
    Umgebung = gleiche laufende Instanz) und beim Start seine eingebaute
    Sicherheitspruefung ausloesen ("Security validation failure: parent
    process has different executable!", PyInstaller >= 6.22.1) - weil der
    Pfad des Elternprozesses (jetzt der umbenannte Backup-Dateiname) nicht
    mehr mit dem eigenen Pfad uebereinstimmt. Offizieller Mechanismus
    dagegen: PYINSTALLER_RESET_ENVIRONMENT=1 setzen, das weist den
    Bootloader an, alle privaten PyInstaller-Variablen zu verwerfen und
    den neuen Prozess als eigenstaendige, neue Instanz zu behandeln (siehe
    PyInstaller-Doku, Abschnitt "Environment Variables Used by Frozen
    Applications"). Siehe auch Windows_Testprotokoll.md."""
    import subprocess

    exe_path = current_exe_path()
    env = os.environ.copy()
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent), env=env)
    sys.exit(0)
