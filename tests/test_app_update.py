"""Tests fuer voxini_studio.core.app_update - insbesondere Regressionsschutz
fuer:
  - den Bugfix aus Version 1.0.4: relaunch_and_exit() muss
    PYINSTALLER_RESET_ENVIRONMENT=1 an den neu gestarteten Prozess
    uebergeben, sonst schlaegt PyInstallers eingebaute Sicherheitspruefung
    (seit 6.10.0) mit "Security validation failure: parent process has
    different executable!" fehl.
  - die Onedir-Migration aus Version 1.1.0: install_downloaded_update()/
    revert_to_previous_version() tauschen seitdem einen kompletten
    Installationsordner aus (siehe current_app_dir()), keine einzelne
    .exe-Datei mehr - siehe Kommentar in app_update.py."""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import pytest

from voxini_studio.core import app_update


@pytest.fixture
def frozen_app(tmp_path, monkeypatch):
    """Simuliert eine gebaute Onedir-Installation: sys.frozen=True,
    sys.executable zeigt auf eine echte .exe-Datei innerhalb eines
    Anwendungsordners mit einem "_internal"-Unterordner (wie ein echter
    PyInstaller-Onedir-Build), damit os.rename()/shutil auf echten
    Verzeichnissen arbeiten koennen."""
    app_dir = tmp_path / "VOXini Video Studio"
    app_dir.mkdir()
    exe_path = app_dir / "VOXini Video Studio.exe"
    exe_path.write_bytes(b"dummy-old-version")
    internal = app_dir / "_internal"
    internal.mkdir()
    (internal / "marker.txt").write_bytes(b"old-internal-data")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_path))
    return app_dir


def _make_update_zip(
    zip_path: Path,
    exe_content: bytes = b"dummy-new-version",
    internal_content: bytes = b"new-internal-data",
    include_exe: bool = True,
) -> None:
    """Baut ein Test-ZIP mit derselben Struktur wie das echte Release-ZIP
    (siehe release.yml: Compress-Archive -Path "VOXini Video Studio\\*") -
    die Dateien liegen direkt im ZIP-Wurzelverzeichnis, kein umschliessender
    Ordnername im Archiv."""
    with zipfile.ZipFile(zip_path, "w") as zf:
        if include_exe:
            zf.writestr("VOXini Video Studio.exe", exe_content)
        zf.writestr("_internal/marker.txt", internal_content)


