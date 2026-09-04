"""Central error/crash logging (Fehlerprotokollierung). Writes a rotating
log file under the user's per-app data directory (Windows: %APPDATA%\\
VOXiniVideoStudio\\logs, fallback elsewhere: ~/.config/VOXiniVideoStudio/
logs - same convention as core/credentials.py's fallback store) and
installs a sys.excepthook so ANY uncaught exception - not just ones an
individual dialog happens to catch - leaves a diagnosable trail with a
timestamp and full traceback. This is what the Windows test protocol
(see docs/WINDOWS_TESTPROTOKOLL.md) points the user at when something goes
wrong during the unavoidable real-machine test.
"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

APP_NAME = "VOXiniVideoStudio"
LOGGER_NAME = "voxini_studio"

_logger: Optional[logging.Logger] = None
_original_excepthook = sys.excepthook


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    d = root / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir() -> Path:
    d = app_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def current_log_path() -> Path:
    return log_dir() / "error.log"


def setup_error_logging() -> Path:
    """Idempotent - safe to call multiple times (e.g. once from main.py and
    once from a test). Returns the active log file path."""
    global _logger
    path = current_log_path()
    if _logger is not None:
        return path

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    _logger = logger

    def _excepthook(exc_type, exc_value, exc_tb):
        logger.error(
            "Unbehandelte Ausnahme:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        _original_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook
    logger.info("Fehlerprotokollierung gestartet (%s)", datetime.now().isoformat(timespec="seconds"))
    return path


def get_logger() -> logging.Logger:
    if _logger is None:
        setup_error_logging()
    return _logger  # type: ignore[return-value]


def log_error(message: str, exc: Optional[BaseException] = None) -> None:
    logger = get_logger()
    if exc is not None:
        logger.error("%s: %s", message, exc, exc_info=exc)
    else:
        logger.error(message)


def log_info(message: str) -> None:
    get_logger().info(message)


def reset_for_tests() -> None:
    """Test-only helper: drops the cached logger/handler so
    setup_error_logging() can be re-run against a fresh (monkeypatched)
    APPDATA in isolated test runs."""
    global _logger
    if _logger is not None:
        for h in list(_logger.handlers):
            _logger.removeHandler(h)
            h.close()
    _logger = None
    sys.excepthook = _original_excepthook
