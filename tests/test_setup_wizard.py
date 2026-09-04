"""Tests for the Einrichtungsassistent (setup wizard) dialog: local
ComfyUI settings + environment check, model download confirmation flow
against a real local fake file server, and Runway key/connection-test flow
against a real local fake Runway server. Modal QMessageBox popups are
monkeypatched to auto-answer so the offscreen Qt event loop never blocks
waiting for a human click - this is standard practice for testing Qt
dialogs headlessly. Requires QT_QPA_PLATFORM=offscreen - see README."""
from __future__ import annotations

import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from voxini_studio.core import credentials  # noqa: E402
from voxini_studio.core.project_manager import ProjectManager  # noqa: E402
from voxini_studio.ui import setup_wizard  # noqa: E402

from tests.fakes.fake_runway_server import FakeRunwayServer  # noqa: E402

FILE_BYTES = b"FAKE-MODEL-BYTES" * 1000


class _ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(FILE_BYTES)))
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(FILE_BYTES)))
        self.end_headers()
        self.wfile.write(FILE_BYTES)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp(prefix="voxini_wizard_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def pm(project_dir):
    manager = ProjectManager()
    manager.create_new(project_dir, "Wizard Test")
    return manager


@pytest.fixture
def model_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _ModelHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def runway_server():
    s = FakeRunwayServer(expected_api_key="wizard-test-key")
    s.start()
    yield s
    s.stop()


# -- LocalSetupPage -----------------------------------------------------

def test_local_page_reflects_project_settings(qapp, pm):
    page = setup_wizard.LocalSetupPage(pm)
    assert page.host_edit.text() == pm.project.comfyui_host
    assert page.port_spin.value() == pm.project.comfyui_port


def test_local_page_apply_to_project_writes_host_port(qapp, pm):
    page = setup_wizard.LocalSetupPage(pm)
    page.host_edit.setText("192.168.1.99")
    page.port_spin.setValue(9191)
    page.apply_to_project()
    assert pm.project.comfyui_host == "192.168.1.99"
    assert pm.project.comfyui_port == 9191


def test_local_page_pick_install_dir_sets_project_fields(qapp, pm, tmp_path, monkeypatch):
    page = setup_wizard.LocalSetupPage(pm)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path)))
    page._pick_install_dir()
    assert pm.project.comfyui_install_dir == str(tmp_path)
    assert pm.project.comfyui_models_dir == str(tmp_path / "models")


def test_local_page_run_check_populates_report(qapp, pm):
    page = setup_wizard.LocalSetupPage(pm)
    page._run_check()
    text = page.report.toPlainText()
    assert "GPU erkannt" in text
    assert "Status:" in text


def test_local_page_download_model_asks_confirmation_and_downloads(qapp, pm, tmp_path, model_server, monkeypatch):
    page = setup_wizard.LocalSetupPage(pm)
    pm.project.comfyui_models_dir = str(tmp_path / "models")

    spec, status_label, _btn = page.model_rows[0]
    port = model_server.server_address[1]
    spec = dict(spec)
    spec["url"] = f"http://127.0.0.1:{port}/model.bin"

    asked = {}

    def fake_question(*args, **kwargs):
        asked["called"] = True
        return QMessageBox.Yes

    monkeypatch.setattr(setup_wizard.QMessageBox, "question", staticmethod(fake_question))
    monkeypatch.setattr(setup_wizard.QMessageBox, "information", staticmethod(lambda *a, **k: None))

    page._download_model(spec, status_label)

    assert asked.get("called") is True
    dest = tmp_path / "models" / spec["subdir"] / spec["filename"]
    assert dest.exists()
    assert dest.read_bytes() == FILE_BYTES
    assert status_label.text() == "heruntergeladen"