def test_is_running_as_frozen_exe_false_in_dev(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert app_update.is_running_as_frozen_exe() is False


def test_is_running_as_frozen_exe_true_when_frozen(frozen_app):
    assert app_update.is_running_as_frozen_exe() is True


def test_current_exe_path_raises_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    with pytest.raises(app_update.UpdateInstallError):
        app_update.current_exe_path()


def test_current_app_dir_returns_exe_parent(frozen_app):
    assert app_update.current_app_dir() == frozen_app


def test_backup_path_for():
    app_dir = Path("D:/Studio Neon68/VOXini Video Studio/VOXini Video Studio")
    backup = app_update.backup_path_for(app_dir)
    assert backup.name == "VOXini Video Studio.vorherige_version_backup"


def test_install_downloaded_update_swaps_folder(frozen_app):
    app_dir = frozen_app
    downloaded = app_dir.with_name(app_dir.name + ".update_download.zip")
    _make_update_zip(downloaded)

    app_update.install_downloaded_update(downloaded)

    backup = app_update.backup_path_for(app_dir)
    assert (app_dir / "VOXini Video Studio.exe").read_bytes() == b"dummy-new-version"
    assert (app_dir / "_internal" / "marker.txt").read_bytes() == b"new-internal-data"
    assert (backup / "VOXini Video Studio.exe").read_bytes() == b"dummy-old-version"
    assert (backup / "_internal" / "marker.txt").read_bytes() == b"old-internal-data"
    assert not downloaded.exists()


def test_install_downloaded_update_replaces_existing_backup(frozen_app):
    app_dir = frozen_app
    backup = app_update.backup_path_for(app_dir)
    backup.mkdir()
    (backup / "ancient.txt").write_bytes(b"ancient-version")  # simuliert Backup von einem frueheren Update

    downloaded = app_dir.with_name(app_dir.name + ".update_download.zip")
    _make_update_zip(downloaded)

    app_update.install_downloaded_update(downloaded)

    assert not (backup / "ancient.txt").exists()  # altes Backup wurde ersetzt, nicht zusammengefuehrt
    assert (backup / "VOXini Video Studio.exe").read_bytes() == b"dummy-old-version"


def test_install_downloaded_update_rejects_zip_without_exe(frozen_app):
    """Sicherheitspruefung MUSS vor dem Umbenennen des echten
    Installationsordners passieren - ein kaputtes/unvollstaendiges Archiv
    darf die laufende Installation nicht antasten."""
    app_dir = frozen_app
    downloaded = app_dir.with_name(app_dir.name + ".update_download.zip")
    _make_update_zip(downloaded, include_exe=False)

    with pytest.raises(app_update.UpdateInstallError):
        app_update.install_downloaded_update(downloaded)

    assert (app_dir / "VOXini Video Studio.exe").read_bytes() == b"dummy-old-version"
    assert not app_update.backup_path_for(app_dir).exists()


def test_install_downloaded_update_restores_on_second_rename_failure(frozen_app, monkeypatch):
    app_dir = frozen_app
    downloaded = app_dir.with_name(app_dir.name + ".update_download.zip")
    _make_update_zip(downloaded)

    real_rename = os.rename
    calls = []

    def flaky_rename(src, dst):
        calls.append((str(src), str(dst)))
        if len(calls) == 2:
            raise OSError("simulated failure on second rename")
        return real_rename(src, dst)

    monkeypatch.setattr(app_update.os, "rename", flaky_rename)

    with pytest.raises(OSError):
        app_update.install_downloaded_update(downloaded)

    # Rollback muss gegriffen haben: die urspruengliche Installation liegt
    # wieder am richtigen Ort mit dem alten Inhalt, kein Nutzer ohne
    # startfaehige Installation.
    assert (app_dir / "VOXini Video Studio.exe").read_bytes() == b"dummy-old-version"


def test_can_revert_to_previous_version_false_without_backup(frozen_app):
    assert app_update.can_revert_to_previous_version() is False


def test_can_revert_to_previous_version_true_with_backup(frozen_app):
    app_update.backup_path_for(frozen_app).mkdir()
    assert app_update.can_revert_to_previous_version() is True


def test_can_revert_to_previous_version_false_in_dev(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert app_update.can_revert_to_previous_version() is False


def test_revert_to_previous_version_swaps_back(frozen_app):
    app_dir = frozen_app
    backup = app_update.backup_path_for(app_dir)
    backup.mkdir()
    (backup / "VOXini Video Studio.exe").write_bytes(b"dummy-old-version")

    (app_dir / "VOXini Video Studio.exe").write_bytes(b"dummy-new-broken-version")

    app_update.revert_to_previous_version()

    assert (app_dir / "VOXini Video Studio.exe").read_bytes() == b"dummy-old-version"
    assert not backup.exists()


def test_revert_to_previous_version_raises_without_backup(frozen_app):
    with pytest.raises(app_update.UpdateInstallError):
        app_update.revert_to_previous_version()


# -- Regressionstest fuer den Sicherheitsvalidierungs-Bugfix (v1.0.4) -------


def test_relaunch_and_exit_sets_pyinstaller_reset_environment(frozen_app, monkeypatch):
    """Kernregressionstest: ohne PYINSTALLER_RESET_ENVIRONMENT=1 im env-
    Kwarg des Kindprozesses haelt sich die neu gestartete .exe faelschlich
    fuer einen Worker-Subprozess der (nach dem Umbenennen des
    Installationsordners auf .vorherige_version_backup nicht mehr
    passenden) alten Instanz und verweigert mit "Security validation
    failure" den Start."""
    exe_path = frozen_app / "VOXini Video Studio.exe"
    captured = {}

    class _FakePopen:
        def __init__(self, args, cwd=None, env=None):
            captured["args"] = args
            captured["cwd"] = cwd
            captured["env"] = env

    # app_update.relaunch_and_exit() importiert subprocess lokal innerhalb
    # der Funktion ("import subprocess") - das patcht dasselbe Modulobjekt,
    # das dieser lokale Import zurueckgibt (Python cached Module in
    # sys.modules), also greift dieses Patch zuverlaessig.
    import subprocess as subprocess_module
    monkeypatch.setattr(subprocess_module, "Popen", _FakePopen)

    exit_calls = []
    monkeypatch.setattr(sys, "exit", lambda code=0: exit_calls.append(code))

    app_update.relaunch_and_exit()

    assert captured["env"] is not None
    assert captured["env"].get("PYINSTALLER_RESET_ENVIRONMENT") == "1"
    assert captured["args"] == [str(exe_path)]
    assert exit_calls == [0]


def test_relaunch_and_exit_does_not_mutate_real_process_environment(frozen_app, monkeypatch):
    """env=os.environ.copy() statt os.environ direkt - der echte Prozess der
    (noch laufenden, alten) .exe darf durch das Setzen der Variable fuer den
    KINDPROZESS nicht selbst beeinflusst werden."""
    import subprocess as subprocess_module

    assert "PYINSTALLER_RESET_ENVIRONMENT" not in os.environ

    monkeypatch.setattr(subprocess_module, "Popen", lambda *a, **kw: None)
    monkeypatch.setattr(sys, "exit", lambda code=0: None)

    app_update.relaunch_and_exit()

    assert "PYINSTALLER_RESET_ENVIRONMENT" not in os.environ
