import sys

import pytest

from voxini_studio.core import error_log


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    error_log.reset_for_tests()
    yield tmp_path
    error_log.reset_for_tests()


def test_setup_error_logging_creates_log_file(isolated_appdata):
    path = error_log.setup_error_logging()
    assert path.exists()
    assert path.name == "error.log"
    assert path.parent.name == "logs"


def test_setup_error_logging_is_idempotent(isolated_appdata):
    path1 = error_log.setup_error_logging()
    path2 = error_log.setup_error_logging()
    assert path1 == path2


def test_log_error_writes_message_and_traceback(isolated_appdata):
    error_log.setup_error_logging()
    try:
        raise ValueError("boom")
    except ValueError as exc:
        error_log.log_error("Testfehler aufgetreten", exc)

    content = error_log.current_log_path().read_text(encoding="utf-8")
    assert "Testfehler aufgetreten" in content
    assert "ValueError" in content
    assert "boom" in content


def test_log_info_writes_message(isolated_appdata):
    error_log.setup_error_logging()
    error_log.log_info("Anwendung gestartet")
    content = error_log.current_log_path().read_text(encoding="utf-8")
    assert "Anwendung gestartet" in content


def test_excepthook_logs_uncaught_exception(isolated_appdata):
    error_log.setup_error_logging()
    try:
        raise RuntimeError("uncaught boom")
    except RuntimeError:
        exc_type, exc_value, exc_tb = sys.exc_info()
        # invoke the installed hook directly rather than actually crashing
        # the test process
        sys.excepthook(exc_type, exc_value, exc_tb)

    content = error_log.current_log_path().read_text(encoding="utf-8")
    assert "Unbehandelte Ausnahme" in content
    assert "uncaught boom" in content


def test_get_logger_auto_initializes(isolated_appdata):
    logger = error_log.get_logger()
    assert logger.name == error_log.LOGGER_NAME
    assert error_log.current_log_path().exists()
