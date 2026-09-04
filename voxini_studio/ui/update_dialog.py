"""EIN Dialog fuer den gesamten Auto-Update-Ablauf von VOXini Video Studio:
zeigt aktuelle vs. neue Version + Änderungshinweise (Changelog aus den
GitHub-Release-Notizen) an, startet Download+Installation erst nach
expliziter Bestätigung ("Jetzt aktualisieren"), und bietet - unabhängig
vom Update-Status - immer auch "Auf vorherige Version zurückkehren" an,
falls ein Backup der zuletzt installierten Version existiert. Entspricht
Punkt 4 der Spezifikation: "Updates niemals unbemerkt installieren. Neue
Version anzeigen -> Änderungen nennen -> Nutzer bestätigt -> Update mit
Sicherung und Rückkehrmöglichkeit installieren."

Wird sowohl vom stillen Hintergrund-Check beim Programmstart geöffnet (nur
wenn tatsächlich ein Update gefunden wurde) als auch vom manuellen
"Nach Updates suchen"-Button in main_window.py - in letzterem Fall auch
dann, wenn kein Update verfügbar ist oder die Prüfung fehlschlägt, damit
der Nutzer eine sichtbare Rückmeldung bekommt statt eines stillen Nichts."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
)

from voxini_studio.core import app_update
from voxini_studio.core.update_check import UpdateInfo
from voxini_studio.ui import theme
from voxini_studio.ui.icons import icon


class UpdateDialog(QDialog):
    def __init__(
        self,
        parent,
        info: Optional[UpdateInfo],
        check_error: Optional[str] = None,
        current_version: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("VOXini Video Studio – Updates")
        self.resize(640, 440)
        self.info = info
        self._installed_download_path = None  # gesetzt, sobald ein Download erfolgreich war

        layout = QVBoxLayout(self)

        version_label = QLabel(f"Installierte Version: {current_version}")
        version_label.setProperty("role", "muted")
        layout.addWidget(version_label)

        if info is not None:
            header = QLabel(f"Neue Version verfügbar: {info.latest_version}")
            header.setProperty("role", "gold")
            layout.addWidget(header)

            changelog = QPlainTextEdit()
            changelog.setReadOnly(True)
            changelog.setPlainText(info.changelog)
            layout.addWidget(changelog, 1)

            note = QLabel(
                "Nach Bestätigung wird die neue Version heruntergeladen und die aktuelle .exe "
                "automatisch ersetzt - die bisherige Version wird dabei als Sicherung aufbewahrt "
                "und kann jederzeit über \"Auf vorherige Version zurückkehren\" wiederhergestellt werden. "
                "Die Anwendung muss danach neu gestartet werden."
            )
            note.setWordWrap(True)
            note.setProperty("role", "muted")
            layout.addWidget(note)

            if not app_update.is_running_as_frozen_exe():
                dev_note = QLabel(
                    "Hinweis: Dies ist der Python-Entwicklungsbetrieb, keine gebaute .exe - "
                    "Download/Installation sind hier nicht möglich."
                )
                dev_note.setWordWrap(True)
                dev_note.setProperty("role", "warning")
                layout.addWidget(dev_note)

            update_btn = QPushButton(" Jetzt aktualisieren")
            update_btn.setIcon(icon("download", "#ffffff"))
            update_btn.setProperty("role", "primary")
            update_btn.setEnabled(app_update.is_running_as_frozen_exe())
            update_btn.clicked.connect(self._run_update)
            layout.addWidget(update_btn)
        elif check_error is not None:
            error_label = QLabel(f"Update-Prüfung fehlgeschlagen: {check_error}")
            error_label.setWordWrap(True)
            error_label.setProperty("role", "warning")
            layout.addWidget(error_label)
        else:
            ok_label = QLabel("Du verwendest bereits die neueste Version.")
            ok_label.setProperty("role", "success")
            layout.addWidget(ok_label)

        layout.addStretch(1)

        # Rueckkehr-Bereich - unabhaengig davon, ob gerade ein Update
        # gefunden wurde: relevant z.B. wenn ein frueheres Update sich als
        # problematisch herausgestellt hat.
        revert_row = QHBoxLayout()
        can_revert = app_update.can_revert_to_previous_version()
        revert_label = QLabel(
            "Eine Sicherung einer vorherigen Version ist vorhanden."
            if can_revert else
            "Keine Sicherung einer vorherigen Version vorhanden."
        )
        revert_label.setProperty("role", "muted")
        revert_row.addWidget(revert_label, 1)
        revert_btn = QPushButton(" Auf vorherige Version zurückkehren")
        revert_btn.setIcon(icon("clock", theme.palette().text))
        revert_btn.setEnabled(can_revert)
        revert_btn.clicked.connect(self._run_revert)
        revert_row.addWidget(revert_btn)
        layout.addLayout(revert_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Schließen")
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _run_update(self) -> None:
        assert self.info is not None
        confirm = QMessageBox.question(
            self, "Update installieren",
            f"Version {self.info.latest_version} jetzt herunterladen und installieren?\n\n"
            f"Die aktuelle Version wird dabei als Sicherung aufbewahrt.",
        )
        if confirm != QMessageBox.Yes:
            return

        progress = QProgressDialog("Lade Update herunter...", "Abbrechen", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        def on_progress(done: int, total: int) -> None:
            if total:
                progress.setValue(int(done / total * 100))
            QApplication.processEvents()

        def cancel_check() -> bool:
            return progress.wasCanceled()

        try:
            downloaded = app_update.download_update(
                self.info.download_url, progress_callback=on_progress, cancel_check=cancel_check
            )
        except app_update.UpdateInstallError as exc:
            progress.close()
            if progress.wasCanceled():
                QMessageBox.information(self, "Abgebrochen", "Download abgebrochen. Es wurde nichts installiert.")
            else:
                QMessageBox.critical(self, "Download fehlgeschlagen", str(exc))
            return

        progress.close()

        try:
            app_update.install_downloaded_update(downloaded)
        except OSError as exc:
            QMessageBox.critical(
                self, "Installation fehlgeschlagen",
                f"Die heruntergeladene Datei konnte nicht eingesetzt werden: {exc}\n\n"
                f"Die bisherige Version läuft unverändert weiter.",
            )
            return

        restart = QMessageBox.question(
            self, "Update installiert",
            f"Version {self.info.latest_version} wurde installiert. VOXini Video Studio muss neu "
            f"gestartet werden, damit die neue Version verwendet wird.\n\nJetzt neu starten?",
        )
        self.accept()
        if restart == QMessageBox.Yes:
            app_update.relaunch_and_exit()

    def _run_revert(self) -> None:
        confirm = QMessageBox.question(
            self, "Auf vorherige Version zurückkehren",
            "Die aktuell installierte Version wird verworfen und durch die zuletzt gesicherte "
            "vorherige Version ersetzt. Fortfahren?",
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            app_update.revert_to_previous_version()
        except app_update.UpdateInstallError as exc:
            QMessageBox.critical(self, "Zurückkehren fehlgeschlagen", str(exc))
            return

        restart = QMessageBox.question(
            self, "Zurückgekehrt",
            "Die vorherige Version wurde wiederhergestellt. VOXini Video Studio muss neu gestartet "
            "werden.\n\nJetzt neu starten?",
        )
        self.accept()
        if restart == QMessageBox.Yes:
            app_update.relaunch_and_exit()
