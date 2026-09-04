"""VOXini Video Studio - application entry point."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from voxini_studio.core.error_log import setup_error_logging
from voxini_studio.ui import theme
from voxini_studio.ui.main_window import MainWindow


def main() -> int:
    setup_error_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("VOXini Video Studio")
    app.setOrganizationName("VOXini")
    # The VOXini/Neon68 theme is applied once, centrally, at the
    # QApplication level - every widget inherits it. "voxini" (the
    # standard theme) is always what a fresh install starts with; a saved
    # preference (Einstellungen -> Theme) only ever overrides this after
    # the user has explicitly chosen Midnight/Graphite once.
    app.setStyleSheet(theme.build_stylesheet(theme.load_theme_preference()))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
