"""Wartung: Zugriff auf die automatischen Projekt-Backups (rolling
backups, angelegt bei jedem manuellen Speichern - siehe
core/autosave.make_backup) und das Fehlerprotokoll (core/error_log). Beide
Mechanismen liefen bisher unsichtbar im Hintergrund; dieser Dialog macht
sie fuer den Benutzer tatsaechlich erreichbar und bedienbar."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from voxini_studio.core import error_log
from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.ui import theme
from voxini_studio.ui.icons import icon

_MAX_LOG_CHARS = 20000


class BackupsPage(QWidget):
    def __init__(self, pm: ProjectManager) -> None:
        super().__init__()
        self.pm = pm
        layout = QVBoxLayout(self)

        info = QLabel(
            "Bei jedem manuellen Speichern wird automatisch eine Sicherungskopie des vorherigen Stands "
            "angelegt (bis zu 10 Versionen). Hier kannst du eine ältere Version wiederherstellen."
        )
        info.setWordWrap(True)
        info.setProperty("role", "muted")
        layout.addWidget(info)

        self.list = QListWidget()
        layout.addWidget(self.list, 1)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton(" Aktualisieren")
        refresh_btn.setIcon(icon("refresh-cw", theme.palette().text))
        refresh_btn.clicked.connect(self.refresh)
        restore_btn = QPushButton(" Ausgewählte Version wiederherstellen")
        restore_btn.setIcon(icon("check-circle", "#ffffff"))
        restore_btn.setProperty("role", "primary")
        restore_btn.clicked.connect(self._restore_selected)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(restore_btn)
        layout.addLayout(btn_row)

        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for path in self.pm.list_backups():
            ts = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            item = QListWidgetItem(f"{ts}  -  {path.name}")
            item.setData(32, str(path))  # Qt.UserRole == 32
            self.list.addItem(item)
        if self.list.count() == 0:
            self.list.addItem("Noch keine Backups vorhanden (werden ab dem nächsten Speichern angelegt).")

    def _restore_selected(self) -> None:
        items = self.list.selectedItems()
        if not items or items[0].data(32) is None:
            QMessageBox.information(self, "Keine Auswahl", "Bitte zuerst ein Backup in der Liste auswählen.")
            return
        path = items[0].data(32)
        confirm = QMessageBox.question(
            self, "Backup wiederherstellen",
            "Der aktuelle Projektstand wird durch diese Sicherung ersetzt und sofort gespeichert "
            "(der bisherige Stand wird dabei selbst wieder als Backup gesichert). Fortfahren?",
        )
        if confirm != QMessageBox.Yes:
            return
        self.pm.restore_backup(path)
        self.pm.save()
        self.refresh()
        QMessageBox.information(self, "Wiederhergestellt", "Das Backup wurde übernommen und gespeichert.")


class ErrorLogPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        info = QLabel(
            "Technisches Fehlerprotokoll dieser Installation. Bei einem Problem auf deinem Windows-PC "
            "hilft dieser Inhalt bei der Fehlersuche."
        )
        info.setWordWrap(True)
        info.setProperty("role", "muted")
        layout.addWidget(info)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(self.log_view, 1)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton(" Aktualisieren")
        refresh_btn.setIcon(icon("refresh-cw", theme.palette().text))
        refresh_btn.clicked.connect(self.refresh)
        open_folder_btn = QPushButton(" Protokollordner öffnen")
        open_folder_btn.setIcon(icon("folder-open", theme.palette().text))
        open_folder_btn.clicked.connect(self._open_folder)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(open_folder_btn)
        layout.addLayout(btn_row)

        self.refresh()

    def refresh(self) -> None:
        path = error_log.current_log_path()
        if not path.exists():
            self.log_view.setPlainText("Noch keine Protokolleinträge vorhanden.")
            return
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > _MAX_LOG_CHARS:
            content = "... (gekürzt) ...\n" + content[-_MAX_LOG_CHARS:]
        self.log_view.setPlainText(content)
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(error_log.log_dir())))


class MaintenanceDialog(QDialog):
    def __init__(self, parent, pm: ProjectManager) -> None:
        super().__init__(parent)
        self.setWindowTitle("Wartung: Backups & Fehlerprotokoll")
        self.resize(720, 560)
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(BackupsPage(pm), "Backups")
        tabs.addTab(ErrorLogPage(), "Fehlerprotokoll")
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Schließen")
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)
