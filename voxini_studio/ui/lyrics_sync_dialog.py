"""Songtext-zu-SRT: Nutzer tippt nur den Songtext ein, VOXini gleicht ihn
automatisch mit der bereits importierten Audiodatei ab (Vocal-Isolation +
Spracherkennung + Fuzzy-Zeitabgleich, siehe core/lyrics_align.py) und zeigt
das Ergebnis anschliessend in einer editierbaren Korrekturansicht, bevor es
wie eine normal importierte SRT-Datei ins Projekt uebernommen wird.
"""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from voxini_studio.core import lyrics_align, lyrics_align_env
from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.core.srt_parser import parse_srt_file
from voxini_studio.ui import theme
from voxini_studio.ui.icons import icon

_LANGUAGES = [("Deutsch", "de"), ("Englisch", "en"), ("Automatisch erkennen", "")]


class _LyricsSyncWorker(QObject):
    """Fuehrt lyrics_align.align_lyrics_to_audio() auf einem Hintergrund-
    Thread aus (gleiches Muster wie _GenerationWorker in generation_panel.py),
    damit das mehrere Minuten dauernde Environment-Setup/die Transkription
    die UI nicht einfriert."""

    progress = Signal(int, str)
    finished = Signal(str)  # Pfad der erzeugten SRT-Datei
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, lyrics_text: str, audio_path: Path, dest_srt_path: Path, language: str, whisper_model: str) -> None:
        super().__init__()
        self.lyrics_text = lyrics_text
        self.audio_path = audio_path
        self.dest_srt_path = dest_srt_path
        self.language = language
        self.whisper_model = whisper_model
        self.cancel_event = threading.Event()

    def run(self) -> None:
        try:
            result = lyrics_align.align_lyrics_to_audio(
                self.lyrics_text, self.audio_path, self.dest_srt_path,
                language=self.language, whisper_model=self.whisper_model,
                progress=lambda pct, stage: self.progress.emit(pct, stage),
                cancel_event=self.cancel_event,
            )
        except lyrics_align.LyricsAlignCancelled:
            self.cancelled.emit()
        except lyrics_align.LyricsAlignError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - Sicherheitsnetz, siehe generation_panel.py-Vorbild
            self.failed.emit(f"Unerwarteter Fehler: {exc}")
        else:
            self.finished.emit(str(result))


