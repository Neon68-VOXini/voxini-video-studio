"""Qt-backed tests for StoryboardView, including the scene-delete flow.
Requires QT_QPA_PLATFORM=offscreen - see README."""
import shutil
import tempfile

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from voxini_studio.core.project_manager import ProjectManager  # noqa: E402
from voxini_studio.models.project import Scene  # noqa: E402
from voxini_studio.ui.storyboard_view import StoryboardView  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp(prefix="voxini_storyboard_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def pm(project_dir):
    manager = ProjectManager()
    manager.create_new(project_dir, "Storyboard Test")
    manager.project.scenes.append(Scene(order=0, label="A", start_seconds=0.0, end_seconds=2.0, prompt_text="scene a"))
    manager.project.scenes.append(Scene(order=1, label="B", start_seconds=2.0, end_seconds=4.0, prompt_text="scene b"))
    return manager


def test_refresh_populates_table(qapp, pm):
    view = StoryboardView(pm)
    assert view.table.rowCount() == 2


def test_delete_selected_scene_removes_it(qapp, pm, monkeypatch):
    view = StoryboardView(pm)
    view.table.selectRow(0)
    assert view._selected_index == 0

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    view._delete_selected_scene()

    assert len(pm.project.scenes) == 1
    assert pm.project.scenes[0].label == "B"
    assert view.table.rowCount() == 1


def test_delete_selected_scene_declined_keeps_it(qapp, pm, monkeypatch):
    view = StoryboardView(pm)
    view.table.selectRow(0)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    view._delete_selected_scene()

    assert len(pm.project.scenes) == 2


def test_delete_scene_without_selection_is_noop(qapp, pm):
    view = StoryboardView(pm)
    view._selected_index = None
    view._delete_selected_scene()
    assert len(pm.project.scenes) == 2
