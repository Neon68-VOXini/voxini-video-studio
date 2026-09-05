"""Fuehrt den eigentlichen Update-Austausch der laufenden Installation
durch - erst NACH expliziter Bestaetigung durch den Nutzer im Update-Dialog
(siehe update_dialog.py). Sichert die bisherige Installation immer zuerst
als "<Ordnername>.vorherige_version_backup" (gleiche Namenskonvention wie
beim Schwesterprojekt VOXini Studio), sodass ueber
revert_to_previous_version() jederzeit ein Rueckschritt moeglich ist.
Funktioniert nur fuer die tatsaechlich gebaute/gepackte .exe (sys.frozen) -
im Python-Entwicklungsbetrieb (python -m voxini_studio, kein
PyInstaller-Build) gibt es keine Installation zum Ersetzen, siehe
is_running_as_frozen_exe().

WECHSEL AUF ONEDIR (Version 1.1.0, vorher Onefile): im Onefile-Modus
entpackte sich die .exe bei JEDEM Start neu in einen temporaeren Ordner
(sys._MEIPASS unter %TEMP%), was PyInstallers eingebaute
Sicherheitspruefung (siehe relaunch_and_exit() unten) in Kombination mit
Antivirus-Scans der frisch entpackten Datei gelegentlich fehlschlagen
liess - auch bei ganz normalem Doppelklick-Start, ohne jeden Zusammenhang
mit dem Auto-Update selbst (siehe Windows_Testprotokoll.md). Der Umstieg
auf Onedir (voxini_studio.spec) entfernt dieses Selbst-Entpacken bei jedem
Start und behebt damit das Problem strukturell.

Dadurch aendert sich hier: statt einer einzelnen .exe-Datei wird jetzt der
GESAMTE Installationsordner ausgetauscht (der Ordner, der die .exe UND den
"_internal"-Unterordner mit allen Abhaengigkeiten enthaelt - siehe
current_app_dir()). Sicherung/Rueckkehr funktionieren nach demselben
Prinzip wie zuvor bei der Einzeldatei, nur eine Verzeichnisebene hoeher:
der komplette Ordner wird auf "<Ordnername>.vorherige_version_backup"
umbenannt. Windows erlaubt das Umbenennen eines Ordners, waehrend eine
Datei darin gerade laeuft (der laufende Prozess haelt nur Datei-Handles,
keinen Namens-Lock auf den Ordner selbst) - genau dasselbe Prinzip, das
vorher schon fuer die einzelne .exe genutzt wurde."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Optional

from voxini_studio.core.model_downloader import DownloadError, download_file

_BACKUP_SUFFIX = ".vorherige_version_backup"
_DOWNLOAD_SUFFIX = ".update_download.zip"
_STAGING_SUFFIX = ".update_staging"
_EXE_NAME = "VOXini Video Studio.exe"


class UpdateInstallError(RuntimeError):
    pass


def is_running_as_frozen_exe() -> bool:
    """True nur, wenn dies tatsaechlich die PyInstaller-gebaute Anwendung
    ist (nicht der Python-Entwicklungsbetrieb) - Download/Installation/
    Rollback ergeben nur dann Sinn, siehe voxini_studio.spec
    (PyInstaller-Onedir)."""
    return bool(getattr(sys, "frozen", False))


def current_exe_path() -> Path:
    if not is_running_as_frozen_exe():
        raise UpdateInstallError(
            "Kein Update möglich: Dies ist der Python-Entwicklungsbetrieb, keine gebaute .exe."
        )
    return Path(sys.executable)


def current_app_dir() -> Path:
    """Der Ordner, der die laufende .exe UND den "_internal"-Unterordner
    enthaelt (PyInstaller-Onedir-Layout, siehe voxini_studio.spec) - das
    ist die Einheit, die bei einem Update komplett ausgetauscht wird, nicht
    nur die .exe-Datei allein."""
    return current_exe_path().parent


def backup_path_for(app_dir: Path) -> Path:
    return app_dir.with_name(app_dir.name + _BACKUP_SUFFIX)


def _staging_path_for(app_dir: Path) -> Path:
    return app_dir.with_name(app_dir.name + _STAGING_SUFFIX)


def _release_cwd_lock_on(app_dir: Path) -> None:
    """Verlaesst - falls noetig - das aktuelle Arbeitsverzeichnis (cwd) des
    LAUFENDEN Prozesses, wenn es innerhalb von app_dir liegt, BEVOR
    app_dir per os.rename()/shutil.rmtree() angefasst wird.

    HINTERGRUND (Bugfix, urspruenglich falsche Annahme in diesem Modul):
    Windows erlaubt zwar das Umbenennen eines Ordners, waehrend eine Datei
    darin geoeffnet ist (der laufende Prozess haelt dafuer nur
    Datei-Handles, keinen Namens-Lock) - das aktuelle Arbeitsverzeichnis
    des Prozesses selbst ist aber ein SEPARATER, impliziter Lock auf genau
    diesen Ordner. Wird eine .exe per Doppelklick gestartet (kein
    Startmenue-Eintrag mit abweichendem "Ausfuehren in"-Pfad), ist ihr cwd
    standardmaessig der eigene Installationsordner - dadurch schlaegt
    os.rename(app_dir, ...) mit "WinError 32: der Prozess kann nicht auf
    die Datei zugreifen, da sie von einem anderen Prozess verwendet wird"
    fehl, obwohl aus Sicht des Docstrings oben eigentlich alles erlaubt
    sein sollte. Der Fix: kurz vor dem Umbenennen/Loeschen in einen
    neutralen Ordner (System-Temp) wechseln, der garantiert ausserhalb von
    app_dir liegt - das gibt den cwd-Lock frei, ohne dass sich sonst etwas
    am Prozess aendert (direkt danach folgt ohnehin nur noch Neustart/
    Prozessende, siehe update_dialog.py)."""
    try:
        current = Path(os.getcwd())
    except OSError:
        # cwd bereits ungueltig (z.B. vorheriger Ordner existiert nicht
        # mehr) - dann kann er auch keinen Lock mehr halten.
        return
    try:
        is_inside = current == app_dir or app_dir in current.parents
    except OSError:
        is_inside = False
    if is_inside:
        os.chdir(tempfile.gettempdir())


def download_update(
    download_url: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Path:
    """Laedt das neue Release-ZIP NEBEN den laufenden Installationsordner
    herunter (temporaerer Name "<Ordnername>.update_download.zip"), OHNE
    die laufende Installation bereits anzufassen - erst
    install_downloaded_update() tauscht nach erfolgreichem Download
    tatsaechlich um. Nutzt dieselbe .part-Datei-Sicherheit wie
    model_downloader.download_file() (siehe core/model_downloader.py) - ein
    abgebrochener/fehlgeschlagener Download laesst nie eine kaputt
    aussehende fertige Datei zurueck."""
    app_dir = current_app_dir()
    dest = app_dir.with_name(app_dir.name + _DOWNLOAD_SUFFIX)
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


def install_downloaded_update(downloaded_zip: Path) -> None:
    """Tauscht den laufenden Installationsordner gegen den Inhalt des
    heruntergeladenen Release-ZIPs:

      1. ZIP in einen frischen Staging-Ordner neben der Installation
         entpacken (Ordnername + ".update_staging") - noch OHNE die
         laufende Installation anzufassen.
      2. Pruefen, dass im Staging-Ordner tatsaechlich eine
         "VOXini Video Studio.exe" liegt - sonst Abbruch, BEVOR irgendetwas
         Laufendes veraendert wird.
      3. Laufenden Installationsordner -> "<Ordnername>.vorherige_version_
         backup" umbenennen (ein vorhandenes aelteres Backup wird dabei
         ersetzt - bewusst nur EINE Rueckfallstufe, keine
         Versionshistorie).
      4. Staging-Ordner -> urspruenglicher Installationspfad umbenennen.

    WICHTIG: Windows kann den Ordner einer laufenden .exe umbenennen, auch
    waehrend Dateien darin geoeffnet sind (der laufende Prozess haelt dafuer
    nur Datei-Handles, keinen Namens-Lock auf den Ordner selbst) - genau das
    nutzt dieser Tausch aus, wie zuvor schon bei der Einzeldatei im
    Onefile-Modus. ABER: das aktuelle Arbeitsverzeichnis (cwd) des Prozesses
    ist ein SEPARATER Lock, der beim Doppelklick-Start standardmaessig
    genau dieser Ordner ist - siehe _release_cwd_lock_on() oben, das
    deshalb vor dem Umbenennen aufgerufen wird (Bugfix nach echtem
    Fehlschlag "WinError 32" bei Version 1.2.0). Die Anwendung muss danach
    trotzdem neu gestartet werden, damit der naechste Start tatsaechlich
    die neue Version ausfuehrt (siehe update_dialog.py, Neustart-Angebot).

    Schlaegt Schritt 4 fehl, wird Schritt 3 sofort rueckgaengig gemacht,
    statt den Nutzer ohne startfaehige Installation dastehen zu lassen."""
    app_dir = current_app_dir()
    staging = _staging_path_for(app_dir)
    backup = backup_path_for(app_dir)

    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        with zipfile.ZipFile(downloaded_zip) as zf:
            zf.extractall(staging)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    if not (staging / _EXE_NAME).exists():
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateInstallError(
            f"Heruntergeladenes Update ist ungültig: „{_EXE_NAME}“ fehlt im Archiv."
        )

    _release_cwd_lock_on(app_dir)
    if backup.exists():
        shutil.rmtree(backup)
    os.rename(app_dir, backup)
    try:
        os.rename(staging, app_dir)
    except OSError:
        # Umbenennen des neuen Ordners fehlgeschlagen - alten Stand sofort
        # wiederherstellen, statt den Nutzer ohne startfaehige Installation
        # dastehen zu lassen.
        os.rename(backup, app_dir)
        raise
    finally:
        downloaded_zip.unlink(missing_ok=True)


def can_revert_to_previous_version() -> bool:
    if not is_running_as_frozen_exe():
        return False
    return backup_path_for(current_app_dir()).exists()


def revert_to_previous_version() -> None:
    """Macht install_downloaded_update() rueckgaengig: der aktuelle (neue)
    Installationsordner wird durch den ".vorherige_version_backup"-Ordner
    ersetzt. Die verworfene "neue" Version wird geloescht, nicht
    aufgehoben - es gibt weiterhin nur eine einzige Rueckfallstufe. Auch
    hier gilt: die Anwendung muss danach neu gestartet werden."""
    app_dir = current_app_dir()
    backup = backup_path_for(app_dir)
    if not backup.exists():
        raise UpdateInstallError("Keine vorherige Version zum Zurückkehren gefunden.")
    _release_cwd_lock_on(app_dir)
    shutil.rmtree(app_dir)
    os.rename(backup, app_dir)


def relaunch_and_exit() -> None:
    """Startet die (nach einem Update/Rollback jetzt am Zielpfad liegende)
    .exe als neuen, unabhaengigen Prozess und beendet den aktuellen -
    fuer den optionalen "Jetzt neu starten"-Button im Update-Dialog. Nur
    aufrufbar, wenn is_running_as_frozen_exe() True ist.

    WICHTIG (per Design von install_downloaded_update()): zum Zeitpunkt
    dieses Aufrufs wurde der urspruengliche Installationsordner des GERADE
    LAUFENDEN Prozesses bereits auf ".vorherige_version_backup" umbenannt
    (Windows erlaubt das Umbenennen eines Ordners, waehrend eine Datei
    darin laeuft, siehe Kommentar dort). Ohne Gegenmassnahme wuerde
    subprocess.Popen() die komplette aktuelle Prozessumgebung an die neue
    .exe vererben - einschliesslich PyInstallers privater _PYI_*-Variablen.
    Der neue Prozess wuerde sich dadurch faelschlich fuer einen
    "Worker-Subprozess derselben Instanz" halten (PyInstaller-Konvention:
    gleiche Umgebung = gleiche laufende Instanz) und beim Start seine
    eingebaute Sicherheitspruefung ausloesen ("Security validation
    failure: parent process has different executable!", eingefuehrt in
    PyInstaller 6.10.0 - lt. offiziellem CHANGES.rst, nicht wie zunaechst
    fälschlich vermutet erst ab 6.22.1/eine Version 6.22.x existiert gar
    nicht) - weil der Pfad des Elternprozesses (jetzt der umbenannte
    Backup-Ordnername) nicht mehr mit dem eigenen Pfad uebereinstimmt.
    Offizieller Mechanismus dagegen: PYINSTALLER_RESET_ENVIRONMENT=1
    setzen, das weist den Bootloader an, alle privaten
    PyInstaller-Variablen zu verwerfen und den neuen Prozess als
    eigenstaendige, neue Instanz zu behandeln (siehe PyInstaller-Doku,
    Abschnitt "Environment Variables Used by Frozen Applications"). Dieser
    Mechanismus gilt unveraendert auch nach dem Wechsel auf Onedir (Version
    1.1.0) - der Ordner-statt-Datei-Rename loest denselben
    Pfad-Mismatch beim Neustart aus. Siehe auch Windows_Testprotokoll.md."""
    import subprocess

    exe_path = current_exe_path()
    env = os.environ.copy()
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent), env=env)
    sys.exit(0)
