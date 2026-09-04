"""Qt-backed tests for MaintenanceDialog (Backups + Fehlerprotokoll pages).
Requires QT_QPA_PLATFORM=offscreen - see README."""
import shutil
import tempfile

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from voxini_studio.core import error_log  # noqa: E402
from voxini_studio.core.project_manager import ProjectManager  # noqa: E402
from voxini_studio.ui.maintenance_dialog import BackupsPage, ErrorLogPage, MaintenanceDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp(prefix="voxini_maint_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def pm(project_dir):
    manager = ProjectManager()
    manager.create_new(project_dir, "First Name")
    return manager


# -- BackupsPage -----------------------------------------------------------

def test_backups_page_shows_placeholder_when_empty(qapp, pm):
    page = BackupsPage(pm)
    assert page.list.count() == 1
    assert "keine Backups" in page.list.item(0).text()


def test_backups_page_lists_backups_after_multiple_saves(qapp, pm):
    pm.project.name = "Second Name"
    pm.save()
    pm.project.name = "Third Name"
    pm.save()

    page = BackupsPage(pm)
    assert page.list.count() >= 1


def test_backups_page_restore_replaces_project_and_saves(qapp, pm, monkeypatch):
    pm.project.name = "Second Name"
    pm.save()  # backs up "First Name" version
    pm.project.name = "Third Name"
    pm.save()

    page = BackupsPage(pm)
    # select the oldest backup (contains "First Name")
    page.list.setCurrentRow(page.list.count() - 1)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    page._restore_selected()

    assert pm.project.name == "First Name"

    reopened = ProjectManager()
    reopened.open(pm.paths.root)
    assert reopened.project.name == "First Name"


def test_backups_page_restore_without_selection_shows_message(qapp, pm, monkeypatch):
    page = BackupsPage(pm)
    shown = {}
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.__setitem__("ok", True)))
    page.list.clearSelection()
    page._restore_selected()
    assert shown.get("ok") is True


# -- ErrorLogPage -----------------------------------------------------------

def test_error_log_page_shows_placeholder_when_no_log(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    error_log.reset_for_tests()
    page = ErrorLogPage()
    assert "Noch keine" in page.log_view.toPlainText()
    error_log.reset_for_tests()


def test_error_log_page_shows_log_content(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    error_log.reset_for_tests()
    error_log.setup_error_logging()
    error_log.log_error("Ein Testfehler ist aufgetreten")

    page = ErrorLogPage()
    assert "Ein Testfehler ist aufgetreten" in page.log_view.toPlainText()
    error_log.reset_for_tests()


# -- MaintenanceDialog -------------------------------------------------------

def test_maintenance_dialog_constructs_with_both_tabs(qapp, pm):
    dialog = MaintenanceDialog(None, pm)
    assert dialog.findChild(BackupsPage) is not None
    assert dialog.findChild(ErrorLogPage) is not None
