"""Storyboard tab: generate the scene list from the project's imported
audio/SRT/prompt, browse it as a table, and edit each scene's prompt text,
character assignment and notes in a detail panel."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from voxini_studio.core.audio_analysis import analyze_audio
from voxini_studio.core.continuity import (
    attach_continuity_reference,
    continuity_candidate,
    detach_continuity_reference,
)
from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.core.scene_planner import DEFAULT_MAX_CLIP_SECONDS, build_scenes
from voxini_studio.core.script_parser import parse_prompt_script
from voxini_studio.core.srt_parser import parse_srt_file
from voxini_studio.models.project import SceneStatus
from voxini_studio.ui import theme
from voxini_studio.ui.icons import icon
from voxini_studio.ui.timeline_preview import TimelinePreview

_COLUMNS = ["#", "Start", "Ende", "Dauer", "Label", "Status", "Charaktere"]

_STATUS_LABEL = {
    SceneStatus.PLANNED: "geplant",
    SceneStatus.QUEUED: "in Warteschlange",
    SceneStatus.GENERATING: "wird generiert",
    SceneStatus.DONE: "fertig",
    SceneStatus.FAILED: "fehlgeschlagen",
    SceneStatus.REJECTED: "abgelehnt",
}


def _fmt_time(seconds: float) -> str:
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:02d}:{s:05.2f}"


class StoryboardView(QWidget):
    def __init__(self, pm: ProjectManager) -> None:
        super().__init__()
        self.pm = pm
        self._selected_index: int | None = None

        outer = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.generate_btn = QPushButton(" Storyboard aus Audio + SRT + Prompt generieren")
        self.generate_btn.setIcon(icon("play", "#ffffff"))
        self.generate_btn.setProperty("role", "primary")
        self.generate_btn.clicked.connect(self._generate_storyboard)
        toolbar.addWidget(self.generate_btn)
        toolbar.addStretch(1)

        toolbar.addWidget(QLabel("Charakter:"))
        self.bulk_char_combo = QComboBox()
        toolbar.addWidget(self.bulk_char_combo)
        self.bulk_assign_btn = QPushButton(" Allen Szenen zuweisen")
        self.bulk_assign_btn.setIcon(icon("user-plus", "#ffffff"))
        self.bulk_assign_btn.clicked.connect(self._bulk_assign_character)
        toolbar.addWidget(self.bulk_assign_btn)
        outer.addLayout(toolbar)

        self.timeline_preview = TimelinePreview(pm)
        outer.addWidget(self.timeline_preview)

        splitter = QSplitter(Qt.Horizontal)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        splitter.addWidget(self.table)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.addWidget(QLabel("Szenen-Prompt"))
        self.prompt_edit = QPlainTextEdit()
        detail_layout.addWidget(self.prompt_edit, 2)

        detail_layout.addWidget(QLabel("Charaktere in dieser Szene"))
        self.char_list = QListWidget()
        detail_layout.addWidget(self.char_list, 1)

        detail_layout.addWidget(QLabel("Notizen"))
        self.notes_edit = QPlainTextEdit()
        detail_layout.addWidget(self.notes_edit, 1)

        # Anschlussbild (continuity reference) - deliberately MANUAL per
        # Neon68's decision 2026-09-05: see core/continuity.py docstring.
        # Attaching this ADDS a second simultaneous reference image on top
        # of any character reference the scene already needs, and no
        # current provider accepts two at once - so this can turn a
        # currently-generatable scene into a blocked one (see
        # generation_service.resolve_scene_references()); the cost-
        # confirmation dialog will show that plainly before anything is
        # generated, but the button here also warns up front.
        detail_layout.addWidget(QLabel("Anschlussbild (Kontinuität zur vorherigen Szene)"))
        self.continuity_label = QLabel("")
        self.continuity_label.setProperty("role", "muted")
        self.continuity_label.setWordWrap(True)
        detail_layout.addWidget(self.continuity_label)
        continuity_btn_row = QHBoxLayout()
        self.attach_continuity_btn = QPushButton(" Anschlussbild übernehmen")
        self.attach_continuity_btn.setIcon(icon("link", theme.palette().text))
        self.attach_continuity_btn.clicked.connect(self._attach_continuity)
        self.detach_continuity_btn = QPushButton(" Entfernen")
        self.detach_continuity_btn.clicked.connect(self._detach_continuity)
        continuity_btn_row.addWidget(self.attach_continuity_btn)
        continuity_btn_row.addWidget(self.detach_continuity_btn)
        detail_layout.addLayout(continuity_btn_row)

        # Automatische Gesichtskontrolle (Task #638) - siehe core/
        # face_verification.py. Nur relevant/sichtbar, wenn die letzte
        # Generierung dieser Szene als "Pruefung noetig" markiert wurde;
        # die Ueberschreibung ist eine bewusste manuelle Nutzerentscheidung
        # (gleiche "manuell statt automatisch"-Philosophie wie beim
        # Anschlussbild oben), NICHT automatisch nach einer gewissen Zeit.
        detail_layout.addWidget(QLabel("Gesichtskontrolle"))
        self.face_warning_label = QLabel("")
        self.face_warning_label.setProperty("role", "muted")
        self.face_warning_label.setWordWrap(True)
        detail_layout.addWidget(self.face_warning_label)
        self.face_override_btn = QPushButton(" Trotzdem akzeptieren")
        self.face_override_btn.setIcon(icon("check-circle", theme.palette().text))
        self.face_override_btn.clicked.connect(self._override_face_review)
        self.face_override_btn.setVisible(False)
        detail_layout.addWidget(self.face_override_btn)

        detail_btn_row = QHBoxLayout()
        self.apply_btn = QPushButton(" Übernehmen")
        self.apply_btn.setIcon(icon("check-circle", theme.palette().text))
        self.apply_btn.setProperty("role", "primary")
        self.apply_btn.clicked.connect(self._apply_edits)
        self.delete_scene_btn = QPushButton(" Szene löschen")
        self.delete_scene_btn.setProperty("role", "danger")
        self.delete_scene_btn.clicked.connect(self._delete_selected_scene)
        detail_btn_row.addWidget(self.apply_btn)
        detail_btn_row.addWidget(self.delete_scene_btn)
        detail_layout.addLayout(detail_btn_row)

        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter, 1)

        self.status_label = QLabel("")
        self.status_label.setProperty("role", "muted")
        outer.addWidget(self.status_label)

        self.refresh()

    # -- generation ------------------------------------------------------
    def _generate_storyboard(self) -> None:
        proj = self.pm.project
        if proj is None:
            QMessageBox.warning(self, "Kein Projekt", "Bitte zuerst ein Projekt öffnen.")
            return
        if not proj.audio_path or not proj.full_prompt_path:
            QMessageBox.warning(
                self,
                "Dateien fehlen",
                "Bitte zuerst Audio und Video-Prompt im Projekt-Tab importieren.",
            )
            return

        audio_abs = self.pm.resolve(proj.audio_path)
        prompt_abs = self.pm.resolve(proj.full_prompt_path)

        if proj.audio_duration_seconds <= 0.0:
            analysis = analyze_audio(str(audio_abs))
            proj.audio_duration_seconds = analysis.duration_seconds
            proj.tempo_bpm = analysis.tempo_bpm
            proj.beat_times = analysis.beat_times

        prompt_text = prompt_abs.read_text(encoding="utf-8")
        parsed = parse_prompt_script(prompt_text)

        srt_lines = []
        if proj.srt_path:
            srt_lines = parse_srt_file(str(self.pm.resolve(proj.srt_path)))

        scenes = build_scenes(parsed, srt_lines, max_clip_seconds=DEFAULT_MAX_CLIP_SECONDS)
        proj.scenes = scenes
        self.refresh()
        self.status_label.setText(
            f"{len(scenes)} Szenen generiert, Gesamtdauer {proj.audio_duration_seconds:.1f}s."
        )

    def _refresh_bulk_char_combo(self) -> None:
        """Keeps the toolbar's character picker in sync with the project's
        character list, preserving the current selection where possible."""
        prev_id = self.bulk_char_combo.currentData()
        self.bulk_char_combo.blockSignals(True)
        self.bulk_char_combo.clear()
        proj = self.pm.project
        if proj is not None:
            for char in proj.characters.values():
                self.bulk_char_combo.addItem(char.name, char.id)
        self.bulk_char_combo.blockSignals(False)
        if prev_id is not None:
            idx = self.bulk_char_combo.findData(prev_id)
            if idx >= 0:
                self.bulk_char_combo.setCurrentIndex(idx)

    def _bulk_assign_character(self) -> None:
        proj = self.pm.project
        if proj is None:
            QMessageBox.warning(self, "Kein Projekt", "Bitte zuerst ein Projekt öffnen.")
            return
        char_id = self.bulk_char_combo.currentData()
        if char_id is None:
            QMessageBox.warning(
                self, "Kein Charakter", "Bitte zuerst im Tab 'Charaktere' einen Charakter anlegen."
            )
            return
        char_name = self.bulk_char_combo.currentText()
        if not proj.scenes:
            QMessageBox.warning(self, "Kein Storyboard", "Es gibt noch keine Szenen.")
            return
        confirm = QMessageBox.question(
            self, "Charakter zuweisen",
            f"'{char_name}' wirklich allen {len(proj.scenes)} Szenen zuweisen? "
            "Bereits gesetzte Charakter-Zuordnungen einzelner Szenen bleiben erhalten, "
            f"'{char_name}' wird nur ergänzt.",
        )
        if confirm != QMessageBox.Yes:
            return
        for scene in proj.scenes:
            if char_id not in scene.character_ids:
                scene.character_ids.append(char_id)
        proj.touch()
        self.refresh()
        self.status_label.setText(f"'{char_name}' wurde allen {len(proj.scenes)} Szenen zugewiesen.")

    # -- table / detail sync ----------------------------------------------
    def refresh(self) -> None:
        proj = self.pm.project
        self.table.setRowCount(0)
        if hasattr(self, "timeline_preview"):
            self.timeline_preview.refresh()
        self._refresh_bulk_char_combo()
        if proj is None:
            return
        scenes = proj.sorted_scenes()
        self.table.setRowCount(len(scenes))
        for row, scene in enumerate(scenes):
            char_names = ", ".join(
                proj.characters[cid].name for cid in scene.character_ids if cid in proj.characters
            )
            values = [
                str(scene.order),
                _fmt_time(scene.start_seconds),
                _fmt_time(scene.end_seconds),
                f"{scene.duration:.2f}s",
                scene.label,
                _STATUS_LABEL.get(scene.status, scene.status.value),
                char_names,
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self._selected_index = None
        self._clear_detail()

    def _clear_detail(self) -> None:
        self.prompt_edit.setPlainText("")
        self.notes_edit.setPlainText("")
        self.char_list.clear()
        self.continuity_label.setText("")
        self.face_warning_label.setText("")
        self.face_override_btn.setVisible(False)

    def _on_row_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        proj = self.pm.project
        if not rows or proj is None:
            self._selected_index = None
            self._clear_detail()
            return
        row = rows[0].row()
        scenes = proj.sorted_scenes()
        if row >= len(scenes):
            return
        scene = scenes[row]
        self._selected_index = scene.order
        self.prompt_edit.setPlainText(scene.prompt_text)
        self.notes_edit.setPlainText(scene.notes)

        self.char_list.clear()
        for char in proj.characters.values():
            item = QListWidgetItem(char.name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if char.id in scene.character_ids else Qt.Unchecked)
            item.setData(Qt.UserRole, char.id)
            self.char_list.addItem(item)

        self._refresh_continuity_label(scene)
        self._refresh_face_warning(scene)

    def _refresh_face_warning(self, scene) -> None:
        version = scene.accepted_version()
        face_warning = version.face_warning if version is not None else ""
        if scene.status == SceneStatus.NEEDS_REVIEW:
            similarity_note = ""
            if version is not None and version.face_similarity is not None:
                similarity_note = f" (Ähnlichkeit: {version.face_similarity:.2f})"
            self.face_warning_label.setText(
                (face_warning or "Automatische Gesichtskontrolle hat ein Problem gemeldet.")
                + similarity_note
            )
            self.face_warning_label.setProperty("role", "warning")
            self.face_override_btn.setVisible(True)
        else:
            self.face_warning_label.setText(face_warning)
            self.face_warning_label.setProperty("role", "muted")
            self.face_override_btn.setVisible(False)
        self.face_warning_label.style().unpolish(self.face_warning_label)
        self.face_warning_label.style().polish(self.face_warning_label)

    def _override_face_review(self) -> None:
        proj = self.pm.project
        if proj is None or self._selected_index is None:
            return
        scene = next((s for s in proj.scenes if s.order == self._selected_index), None)
        if scene is None:
            return
        confirm = QMessageBox.question(
            self, "Trotzdem akzeptieren",
            "Die automatische Gesichtskontrolle hat diese Szene zur Prüfung markiert. "
            "Soll der vorhandene Clip trotzdem als fertig akzeptiert und wieder in den Export "
            "aufgenommen werden? Das ist eine manuelle Entscheidung - prüfe den Clip vorher selbst.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        scene.status = SceneStatus.DONE
        proj.touch()
        self._refresh_face_warning(scene)
        self.refresh()
        self.status_label.setText("Szene manuell akzeptiert (Gesichtskontrolle überschrieben).")

    def _refresh_continuity_label(self, scene) -> None:
        if scene.continuity_reference_path:
            self.continuity_label.setText(
                f"Verknüpft: {Path(scene.continuity_reference_path).name} "
                "(wird als zweites Referenzbild gesendet - siehe Kostenfreigabe-Dialog, "
                "ob das die Szene sperrt, weil ein Charakter zusätzlich benötigt wird)."
            )
        elif continuity_candidate(self.pm, scene) is not None:
            self.continuity_label.setText(
                "Verfügbar (letztes Frame der vorherigen Szene), aber noch nicht übernommen."
            )
        else:
            self.continuity_label.setText(
                "Nicht verfügbar (vorherige Szene hat noch keinen akzeptierten Clip)."
            )

    def _attach_continuity(self) -> None:
        proj = self.pm.project
        if proj is None or self._selected_index is None:
            return
        scene = next((s for s in proj.scenes if s.order == self._selected_index), None)
        if scene is None:
            return
        confirm = QMessageBox.question(
            self, "Anschlussbild übernehmen",
            "Das Anschlussbild wird als zusätzliches Referenzbild neben einer eventuell "
            "zugewiesenen Figur gesendet. Kein aktueller Anbieter kann zwei Referenzbilder "
            "gleichzeitig verarbeiten - hat diese Szene bereits eine Figur, wird sie dadurch "
            "gesperrt, bis der mehrstufige Schlüsselbild-Workflow existiert. Trotzdem übernehmen?",
        )
        if confirm != QMessageBox.Yes:
            return
        ok, message = attach_continuity_reference(self.pm, scene)
        self._refresh_continuity_label(scene)
        self.status_label.setText(message)
        if not ok:
            QMessageBox.warning(self, "Kein Anschlussbild verfügbar", message)

    def _detach_continuity(self) -> None:
        proj = self.pm.project
        if proj is None or self._selected_index is None:
            return
        scene = next((s for s in proj.scenes if s.order == self._selected_index), None)
        if scene is None:
            return
        detach_continuity_reference(scene)
        self._refresh_continuity_label(scene)
        self.status_label.setText("Anschlussbild entfernt.")

    def _apply_edits(self) -> None:
        proj = self.pm.project
        if proj is None or self._selected_index is None:
            return
        scene = next((s for s in proj.scenes if s.order == self._selected_index), None)
        if scene is None:
            return
        scene.prompt_text = self.prompt_edit.toPlainText()
        scene.notes = self.notes_edit.toPlainText()
        checked_ids = []
        for i in range(self.char_list.count()):
            item = self.char_list.item(i)
            if item.checkState() == Qt.Checked:
                checked_ids.append(item.data(Qt.UserRole))
        scene.character_ids = checked_ids
        self.refresh()

    def _delete_selected_scene(self) -> None:
        proj = self.pm.project
        if proj is None or self._selected_index is None:
            return
        scene = next((s for s in proj.scenes if s.order == self._selected_index), None)
        if scene is None:
            return
        confirm = QMessageBox.question(
            self, "Szene löschen",
            f"Szene [{scene.order}] '{scene.label}' wirklich löschen? Zugehörige generierte Clips bleiben "
            "auf der Festplatte, werden aber nicht mehr im Export verwendet.",
        )
        if confirm != QMessageBox.Yes:
            return
        proj.scenes.remove(scene)
        self.refresh()