def test_local_page_download_model_declined_does_not_download(qapp, pm, tmp_path, model_server, monkeypatch):
    page = setup_wizard.LocalSetupPage(pm)
    pm.project.comfyui_models_dir = str(tmp_path / "models")
    spec, status_label, _btn = page.model_rows[0]
    spec = dict(spec)
    spec["url"] = f"http://127.0.0.1:{model_server.server_address[1]}/model.bin"

    monkeypatch.setattr(setup_wizard.QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    page._download_model(spec, status_label)

    dest = tmp_path / "models" / spec["subdir"] / spec["filename"]
    assert not dest.exists()


def test_local_page_download_model_without_models_dir_warns_and_skips(qapp, pm, monkeypatch):
    page = setup_wizard.LocalSetupPage(pm)
    pm.project.comfyui_models_dir = ""
    spec, status_label, _btn = page.model_rows[0]

    warned = {}
    monkeypatch.setattr(setup_wizard.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.__setitem__("w", True)))
    page._download_model(spec, status_label)
    assert warned.get("w") is True


# -- RunwaySetupPage ------------------------------------------------------

def test_runway_page_save_and_delete_key(qapp, pm, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    page = setup_wizard.RunwaySetupPage(pm)
    page.key_edit.setText("sk-test-abc")
    monkeypatch.setattr(setup_wizard.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    page._save_key()
    assert credentials.get_runway_api_key() == "sk-test-abc"

    page._delete_key()
    assert credentials.get_runway_api_key() is None


def test_runway_page_apply_to_project(qapp, pm):
    page = setup_wizard.RunwaySetupPage(pm)
    idx = page.model_combo.findData("gen4.5")
    page.model_combo.setCurrentIndex(idx)
    page.budget_spin.setValue(25.5)
    page.apply_to_project()
    assert pm.project.runway_model_id == "gen4.5"
    assert pm.project.runway_budget_limit == pytest.approx(25.5)


def test_runway_page_test_connection_success(qapp, pm, runway_server, monkeypatch):
    page = setup_wizard.RunwaySetupPage(pm)
    page.key_edit.setText("wizard-test-key")

    monkeypatch.setattr(setup_wizard, "RunwayProvider", lambda api_key=None: _make_provider(api_key, runway_server))

    shown = {}
    monkeypatch.setattr(setup_wizard.QMessageBox, "information", staticmethod(lambda *a, **k: shown.__setitem__("ok", True)))
    monkeypatch.setattr(setup_wizard.QMessageBox, "critical", staticmethod(lambda *a, **k: shown.__setitem__("fail", True)))

    page._test_connection()
    assert shown.get("ok") is True
    assert "fail" not in shown


def test_runway_page_test_connection_failure_shows_critical(qapp, pm, runway_server, monkeypatch):
    page = setup_wizard.RunwaySetupPage(pm)
    page.key_edit.setText("totally-wrong-key")
    monkeypatch.setattr(setup_wizard, "RunwayProvider", lambda api_key=None: _make_provider(api_key, runway_server))

    shown = {}
    monkeypatch.setattr(setup_wizard.QMessageBox, "critical", staticmethod(lambda *a, **k: shown.__setitem__("fail", True)))
    page._test_connection()
    assert shown.get("fail") is True


def _make_provider(api_key, server):
    from voxini_studio.providers.runway_provider import RunwayProvider

    return RunwayProvider(api_key=api_key, base_url=server.base_url)


# -- SetupWizardDialog ------------------------------------------------------

def test_setup_wizard_dialog_accept_applies_and_saves(qapp, pm):
    dialog = setup_wizard.SetupWizardDialog(None, pm)
    dialog.local_page.host_edit.setText("10.0.0.5")
    dialog.runway_page.budget_spin.setValue(12.0)
    dialog._accept()

    assert pm.project.comfyui_host == "10.0.0.5"
    assert pm.project.runway_budget_limit == pytest.approx(12.0)

    reopened = ProjectManager()
    reopened.open(pm.paths.root)
    assert reopened.project.comfyui_host == "10.0.0.5"