class LyricsSyncDialog(QDialog):
    def __init__(self, parent, pm: ProjectManager) -> None:
        super().__init__(parent)
        self.pm = pm
        self.setWindowTitle("Songtext automatisch synchronisieren")
        self.resize(760, 620)

        self._thread: QThread | None = None
        self._worker: _LyricsSyncWorker | None = None
        self._progress_dialog: QProgressDialog | None = None
        self._generated_srt_path: Path | None = None

        outer = QVBoxLayout(self)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        self.stack.addWidget(self._build_input_page())
        self.stack.addWidget(self._build_correction_page())
        self.stack.setCurrentIndex(0)

    # -- Seite 1: Songtext eingeben --------------------------------------
    def _build_input_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        hint = QLabel(
            "Songtext hier eintippen oder einfügen. VOXini trennt den Gesang vom "
            "Instrumental, erkennt den gesungenen Text automatisch und gleicht ihn "
            "zeitlich mit der bereits importierten Audiodatei ab. Anschließend kannst "
            "du das Ergebnis in einer Korrekturansicht prüfen und anpassen, bevor es "
            "als Untertitel-Datei ins Projekt übernommen wird."
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)

        self.lyrics_edit = QPlainTextEdit()
        self.lyrics_edit.setPlaceholderText("Strophe 1\nZeile 1\nZeile 2\n...\n\nRefrain\n...")
        layout.addWidget(self.lyrics_edit, 1)

        row = QHBoxLayout()
        row.addWidget(QLabel("Sprache:"))
        self.language_combo = QComboBox()
        for label, code in _LANGUAGES:
            self.language_combo.addItem(label, code)
        row.addWidget(self.language_combo)
        row.addStretch(1)
        layout.addLayout(row)

        self.env_info_label = QLabel("")
        self.env_info_label.setWordWrap(True)
        self.env_info_label.setProperty("role", "muted")
        layout.addWidget(self.env_info_label)
        self._refresh_env_info()

        start_btn = QPushButton(" Automatisch synchronisieren")
        start_btn.setIcon(icon("wand", "#ffffff"))
        start_btn.setProperty("role", "primary")
        start_btn.clicked.connect(self._on_start_clicked)
        layout.addWidget(start_btn)

        return page

    def _refresh_env_info(self) -> None:
        info = lyrics_align_env.environment_info()
        self.env_info_label.setText(info["note"])

    # -- Seite 2: Korrekturansicht ---------------------------------------
    def _build_correction_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        hint = QLabel(
            "Ergebnis des automatischen Zeitabgleichs. Start/Ende (Sekunden) und Text "
            "können hier korrigiert werden, bevor sie als Untertitel-Datei übernommen werden."
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Start (s)", "Ende (s)", "Text"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        discard_btn = QPushButton(" Verwerfen")
        discard_btn.setIcon(icon("x-circle", theme.palette().text))
        discard_btn.clicked.connect(self._on_discard_clicked)
        row.addWidget(discard_btn)
        accept_btn = QPushButton(" Übernehmen")
        accept_btn.setIcon(icon("check-circle", "#ffffff"))
        accept_btn.setProperty("role", "primary")
        accept_btn.clicked.connect(self._on_accept_clicked)
        row.addWidget(accept_btn)
        layout.addLayout(row)

        return page

    # -- Ablauf ------------------------------------------------------------
    def _on_start_clicked(self) -> None:
        proj = self.pm.project
        if proj is None or self.pm.paths is None:
            QMessageBox.warning(self, "Kein Projekt", "Bitte zuerst ein Projekt öffnen.")
            return
        if not proj.audio_path:
            QMessageBox.warning(
                self, "Kein Audio importiert",
                "Bitte zuerst eine Audiodatei importieren (Tab 'Projekt' -> 'Audio (WAV/MP3)') "
                "- der automatische Zeitabgleich braucht die Audiodatei des Songs.",
            )
            return
        lyrics_text = self.lyrics_edit.toPlainText()
        if not lyrics_text.strip():
            QMessageBox.warning(self, "Kein Songtext", "Bitte zuerst einen Songtext eingeben.")
            return

        info = lyrics_align_env.environment_info()
        reply = QMessageBox.question(
            self, "Zeitabgleich starten",
            f"{info['note']}\n\nJetzt starten?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        audio_path = self.pm.resolve(proj.audio_path)
        language = self.language_combo.currentData()
        staging = Path(tempfile.mkdtemp(prefix="voxini_lyrics_dialog_"))
        dest_srt = staging / "auto_sync.srt"

        self._progress_dialog = QProgressDialog("Zeitabgleich wird vorbereitet ...", "Abbrechen", 0, 100, self)
        self._progress_dialog.setWindowModality(Qt.WindowModal)
        self._progress_dialog.setMinimumDuration(0)
        self._progress_dialog.setAutoClose(False)
        self._progress_dialog.setAutoReset(False)
        self._progress_dialog.canceled.connect(self._on_cancel_requested)

        self._thread = QThread(self)
        self._worker = _LyricsSyncWorker(lyrics_text, audio_path, dest_srt, language, lyrics_align.DEFAULT_WHISPER_MODEL)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.cancelled.connect(self._on_worker_cancelled)
        for signal in (self._worker.finished, self._worker.failed, self._worker.cancelled):
            signal.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()
        self._progress_dialog.show()

    def _on_thread_finished(self) -> None:
        # Gleiches Aufräum-Muster wie _GenerationWorker in generation_panel.py:
        # Worker/Thread erst nach thread.finished per deleteLater() freigeben,
        # nicht schon in den finished/failed/cancelled-Slots selbst.
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None

    def _on_cancel_requested(self) -> None:
        if self._worker is not None:
            self._worker.cancel_event.set()

    def _on_worker_progress(self, percent: int, stage: str) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.setValue(max(0, min(100, percent)))
            self._progress_dialog.setLabelText(stage)

    def _close_progress(self) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None

    def _on_worker_finished(self, srt_path_str: str) -> None:
        self._close_progress()
        self._refresh_env_info()
        srt_path = Path(srt_path_str)
        self._generated_srt_path = srt_path
        try:
            lines = parse_srt_file(str(srt_path))
        except Exception as exc:
            QMessageBox.critical(self, "Fehler", f"Erzeugte SRT-Datei konnte nicht gelesen werden:\n{exc}")
            return
        self._populate_table(lines)
        self.stack.setCurrentIndex(1)

    def _on_worker_failed(self, message: str) -> None:
        self._close_progress()
        self._refresh_env_info()
        QMessageBox.critical(self, "Zeitabgleich fehlgeschlagen", message)

    def _on_worker_cancelled(self) -> None:
        self._close_progress()
        self._refresh_env_info()

    def _populate_table(self, lines) -> None:
        self.table.setRowCount(len(lines))
        for row, line in enumerate(lines):
            start_item = QTableWidgetItem(f"{line.start_seconds:.2f}")
            end_item = QTableWidgetItem(f"{line.end_seconds:.2f}")
            text_item = QTableWidgetItem(line.text)
            self.table.setItem(row, 0, start_item)
            self.table.setItem(row, 1, end_item)
            self.table.setItem(row, 2, text_item)

    def _on_discard_clicked(self) -> None:
        self.stack.setCurrentIndex(0)

    def _on_accept_clicked(self) -> None:
        import srt as srt_lib
        from datetime import timedelta

        subtitles = []
        for row in range(self.table.rowCount()):
            try:
                start = float(self.table.item(row, 0).text())
                end = float(self.table.item(row, 1).text())
            except (ValueError, AttributeError):
                QMessageBox.warning(self, "Ungültige Zeit", f"Zeile {row + 1}: Start/Ende müssen Zahlen (Sekunden) sein.")
                return
            text = self.table.item(row, 2).text() if self.table.item(row, 2) else ""
            if end <= start:
                QMessageBox.warning(self, "Ungültige Zeit", f"Zeile {row + 1}: Ende muss nach Start liegen.")
                return
            subtitles.append(srt_lib.Subtitle(
                index=row + 1, start=timedelta(seconds=start), end=timedelta(seconds=end), content=text,
            ))

        staging = Path(tempfile.mkdtemp(prefix="voxini_lyrics_final_"))
        final_srt = staging / "songtext_synchronisiert.srt"
        final_srt.write_text(srt_lib.compose(subtitles), encoding="utf-8")

        self.pm.import_srt(final_srt)
        self.accept()
