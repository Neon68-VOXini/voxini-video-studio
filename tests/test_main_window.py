"""Smoke test for the full MainWindow shell: construct it with a small
populated project (scenes, a character, a hybrid provider mix) offscreen,
make sure nothing crashes, the VOXini theme/logo/icons load, the theme
switcher actually re-themes the running QApplication, and the generation
table shows the per-scene provider/cost-label columns introduced by the
hybrid UI work. Requires QT_QPA_PLATFORM=offscreen - see README."""
import shutil
import tempfile

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from voxini_studio.models.project import Scene  # noqa: E402
from voxini_studio.ui import theme  # noqa: E402
from voxini_studio.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.build_stylesheet("voxini"))
    yield app


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp(prefix="voxini_mainwin_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_main_window_constructs_without_error(qapp, project_dir):
    win = MainWindow()
    win.pm.create_new(project_dir, "Smoke Test Projekt")
    win.pm.project.scenes.append(
        Scene(order=0, label="INTRO", start_seconds=0.0, end_seconds=3.0, prompt_text="a calm intro shot")
    )
    win.pm.project.scenes.append(
        Scene(
            order=1, label="HARD SHOT", start_seconds=3.0, end_seconds=6.0,
            prompt_text="a complex action shot", provider_override="runway",
        )
    )
    win.project_panel.refresh()

    assert win.windowTitle() == "VOXini Video Studio"
    assert win.tabs.count() == 5
    assert win.project_panel.project_label.text() == "Smoke Test Projekt"

    # generation table reflects both the local default and the per-scene
    # Runway override, each with a clear free/paid badge
    gen_table = win.generation_panel.table
    assert gen_table.rowCount() == 2
    badges = [gen_table.item(r, 5).text() for r in range(gen_table.rowCount())]
    assert "Lokal - kostenlos" in badges
    assert "Runway - kostenpflichtig" in badges


def test_autosave_timer_is_running_and_ticks_write_snapshot(qapp, project_dir):
    win = MainWindow()
    win.pm.create_new(project_dir, "Autosave Test")
    assert win._autosave_timer.isActive()

    win.pm.project.name = "Changed In Memory"
    win._autosave_tick()
    assert win.pm.paths.autosave_file.exists()
    assert win.pm.has_pending_recovery() is True


def test_close_event_triggers_final_autosave(qapp, project_dir):
    win = MainWindow()
    win.pm.create_new(project_dir, "Close Test")
    win.pm.project.name = "Unsaved At Close"

    win.close()  # triggers closeEvent -> autosave

    assert win.pm.paths.autosave_file.exists()


def test_theme_switcher_changes_app_stylesheet_live(qapp, project_dir):
    win = MainWindow()
    win.pm.create_new(project_dir, "Theme Test")

    idx = win.theme_combo.findData("midnight")
    assert idx >= 0
    win.theme_combo.setCurrentIndex(idx)

    assert "#44a8ff" in qapp.styleSheet()  # midnight's accent color took effect

    # restore default so later tests in the same process see the standard theme
    idx_default = win.theme_combo.findData("voxini")
    win.theme_combo.setCurrentIndex(idx_default)
    theme.save_theme_preference("voxini")


def test_setup_wizard_button_opens_dialog(qapp, project_dir, monkeypatch):
    win = MainWindow()
    win.pm.create_new(project_dir, "Wizard Button Test")

    from voxini_studio.ui import setup_wizard as setup_wizard_module

    opened = {}

    class FakeDialog:
        Accepted = 1

        def __init__(self, parent, pm):
            opened["called"] = True

        def exec(self):
            return 0  # Rejected - don't trigger _on_project_changed side effects

    monkeypatch.setattr(setup_wizard_module, "SetupWizardDialog", FakeDialog)
    monkeypatch.setattr("voxini_studio.ui.main_window.SetupWizardDialog", FakeDialog)
    win._open_setup_wizard()
    assert opened.get("called") is True


def test_maintenance_button_opens_dialog(qapp, project_dir, monkeypatch):
    win = MainWindow()
    win.pm.create_new(project_dir, "Maintenance Button Test")

    opened = {}

    class FakeDialog:
        def __init__(self, parent, pm):
            opened["called"] = True

        def exec(self):
            return 0

    monkeypatch.setattr("voxini_studio.ui.main_window.MaintenanceDialog", FakeDialog)
    win._open_maintenance_dialog()
    assert opened.get("called") is True


def test_logo_svg_loads(qapp, project_dir):
    win = MainWindow()
    assert win.logo.renderer().isValid()


def test_screenshot_for_manual_visual_check(qapp, project_dir, tmp_path):
    """Not an assertion-heavy test - renders the populated main window
    offscreen to a PNG so a human (or a follow-up review pass) can visually
    confirm the VOXini theme actually looks right, the same technique used
    throughout earlier phases of this project."""
    win = MainWindow()
    win.pm.create_new(project_dir, "Screenshot Projekt")
    win.pm.project.scenes.append(
        Scene(order=0, label="INTRO", start_seconds=0.0, end_seconds=3.0, prompt_text="intro")
    )
    win.project_panel.refresh()
    win.resize(1200, 760)
    win.show()
    QApplication.processEvents()

    out_dir = tmp_path
    out_path = out_dir / "main_window_themed.png"
    pixmap = win.grab()
    pixmap.save(str(out_path))
    assert out_path.exists() and out_path.stat().st_size > 0

    # also drop a copy in the dev sandbox's shared scratch dir so it can be
    # visually inspected after the test run (not asserted on - best effort)
    try:
        import shutil as _shutil
        from pathlib import Path as _Path

        dest_dir = _Path("/tmp/qt_check")
        dest_dir.mkdir(parents=True, exist_ok=True)
        _shutil.copyfile(out_path, dest_dir / "main_window_themed.png")
    except Exception:
        pass
