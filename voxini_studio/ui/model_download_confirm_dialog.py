"""EINE kombinierte Bestätigung fuer den vollautomatischen Download aller
noch fehlenden Wan2.2-Modelldateien: zeigt Dateiliste, Gesamtgröße,
Zielordner und verfügbaren/benötigten Speicherplatz in einem einzigen
Dialog - ersetzt die frühere Praxis, pro Datei eine eigene
QMessageBox.question()-Abfrage zu zeigen. Nach dieser einen Bestätigung
läuft der Download aller Dateien automatisch durch, ohne weitere
Einzelbestätigungen (siehe setup_wizard.py::_download_missing_models)."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout

from voxini_studio.core.model_downloader import ModelDownloadItem, format_size
from voxini_studio.ui.icons import icon


class ModelDownloadConfirmDialog(QDialog):
    def __init__(
        self,
        parent,
        missing_items: list[ModelDownloadItem],
        dest_dir: str,
        free_space_gb: float,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modell-Download bestätigen")
        self.resize(640, 420)
        layout = QVBoxLayout(self)

        total_bytes = sum(item.display_size_bytes for item in missing_items)
        needed_gb = total_bytes / (1024 ** 3)
        header = QLabel(
            f"{len(missing_items)} fehlende Modelldatei(en). "
            f"Gesamtgröße: {format_size(total_bytes)}    Zielordner: {dest_dir}"
        )
        header.setWordWrap(True)
        header.setProperty("role", "gold")
        layout.addWidget(header)

        space_ok = free_space_gb >= needed_gb + 2.0  # 2 GB Sicherheitsreserve
        space_label = QLabel(
            f"Verfügbarer Speicherplatz im Zielordner: {free_space_gb:.1f} GB "
            f"({'ausreichend' if space_ok else 'WARNUNG: knapp/nicht ausreichend'} "
            f"für {needed_gb:.1f} GB Download)"
        )
        space_label.setProperty("role", "success" if space_ok else "warning")
        layout.addWidget(space_label)

        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        blocks = []
        for item in missing_items:
            size_text = format_size(item.size_bytes) if item.size_bytes else (
                f"ca. {item.spec['approx_size_gb']:.1f} GB (nicht bestätigt, Netzwerkabfrage fehlgeschlagen)"
            )
            blocks.append(f"{item.spec['filename']}  ({size_text})\n→ {item.dest}")
        preview.setPlainText("\n\n".join(blocks))
        layout.addWidget(preview, 1)

        note = QLabel(
            "Diese Dateien werden nicht in die Anwendung eingebaut und einmalig heruntergeladen. "
            "Nach Bestätigung läuft der Download aller Dateien automatisch nacheinander durch - "
            "es folgt keine weitere Einzelabfrage pro Datei. Der Vorgang kann jederzeit über "
            "\"Abbrechen\" im Fortschrittsfenster gestoppt werden."
        )
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        ok_btn.setText(" Download starten")
        ok_btn.setIcon(icon("download", "#ffffff"))
        ok_btn.setProperty("role", "primary")
        ok_btn.setEnabled(space_ok)
        buttons.button(QDialogButtonBox.Cancel).setText(" Abbrechen")
        buttons.button(QDialogButtonBox.Cancel).setIcon(icon("x-circle", "#ffffff"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if not space_ok:
            block_note = QLabel(
                "Download-Start ist gesperrt, solange nicht genug freier Speicherplatz vorhanden ist. "
                "Bitte Speicherplatz freigeben oder einen anderen Zielordner wählen."
            )
            block_note.setWordWrap(True)
            block_note.setProperty("role", "warning")
            layout.addWidget(block_note)
