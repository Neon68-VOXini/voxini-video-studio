"""Export tab: title text, aspect ratio, subtitle mode, and the final
FFmpeg assembly/export call."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from voxini_studio.core.ffmpeg_assembly import AssemblyError, assemble_video, ready_scenes
from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.models.project import AspectRatio, SceneStatus
from voxini_studio.ui import theme
from voxini_studio.ui.icons import icon

_ASPECT_LABELS = {
    AspectRatio.WIDESCREEN: "16:9 (Querformat)",
    AspectRatio.VERTICAL: "9:16 (Hochformat)",
    AspectRatio.SQUARE: "1:1 (Quadratisch)",
}
_SUBTITLE_LABELS = {"soft": "Weich (Untertitel-Spur)", "burned": "Eingebrannt", "none": "Keine"}


class ExportPanel(QWidget):
    def __init__(self, pm: ProjectManager, on_changed=None) -> None:
        super().__init__()
        self.pm = pm
        self.on_changed = on_changed or (lambda: None)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Titeltext (optional, als Titelkarte am Anfang)"))
        self.title_edit = QLineEdit()
        layout.addWidget(self.title_edit)

        row = QHBoxLayout()
        row.addWidget(QLabel("Seitenverhältnis:"))
        self.aspect_combo = QComboBox()
        for ar, label in _ASPECT_LABELS.items():
            self.aspect_combo.addItem(label, ar.value)
        row.addWidget(self.aspect_combo)

        row.addWidget(QLabel("Untertitel:"))
        self.subtitle_combo = QComboBox()
        for mode, label in _SUBTITLE_LABELS.items():
            self.subtitle_combo.addItem(label, mode)
        row.addWidget(self.subtitle_combo)
        row.addStretch(1)
        layout.addLayout(row)

        social_row = QHBoxLayout()
        self.social_outro_checkbox = QCheckBox("Plattform-Abspannkarte (TikTok/YouTube/Instagram/Spotify/Amazon Music/Apple Music + Website) am Ende einblenden")
        self.social_outro_checkbox.toggled.connect(self._on_social_outro_toggled)
        social_row.addWidget(self.social_outro_checkbox)
        layout.addLayout(social_row)

        website_row = QHBoxLayout()
        self.social_website_label = QLabel("Website:")
        website_row.addWidget(self.social_website_label)
        self.social_website_edit = QLineEdit()
        self.social_website_edit.setPlaceholderText("z.B. Neon68.de")
        website_row.addWidget(self.social_website_edit)
        layout.addLayout(website_row)

        social_hint = QLabel(
            "Wird als echter Text + echte Plattform-Icons zusammengesetzt (nicht vom KI-Modell "
            "generiert), damit Schrift und Logos garantiert korrekt aussehen."
        )
        social_hint.setProperty("role", "muted")
        social_hint.setWordWrap(True)
        layout.addWidget(social_hint)

        self.readiness_label = QLabel("")
        self.readiness_label.setProperty("role", "muted")
        layout.addWidget(self.readiness_label)

        export_btn = QPushButton(" Exportieren...")
        export_btn.setIcon(icon("download", "#ffffff"))
        export_btn.setProperty("role", "primary")
        export_btn.clicked.connect(self._export)
        layout.addWidget(export_btn)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

        self.refresh()

    def refresh(self) -> None:
        proj = self.pm.project
        if proj is None:
            self.readiness_label.setText("Kein Projekt geöffnet.")
            return
        self.title_edit.setText(proj.title_text)
        idx = self.subtitle_combo.findData(proj.subtitle_mode)
        if idx >= 0:
            self.subtitle_combo.setCurrentIndex(idx)
        self.social_outro_checkbox.setChecked(proj.social_outro_enabled)
        self.social_website_edit.setText(proj.social_outro_website)
        self._on_social_outro_toggled(proj.social_outro_enabled)
        total = len(proj.scenes)
        ready_count = len(ready_scenes(self.pm)) if total else 0
        needs_review_count = sum(1 for s in proj.scenes if s.status == SceneStatus.NEEDS_REVIEW)
        text = f"{ready_count} von {total} Szenen haben einen akzeptierten Clip."
        if needs_review_count:
            text += (
                f" ({needs_review_count} davon von der automatischen Gesichtskontrolle zur "
                "Pruefung markiert und vom Export ausgeschlossen.)"
            )
        self.readiness_label.setText(text)
        role = "warning" if needs_review_count else "success" if total and ready_count == total else "warning" if ready_count else "muted"
        self.readiness_label.setProperty("role", role)
        self.readiness_label.style().unpolish(self.readiness_label)
        self.readiness_label.style().polish(self.readiness_label)

    def _on_social_outro_toggled(self, checked: bool) -> None:
        self.social_website_label.setEnabled(checked)
        self.social_website_edit.setEnabled(checked)

    def _export(self) -> None:
        proj = self.pm.project
        if proj is None:
            QMessageBox.warning(self, "Kein Projekt", "Bitte zuerst ein Projekt öffnen.")
            return
        if not ready_scenes(self.pm):
            QMessageBox.warning(
                self, "Keine Clips", "Es gibt noch keine akzeptierten Clips zum Exportieren."
            )
            return

        if not proj.audio_path:
            reply = QMessageBox.warning(
                self,
                "Kein Audio importiert",
                "Es ist keine Audiodatei importiert (Tab 'Projekt' -> 'Audio (WAV/MP3)'). "
                "Das exportierte Video wäre komplett STUMM.\n\n"
                "Trotzdem ohne Ton exportieren?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        proj.title_text = self.title_edit.text().strip()
        proj.subtitle_mode = self.subtitle_combo.currentData()
        proj.social_outro_enabled = self.social_outro_checkbox.isChecked()
        proj.social_outro_website = self.social_website_edit.text().strip() or "Neon68.de"
        aspect = AspectRatio(self.aspect_combo.currentData())

        default_name = f"{proj.name.replace(' ', '_')}_{aspect.value.replace(':', 'x')}.mp4"
        # Default into the project's own export/ folder (see ProjectPaths.export_dir)
        # instead of leaving Qt's file dialog to fall back to whatever directory
        # was last used - that's how the "Willkommen bei Neon68" export ended up
        # sitting in the program's install folder instead of with the rest of
        # that project's own files.
        if self.pm.paths is not None:
            self.pm.paths.export_dir.mkdir(parents=True, exist_ok=True)
            default_path = str(self.pm.paths.export_dir / default_name)
        else:
            default_path = default_name
        path, _ = QFileDialog.getSaveFileName(self, "Export speichern unter", default_path, "Video (*.mp4)")
        if not path:
            return

        self.log.appendPlainText(f"Exportiere nach {path} ...")
        try:
            assemble_video(self.pm, aspect, Path(path))
        except AssemblyError as exc:
            self.log.appendPlainText(f"FEHLER: {exc}")
            QMessageBox.critical(self, "Export fehlgeschlagen", str(exc))
            return
        self.log.appendPlainText("Fertig.")
        QMessageBox.information(self, "Export abgeschlossen", f"Video gespeichert unter:\n{path}")
        self.on_changed()
