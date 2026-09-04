"""Tests fuer voxini_studio.core.app_update - insbesondere Regressionsschutz
fuer den Bugfix aus Version 1.0.4: relaunch_and_exit() muss
PYINSTALLER_RESET_ENVIRONMENT=1 an den neu gestarteten Prozess uebergeben,
sonst schlaegt PyInstallers eingebaute Sicherheitspruefung (>= 6.22.1) mit
"Security validation failure: parent process has different executable!"
fehl, weil install_downloaded_update() die .exe-Datei des noch laufenden
Prozesses vorher auf ".exe.vorherige_version_backup" umbenennt (siehe
Kommentar in app_update.py:relaunch_and_exit())."""
from __future__ import annotations

import sys

import pytest

from voxini_studio.core import app_update


@pytest.fixture
def frozen_exe(tmp_path, monkeypatch):
    """Simuliert eine gebaute .exe: sys.frozen=True, sys.executable zeigt auf
    eine echte (leere) Datei in tmp_path, damit os.rename() real funktioniert."""
    exe_path = tmp_path / "VOXini Video Studio.exe"
    exe_path.write_bytes(b"dummy-old-version")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_path))
    return exe_path


def test_is_running_as_frozen_exe_false_in_dev(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert app_update.is_running_as_frozen_exe() is False


def test_is_running_as_frozen_exe_true_when_frozen(frozen_exe):
    assert app_update.is_running_as_frozen_exe() is True


def test_current_exe_path_raises_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    with pytest.raises(app_update.UpdateInstallError):
        app_update.current_exe_path()


def test_backup_path_for():
    from pathlib import Path

    exe = Path("D:/Studio Neon68/VOXini Video Studio/VOXini Video Studio.exe")
    backup = app_update.backup_path_for(exe)
    assert backup.name == "VOXini Video Studio.exe.vorherige_version_backup"


def test_install_downloaded_update_swaps_files(frozen_exe):
    exe_path = frozen_exe
    downloaded = exe_path.with_name(exe_path.name + ".update_download")
    downloaded.write_bytes(b"dummy-new-version")

    app_update.install_downloaded_update(downloaded)

    backup = app_update.backup_path_for(exe_path)
    assert exe_path.read_bytes() == b"dummy-new-version"
    assert backup.read_bytes() == b"dummy-old-version"
    assert not downloaded.exists()


def test_install_downloaded_update_replaces_existing_backup(frozen_exe):
    exe_path = frozen_exe
    backup = app_update.backup_path_for(exe_path)
    backup.write_bytes(b"ancient-version")  # simuliert Backup von einem frueheren Update

    downloaded = exe_path.with_name(exe_path.name + ".update_download")
    downloaded.write_bytes(b"dummy-new-version")

    app_update.install_downloaded_update(downloaded)

    assert backup.read_bytes() == b"dummy-old-version"  # nicht mehr "ancient-version"


def test_install_downloaded_update_restores_on_second_rename_failure(frozen_exe, monkeypatch):
    exe_path = frozen_exe
    downloaded = exe_path.with_name(exe_path.name + ".update_download")
    downloaded.write_bytes(b"dummy-new-version")

    real_rename = __import__("os").rename
    calls = []

    def flaky_rename(src, dst):
        calls.append((str(src), str(dst)))
        if len(calls) == 2:
            raise OSError("simulated failure on second rename")
        return real_rename(src, dst)

    monkeypatch.setattr(app_update.os, "rename", flaky_rename)

    with pytest.raises(OSError):
        app_update.install_downloaded_update(downloaded)

    # Rollback muss gegriffen haben: die urspruengliche .exe liegt wieder am
    # richtigen Ort mit dem alten Inhalt, kein Nutzer ohne startfaehige .exe.
    assert exe_path.read_bytes() == b"dummy-old-version"


def test_can_revert_to_previous_version_false_without_backup(frozen_exe):
    assert app_update.can_revert_to_previous_version() is False


def test_can_revert_to_previous_version_true_with_backup(frozen_exe):
    app_update.backup_path_for(frozen_exe).write_bytes(b"old")
    assert app_update.can_revert_to_previous_version() is True


def test_can_revert_to_previous_version_false_in_dev(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert app_update.can_revert_to_previous_version() is False


def test_revert_to_previous_version_swaps_back(frozen_exe):
    exe_path = frozen_exe
    backup = app_update.backup_path_for(exe_path)
    backup.write_bytes(b"dummy-old-version")
    exe_path.write_bytes(b"dummy-new-broken-version")

    app_update.revert_to_previous_version()

    assert exe_path.read_bytes() == b"dummy-old-version"
    assert not backup.exists()


def test_revert_to_previous_version_raises_without_backup(frozen_exe):
    with pytest.raises(app_update.UpdateInstallError):
        app_update.revert_to_previous_version()


# -- Regressionstest fuer den Sicherheitsvalidierungs-Bugfix (v1.0.4) -------


def test_relaunch_and_exit_sets_pyinstaller_reset_environment(frozen_exe, monkeypatch):
    """Kernregressionstest: ohne PYINSTALLER_RESET_ENVIRONMENT=1 im env-
    Kwarg des Kindprozesses haelt sich die neu gestartete .exe faelschlich
    fuer einen Worker-Subprozess der (nach dem Umbenennen auf
    .vorherige_version_backup nicht mehr passenden) alten Instanz und
    verweigert mit "Security validation failure" den Start."""
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
    assert captured["args"] == [str(frozen_exe)]
    assert exit_calls == [0]


def test_relaunch_and_exit_does_not_mutate_real_process_environment(frozen_exe, monkeypatch):
    """env=os.environ.copy() statt os.environ direkt - der echte Prozess der
    (noch laufenden, alten) .exe darf durch das Setzen der Variable fuer den
    KINDPROZESS nicht selbst beeinflusst werden."""
    import os
    import subprocess as subprocess_module

    assert "PYINSTALLER_RESET_ENVIRONMENT" not in os.environ

    monkeypatch.setattr(subprocess_module, "Popen", lambda *a, **kw: None)
    monkeypatch.setattr(sys, "exit", lambda code=0: None)

    app_update.relaunch_and_exit()

    assert "PYINSTALLER_RESET_ENVIRONMENT" not in os.environ
