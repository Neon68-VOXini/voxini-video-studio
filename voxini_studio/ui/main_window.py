"""Main application window - Phase 1 shell.

Phase 1 scope: create/open a project, import audio/SRT/prompt files and
character reference images, see the imported state reflected in the UI,
and save the project. Storyboard, timeline and provider UI are added in
later phases and simply appear as additional tabs.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from voxini_studio import __version__
from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.core.update_check import UpdateCheckError, check_for_update, check_for_update_verbose
from voxini_studio.ui import theme
from voxini_studio.ui.character_panel import CharacterPanel
from voxini_studio.ui.export_panel import ExportPanel
from voxini_studio.ui.generation_panel import GenerationPanel
from voxini_studio.ui.icons import icon
from voxini_studio.ui.lyrics_sync_dialog import LyricsSyncDialog
from voxini_studio.ui.maintenance_dialog import MaintenanceDialog
from voxini_studio.ui.setup_wizard import SetupWizardDialog
from voxini_studio.ui.storyboard_view import StoryboardView
from voxini_studio.ui.theme import icons_dir
from voxini_studio.ui.update_dialog import UpdateDialog

# App root = the folder this package (voxini_studio/) lives directly under,
# e.g. "D:\Studio Neon68\VOXini Video Studio". Used as the base for the
# automatic "Projekte" folder so every new project lands in one clean,
# predictable place instead of wherever an OS folder-picker happened to be
# pointed at (that ambiguity is exactly how earlier projects ended up
# misplaced inside tests/).
#
# In der gebauten Anwendung (PyInstaller-Onedir seit v1.1.0, vorher
# Onefile) zeigt __file__ auf sys._MEIPASS - im aktuellen Onedir-Build ist
# das der "_internal"-Unterordner der eigentlichen Installation (also
# unproblematisch), im frueheren Onefile-Build war das dagegen ein bei
# jedem Start neu angelegter und beim Beenden wieder geloeschter
# temporaerer Ordner, wodurch _APP_ROOT dort auf einen verschwindenden Pfad
# gezeigt haette. sys.executable (gleiches Prinzip wie
# core/app_update.py:current_exe_path()) zeigt in beiden Modi zuverlaessig
# auf den echten, dauerhaften Installationsordner und wird deshalb hier
# weiterhin bevorzugt - via is_running_as_frozen_exe() faellt der Code im
# Python-Entwicklungsbetrieb (sys.frozen nicht gesetzt) auf die
# __file__-basierte Berechnung zurueck.
if getattr(sys, "frozen", False):
    _APP_ROOT = Path(sys.executable).resolve().parent
else:
    _APP_ROOT = Path(__file__).resolve().parent.parent.parent


def _projects_root() -> Path:
    root = _APP_ROOT / "Projekte"
    root.mkdir(parents=True, exist_ok=True)
    return root


_INVALID_FOLDER_CHARS = '\\/:*?"<>|'


def _safe_folder_name(name: str) -> str:
    """Strips characters that are illegal in Windows folder names so the
    project name the user types can always be used directly as the
    subfolder name under Projekte/."""
    cleaned = "".join(c for c in name if c not in _INVALID_FOLDER_CHARS).strip(" .")
    return cleaned or "Projekt"


class ImportRow(QWidget):
    """One labeled 'file not set / Import.../Entfernen' row used for
    audio/SRT/prompt. The Entfernen button only resets the project's
    reference to the file (back to "nicht gesetzt") - it never deletes the
    copied file in media/, so this is always safe/reversible via re-import."""

    def __init__(self, label: str, on_import, on_clear) -> None:
        super().__init__()
        self._on_import = on_import
        self._on_clear = on_clear
        self._is_set = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(label)
        self.title.setFixedWidth(140)
        self.value = QLabel("nicht gesetzt")
        self.value.setProperty("role", "muted")
        btn = QPushButton(" Importieren...")
        btn.setIcon(icon("upload", theme.palette().text))
        btn.clicked.connect(self._handle_click)
        self.clear_btn = QPushButton(" Entfernen")
        self.clear_btn.setIcon(icon("x-circle", theme.palette().text))
        self.clear_btn.clicked.connect(self._handle_clear)
        self.clear_btn.setEnabled(False)
        layout.addWidget(self.title)
        layout.addWidget(self.value, 1)
        layout.addWidget(btn)
        layout.addWidget(self.clear_btn)

    def _handle_click(self) -> None:
        self._on_import()

    def _handle_clear(self) -> None:
        if not self._is_set:
            return
        reply = QMessageBox.question(
            self,
            "Entfernen",
            f"'{self.title.text()}' aus dem Projekt entfernen? Die Datei bleibt im "
            "Projektordner erhalten, das Projekt verweist danach nur nicht mehr darauf.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._on_clear()

    def set_value(self, text: str) -> None:
        self._is_set = text != "nicht gesetzt"
        self.value.setText(text)
        self.value.setProperty("role", "" if self._is_set else "muted")
        self.value.style().unpolish(self.value)
        self.value.style().polish(self.value)
        self.clear_btn.setEnabled(self._is_set)


class ProjectPanel(QWidget):
    def __init__(self, pm: ProjectManager, on_project_changed) -> None:
        super().__init__()
        self.pm = pm
        self.on_project_changed = on_project_changed

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.project_label = QLabel("Kein Projekt geöffnet")
        self.project_label.setProperty("role", "heading")
        new_btn = QPushButton(" Neues Projekt...")
        new_btn.setIcon(icon("folder-plus", theme.palette().text))
        open_btn = QPushButton(" Projekt öffnen...")
        open_btn.setIcon(icon("folder-open", theme.palette().text))
        save_btn = QPushButton(" Speichern")
        save_btn.setIcon(icon("save", "#ffffff"))
        save_btn.setProperty("role", "primary")
        new_btn.clicked.connect(self._new_project)
        open_btn.clicked.connect(self._open_project)
        save_btn.clicked.connect(self._save_project)
        header.addWidget(self.project_label, 1)
        header.addWidget(new_btn)
        header.addWidget(open_btn)
        header.addWidget(save_btn)
        layout.addLayout(header)

        source_box = QGroupBox("Quelldateien")
        source_layout = QVBoxLayout(source_box)
        self.audio_row = ImportRow("Audio (WAV/MP3)", self._import_audio, self._clear_audio)
        self.srt_row = ImportRow("Untertitel (SRT)", self._import_srt, self._clear_srt)
        self.prompt_row = ImportRow("Video-Prompt (TXT)", self._import_prompt, self._clear_prompt)
        source_layout.addWidget(self.audio_row)
        source_layout.addWidget(self.srt_row)
        sync_row = QHBoxLayout()
        sync_row.addStretch(1)
        sync_lyrics_btn = QPushButton(" Songtext eingeben & automatisch synchronisieren...")
        sync_lyrics_btn.setIcon(icon("wand", theme.palette().text))
        sync_lyrics_btn.clicked.connect(self._open_lyrics_sync_dialog)
        sync_row.addWidget(sync_lyrics_btn)
        source_layout.addLayout(sync_row)
        source_layout.addWidget(self.prompt_row)
        layout.addWidget(source_box)

        char_box = QGroupBox("Charakterprofile")
        char_layout = QVBoxLayout(char_box)
        self.char_list = QListWidget()
        char_btns = QHBoxLayout()
        add_char_btn = QPushButton(" Charakter hinzufügen...")
        add_char_btn.setIcon(icon("user-plus", theme.palette().text))
        add_ref_btn = QPushButton(" Referenzbild hinzufügen...")
        add_ref_btn.setIcon(icon("image-plus", theme.palette().text))
        add_char_btn.clicked.connect(self._add_character)
        add_ref_btn.clicked.connect(self._add_reference_image)
        char_btns.addWidget(add_char_btn)
        char_btns.addWidget(add_ref_btn)
        char_layout.addWidget(self.char_list)
        char_layout.addLayout(char_btns)
        layout.addWidget(char_box)

        layout.addStretch(1)
        self.refresh()

    # -- helpers ------------------------------------------------------
    def _require_project(self) -> bool:
        if self.pm.project is None:
            QMessageBox.warning(self, "Kein Projekt", "Bitte zuerst ein Projekt anlegen oder öffnen.")
            return False
        return True

    def refresh(self) -> None:
        proj = self.pm.project
        if proj is None:
            self.project_label.setText("Kein Projekt geöffnet")
            self.audio_row.set_value("nicht gesetzt")
            self.srt_row.set_value("nicht gesetzt")
            self.prompt_row.set_value("nicht gesetzt")
            self.char_list.clear()
            return
        self.project_label.setText(proj.name)
        self.audio_row.set_value(proj.audio_path or "nicht gesetzt")
        self.srt_row.set_value(proj.srt_path or "nicht gesetzt")
        self.prompt_row.set_value(proj.full_prompt_path or "nicht gesetzt")
        self.char_list.clear()
        for char in proj.characters.values():
            n_refs = len(char.reference_image_paths)
            self.char_list.addItem(f"{char.name}  ({n_refs} Referenzbild{'er' if n_refs != 1 else ''})")
        self.on_project_changed()

    # -- project actions ------------------------------------------------
    def _new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "Projektname", "Name des Projekts:")
        if not ok or not name.strip():
            return
        name = name.strip()

        projects_root = _projects_root()
        directory = projects_root / _safe_folder_name(name)
        if directory.exists() and any(directory.iterdir()):
            QMessageBox.warning(
                self,
                "Projekt existiert bereits",
                f"Der Ordner\n{directory}\nexistiert bereits und ist nicht leer. "
                "Bitte einen anderen Projektnamen wählen oder das bestehende Projekt "
                "über 'Projekt öffnen...' laden.",
            )
            return
        directory.mkdir(parents=True, exist_ok=True)

        self.pm.create_new(directory, name)
        self.refresh()

    def _open_project(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Projektordner wählen", str(_projects_root()))
        if not directory:
            return
        try:
            self.pm.open(directory)
        except FileNotFoundError as exc:
            QMessageBox.critical(self, "Fehler", str(exc))
            return

        if self.pm.has_pending_recovery():
            reply = QMessageBox.question(
                self,
                "Automatische Sicherung gefunden",
                "Es wurde eine automatische Sicherung gefunden, die neuer ist als der zuletzt "
                "manuell gespeicherte Stand dieses Projekts (z. B. nach einem Absturz oder "
                "Schließen ohne Speichern). Möchtest du diesen Stand wiederherstellen?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.pm.recover_from_autosave()
                self.pm.save()
                QMessageBox.information(self, "Wiederhergestellt", "Die automatische Sicherung wurde übernommen und gespeichert.")
            else:
                self.pm.discard_recovery()

        self.refresh()

    def _save_project(self) -> None:
        if not self._require_project():
            return
        self.pm.save()
        QMessageBox.information(self, "Gespeichert", "Projekt wurde gespeichert.")

    # -- import actions ---------------------------------------------------
    def _import_audio(self) -> None:
        if not self._require_project():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Audio wählen", "", "Audio (*.wav *.mp3)")
        if not path:
            return
        self.pm.import_audio(path)
        self.refresh()

    def _import_srt(self) -> None:
        if not self._require_project():
            return
        path, _ = QFileDialog.getOpenFileName(self, "SRT wählen", "", "Untertitel (*.srt)")
        if not path:
            return
        self.pm.import_srt(path)
        self.refresh()

    def _open_lyrics_sync_dialog(self) -> None:
        if not self._require_project():
            return
        dialog = LyricsSyncDialog(self, self.pm)
        if dialog.exec() == LyricsSyncDialog.Accepted:
            self.refresh()

    def _import_prompt(self) -> None:
        if not self._require_project():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Prompt-Datei wählen", "", "Text (*.txt *.md)")
        if not path:
            return
        self.pm.import_prompt(path)
        self.refresh()

    def _clear_audio(self) -> None:
        if not self._require_project():
            return
        self.pm.clear_audio()
        self.refresh()

    def _clear_srt(self) -> None:
        if not self._require_project():
            return
        self.pm.clear_srt()
        self.refresh()

    def _clear_prompt(self) -> None:
        if not self._require_project():
            return
        self.pm.clear_prompt()
        self.refresh()

    def _add_character(self) -> None:
        if not self._require_project():
            return
        name, ok = QInputDialog.getText(self, "Neuer Charakter", "Name:")
        if not ok or not name.strip():
            return
        desc, ok2 = QInputDialog.getMultiLineText(
            self, "Charakterbeschreibung", "Beschreibung (Alter, Haare, Augen, Kleidung...):"
        )
        self.pm.add_character(name.strip(), desc if ok2 else "")
        self.refresh()

    def _add_reference_image(self) -> None:
        if not self._require_project():
            return
        proj = self.pm.project
        if not proj.characters:
            QMessageBox.warning(self, "Kein Charakter", "Bitte zuerst einen Charakter anlegen.")
            return
        names = [c.name for c in proj.characters.values()]
        ids = list(proj.characters.keys())
        name, ok = QInputDialog.getItem(self, "Charakter wählen", "Charakter:", names, editable=False)
        if not ok:
            return
        char_id = ids[names.index(name)]
        path, _ = QFileDialog.getOpenFileName(self, "Referenzbild wählen", "", "Bilder (*.png *.jpg *.jpeg)")
        if not path:
            return
        self.pm.import_character_reference(char_id, path)
        self.refresh()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("VOXini Video Studio")
        self.resize(1200, 760)

        self.pm = ProjectManager()

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 8, 10, 10)
        root_layout.setSpacing(8)

        top_bar = QWidget()
        top_bar.setObjectName("TopBar")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(4, 2, 4, 2)

        self.logo = QSvgWidget(str(icons_dir() / "logo.svg"))
        self.logo.setFixedSize(180, 34)
        top_layout.addWidget(self.logo)
        top_layout.addStretch(1)

        setup_btn = QPushButton(" Einrichtungsassistent")
        setup_btn.setIcon(icon("settings", theme.palette().text))
        setup_btn.clicked.connect(self._open_setup_wizard)
        top_layout.addWidget(setup_btn)

        maintenance_btn = QPushButton(" Wartung")
        maintenance_btn.setIcon(icon("clock", theme.palette().text))
        maintenance_btn.clicked.connect(self._open_maintenance_dialog)
        top_layout.addWidget(maintenance_btn)

        update_btn = QPushButton(" Nach Updates suchen")
        update_btn.setIcon(icon("refresh-cw", theme.palette().text))
        update_btn.clicked.connect(self._check_for_updates_manual)
        top_layout.addWidget(update_btn)

        top_layout.addWidget(QLabel("Theme:"))
        self.theme_combo = QComboBox()
        for name, label in theme.THEME_LABELS.items():
            self.theme_combo.addItem(label, name)
        current = theme.load_theme_preference()
        idx = self.theme_combo.findData(current)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        top_layout.addWidget(self.theme_combo)

        root_layout.addWidget(top_bar)

        self.tabs = QTabWidget()
        self.project_panel = ProjectPanel(self.pm, self._on_project_changed)
        self.tabs.addTab(self.project_panel, "Projekt")

        self.storyboard_view = StoryboardView(self.pm)
        self.tabs.addTab(self.storyboard_view, "Storyboard")

        self.character_panel = CharacterPanel(self.pm, self._on_project_changed)
        self.tabs.addTab(self.character_panel, "Charaktere")

        self.generation_panel = GenerationPanel(self.pm, self._on_project_changed)
        self.tabs.addTab(self.generation_panel, "Generierung")

        self.export_panel = ExportPanel(self.pm, self._on_project_changed)
        self.tabs.addTab(self.export_panel, "Export")

        root_layout.addWidget(self.tabs, 1)
        self.setCentralWidget(root)

        # periodic autosave - writes project.autosave.voxproj every 60s
        # while a project is open, WITHOUT touching the user's manually
        # saved project.voxproj (see core/autosave.py). Purely a safety
        # net for crashes/unclean shutdown, recovery is offered on next open.
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(60_000)
        self._autosave_timer.timeout.connect(self._autosave_tick)
        self._autosave_timer.start()

        # Stiller Hintergrund-Update-Check kurz nach dem Start (Punkt 4 der
        # Spezifikation) - verzoegert, damit er den eigentlichen Start nicht
        # bremst. Zeigt NUR dann etwas an, wenn tatsaechlich ein Update
        # gefunden wurde; bei "kein Update" oder Netzwerkfehler passiert
        # bewusst nichts Sichtbares (siehe core/update_check.check_for_update).
        QTimer.singleShot(3000, self._check_for_updates_silent)

    def _check_for_updates_silent(self) -> None:
        info = check_for_update()
        if info is not None:
            UpdateDialog(self, info, current_version=__version__).exec()

    def _check_for_updates_manual(self) -> None:
        try:
            info = check_for_update_verbose()
        except UpdateCheckError as exc:
            UpdateDialog(self, None, check_error=str(exc), current_version=__version__).exec()
            return
        UpdateDialog(self, info, current_version=__version__).exec()

    def _open_setup_wizard(self) -> None:
        if self.pm.project is None:
            QMessageBox.warning(self, "Kein Projekt", "Bitte zuerst ein Projekt anlegen oder öffnen.")
            return
        dialog = SetupWizardDialog(self, self.pm)
        if dialog.exec() == SetupWizardDialog.Accepted:
            self._on_project_changed()

    def _open_maintenance_dialog(self) -> None:
        if self.pm.project is None:
            QMessageBox.warning(self, "Kein Projekt", "Bitte zuerst ein Projekt anlegen oder öffnen.")
            return
        dialog = MaintenanceDialog(self, self.pm)
        dialog.exec()
        self._on_project_changed()

    def _autosave_tick(self) -> None:
        if self.pm.project is not None:
            self.pm.autosave()

    def _on_theme_changed(self) -> None:
        name = self.theme_combo.currentData()
        if name is None:
            return
        theme.save_theme_preference(name)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.build_stylesheet(name))

    def _on_project_changed(self) -> None:
        self.storyboard_view.refresh()
        self.character_panel.refresh()
        self.generation_panel.refresh()
        self.export_panel.refresh()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # last-chance autosave so an unsaved-but-closed session is still
        # recoverable next time the project is opened
        if self.pm.project is not None:
            self.pm.autosave()
        super().closeEvent(event)
