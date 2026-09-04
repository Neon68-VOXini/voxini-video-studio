import shutil
import tempfile
import time

import pytest

from voxini_studio.core import autosave
from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.models.project import Project, ProjectPaths


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp(prefix="voxini_autosave_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def paths(project_dir):
    p = ProjectPaths(project_dir)
    p.ensure_layout()
    return p


# -- write_autosave / recovery detection -------------------------------------

def test_write_autosave_creates_file(paths):
    project = Project(name="Test")
    path = autosave.write_autosave(project, paths)
    assert path.exists()
    assert path.name == "project.autosave.voxproj"


def test_has_pending_recovery_false_when_no_autosave(paths):
    assert autosave.has_pending_recovery(paths) is False


def test_has_pending_recovery_true_when_autosave_newer_than_manual_save(paths):
    project = Project(name="Test")
    paths.project_file.write_text(project.model_dump_json(), encoding="utf-8")
    time.sleep(0.05)
    autosave.write_autosave(project, paths)
    assert autosave.has_pending_recovery(paths) is True


def test_has_pending_recovery_false_when_manual_save_is_newer(paths):
    project = Project(name="Test")
    autosave.write_autosave(project, paths)
    time.sleep(0.01)
    paths.project_file.write_text(project.model_dump_json(), encoding="utf-8")
    autosave.mark_manual_save(paths)
    assert autosave.has_pending_recovery(paths) is False


def test_has_pending_recovery_true_when_manual_file_missing(paths):
    project = Project(name="Test")
    autosave.write_autosave(project, paths)
    assert not paths.project_file.exists()
    assert autosave.has_pending_recovery(paths) is True


def test_load_autosave_roundtrips_project_data(paths):
    project = Project(name="Recovered Project")
    autosave.write_autosave(project, paths)
    loaded = autosave.load_autosave(paths)
    assert loaded is not None
    assert loaded.name == "Recovered Project"
    assert loaded.id == project.id


def test_load_autosave_returns_none_when_missing(paths):
    assert autosave.load_autosave(paths) is None


def test_discard_autosave_removes_file(paths):
    project = Project(name="Test")
    autosave.write_autosave(project, paths)
    autosave.discard_autosave(paths)
    assert not paths.autosave_file.exists()
    autosave.discard_autosave(paths)  # idempotent, no error


# -- backups -----------------------------------------------------------------

def test_make_backup_returns_none_when_nothing_on_disk(paths):
    assert autosave.make_backup(paths) is None


def test_make_backup_copies_existing_file(paths):
    project = Project(name="Test")
    paths.project_file.write_text(project.model_dump_json(), encoding="utf-8")
    backup_path = autosave.make_backup(paths)
    assert backup_path is not None
    assert backup_path.exists()
    assert backup_path.read_text(encoding="utf-8") == paths.project_file.read_text(encoding="utf-8")


def test_make_backup_prunes_old_backups_beyond_max(paths, monkeypatch):
    monkeypatch.setattr(autosave, "MAX_BACKUPS", 3)
    project = Project(name="Test")
    for i in range(6):
        paths.project_file.write_text(project.model_dump_json(), encoding="utf-8")
        autosave.make_backup(paths)
        time.sleep(0.01)
    backups = autosave.list_backups(paths)
    assert len(backups) == 3


def test_list_backups_newest_first(paths):
    project = Project(name="Test")
    for i in range(3):
        paths.project_file.write_text(project.model_dump_json(), encoding="utf-8")
        autosave.make_backup(paths)
        time.sleep(0.01)
    backups = autosave.list_backups(paths)
    mtimes = [b.stat().st_mtime for b in backups]
    assert mtimes == sorted(mtimes, reverse=True)


# -- ProjectManager integration -----------------------------------------------

def test_project_manager_save_creates_backup_of_previous_version(project_dir):
    pm = ProjectManager()
    pm.create_new(project_dir, "First")
    pm.project.name = "Second"
    pm.save()
    backups = pm.list_backups()
    assert len(backups) >= 1


def test_project_manager_save_discards_pending_autosave(project_dir):
    pm = ProjectManager()
    pm.create_new(project_dir, "Test")
    pm.project.name = "Changed"
    pm.autosave()
    assert pm.paths.autosave_file.exists()
    pm.save()
    assert not pm.paths.autosave_file.exists()


def test_project_manager_autosave_noop_without_open_project():
    pm = ProjectManager()
    assert pm.autosave() is None
    assert pm.has_pending_recovery() is False


def test_project_manager_recover_from_autosave_replaces_in_memory_project(project_dir):
    pm = ProjectManager()
    pm.create_new(project_dir, "Original")
    pm.project.name = "Unsaved Change"
    pm.autosave()

    # simulate reopening after a crash: fresh manager, load the manually
    # saved (still "Original") file, then recover the autosaved change
    pm2 = ProjectManager()
    pm2.open(project_dir)
    assert pm2.project.name == "Original"
    assert pm2.has_pending_recovery() is True

    pm2.recover_from_autosave()
    assert pm2.project.name == "Unsaved Change"

    pm2.save()
    assert not pm2.has_pending_recovery()

    pm3 = ProjectManager()
    pm3.open(project_dir)
    assert pm3.project.name == "Unsaved Change"


def test_project_manager_discard_recovery_keeps_manual_save(project_dir):
    pm = ProjectManager()
    pm.create_new(project_dir, "Original")
    pm.project.name = "Unsaved Change"
    pm.autosave()

    pm2 = ProjectManager()
    pm2.open(project_dir)
    assert pm2.has_pending_recovery() is True
    pm2.discard_recovery()
    assert pm2.has_pending_recovery() is False
    assert pm2.project.name == "Original"  # in-memory project untouched by discard
