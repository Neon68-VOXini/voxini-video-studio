"""Qt-backed tests for CharacterPanel. Requires QT_QPA_PLATFORM=offscreen
and the EGL/GLX/xkbcommon shared libs to be resolvable via LD_LIBRARY_PATH
in this sandbox - see README for the exact env vars used in CI/dev."""
import shutil
import tempfile

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from voxini_studio.core.project_manager import ProjectManager  # noqa: E402
from voxini_studio.models.project import Scene  # noqa: E402
from voxini_studio.ui.character_panel import CharacterPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp(prefix="voxini_charpanel_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_image(tmp_path):
    img = tmp_path / "emma_ref.png"
    img.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
            "de0000000a49444154789c6360000002000100ffff03000006000557bf"
            "abd40000000049454e44ae426082"
        )
    )
    return img


def test_add_edit_and_delete_character(qapp, project_dir, sample_image):
    pm = ProjectManager()
    pm.create_new(project_dir, "Test")
    panel = CharacterPanel(pm)

    panel._add_character()
    assert len(pm.project.characters) == 1
    char_id = list(pm.project.characters.keys())[0]

    panel.name_edit.setText("Emma")
    panel.desc_edit.setPlainText("25, dark-brown hair, green eyes")
    panel._save()
    assert pm.project.characters[char_id].name == "Emma"
    assert "green eyes" in pm.project.characters[char_id].description

    panel._selected_id = char_id
    panel.pm.import_character_reference(char_id, str(sample_image))
    panel._on_select_reload = None  # no-op, just re-trigger via refresh
    panel.refresh()
    for i in range(panel.char_list.count()):
        if panel.char_list.item(i).data(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.UserRole) == char_id:
            panel.char_list.setCurrentRow(i)
    assert len(pm.project.characters[char_id].reference_image_paths) == 1

    # a scene referencing this character should lose the reference on delete
    pm.project.scenes.append(Scene(order=0, character_ids=[char_id]))
    panel._selected_id = char_id
    panel._delete_character_confirmed_for_test = True

    # bypass the QMessageBox confirmation dialog for the headless test
    from PySide6.QtWidgets import QMessageBox

    original = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.Yes)
    try:
        panel._delete_character()
    finally:
        QMessageBox.question = original

    assert char_id not in pm.project.characters
    assert pm.project.scenes[0].character_ids == []


def test_remove_single_reference_image(qapp, project_dir, sample_image, monkeypatch):
    pm = ProjectManager()
    pm.create_new(project_dir, "Test")
    panel = CharacterPanel(pm)

    panel._add_character()
    char_id = list(pm.project.characters.keys())[0]
    panel._selected_id = char_id
    pm.import_character_reference(char_id, str(sample_image))
    panel._on_select()
    assert panel.ref_list.count() == 1

    panel.ref_list.setCurrentRow(0)
    panel._remove_reference()

    assert pm.project.characters[char_id].reference_image_paths == []
    assert panel.ref_list.count() == 0


def test_remove_reference_without_selection_shows_message(qapp, project_dir, monkeypatch):
    pm = ProjectManager()
    pm.create_new(project_dir, "Test")
    panel = CharacterPanel(pm)
    panel._add_character()
    panel._selected_id = list(pm.project.characters.keys())[0]

    from PySide6.QtWidgets import QMessageBox

    shown = {}
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.__setitem__("ok", True)))
    panel._remove_reference()
    assert shown.get("ok") is True


def test_refresh_with_no_project_clears_detail(qapp):
    pm = ProjectManager()
    panel = CharacterPanel(pm)
    panel.refresh()
    assert panel.char_list.count() == 0
    assert panel.name_edit.text() == ""
