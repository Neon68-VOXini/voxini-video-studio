"""Einrichtungsassistent (Setup Wizard): prueft und richtet die lokale
ComfyUI/ROCm/Wan2.2-Umgebung ein, und verwaltet die optionalen
Runway-Cloud-Einstellungen (API-Schluessel, Budget, Verbindungstest) - an
einem Ort, mit durchgaengig klarer Kostenkennzeichnung.

Hard requirements this file implements literally:
- GPU/VRAM/ROCm/ComfyUI-Verbindung/Modelle/freier Speicher werden
  automatisch geprueft (environment_check.check_environment).
- Vor jedem Modell-Download werden Groesse UND Zielordner angezeigt und
  eine ausdrueckliche Bestaetigung verlangt - kein Download startet ohne
  diesen Dialog.
- Modelle werden NIE in die EXE eingebaut; der Speicherort ist beim ersten
  Start frei waehlbar (Installationsordner-Auswahl weiter unten).
- Der Runway-API-Schluessel wird ausschliesslich ueber
  core.credentials (Windows Credential Manager / keyring) gespeichert,
  nie im Projekt oder Quellcode.
- Der Verbindungstest fuer Runway ruft NUR GET /v1/organization auf - das
  generiert kein Video und kostet nichts.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from voxini_studio.core import credentials
from voxini_studio.core.environment_check import REQUIRED_MODELS, check_environment, free_disk_space_gb
from voxini_studio.core.model_downloader import DownloadError, download_file, format_size, plan_downloads
from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.providers.runway_provider import MODEL_CREDITS_PER_SEC, RunwayAPIError, RunwayProvider
from voxini_studio.ui import theme
from voxini_studio.ui.icons import icon
from voxini_studio.ui.model_download_confirm_dialog import ModelDownloadConfirmDialog


class LocalSetupPage(QWidget):
    def __init__(self, pm: ProjectManager) -> None:
        super().__init__()
        self.pm = pm
        layout = QVBoxLayout(self)

        info = QLabel(
            "Lokale Generierung ist der Standard und kostenlos: ComfyUI + AMD ROCm + Wan2.2 TI2V 5B "
            "laufen vollstaendig auf deinem eigenen PC, ohne API-Kosten."
        )
        info.setWordWrap(True)
        info.setProperty("role", "success")
        layout.addWidget(info)

        conn_box = QGroupBox("ComfyUI-Verbindung")
        conn_form = QFormLayout(conn_box)
        self.host_edit = QLineEdit()
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        conn_form.addRow("Host:", self.host_edit)
        conn_form.addRow("Port:", self.port_spin)
        self.restart_every_spin = QSpinBox()
        self.restart_every_spin.setRange(0, 100)
        self.restart_every_spin.setSpecialValueText("Aus")
        conn_form.addRow("ComfyUI automatisch neu starten alle:", self.restart_every_spin)
        restart_hint = QLabel(
            "Startet ComfyUI bei einem Stapel-Lauf nach jeweils dieser Anzahl generierter Szenen komplett "
            "neu (nicht nur Speicher freigeben), damit sich VRAM-Fragmentierung (bekannte ROCm-Einschränkung) "
            "nicht über viele Szenen aufbaut. Praktisch für lange, unbeaufsichtigte Läufe (z.B. über Nacht). "
            "0 = aus, wie bisher."
        )
        restart_hint.setWordWrap(True)
        restart_hint.setProperty("role", "muted")
        conn_form.addRow("", restart_hint)
        layout.addWidget(conn_box)

        quality_box = QGroupBox("Qualität")
        quality_form = QFormLayout(quality_box)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItem("480p (schneller)", "480p")
        self.resolution_combo.addItem("720p (Standard)", "720p")
        quality_form.addRow("Wan2.2-Auflösung:", self.resolution_combo)
        self.upscale_checkbox = QCheckBox("Lokal auf 1080p hochskalieren (ffmpeg, nach der Generierung)")
        quality_form.addRow("", self.upscale_checkbox)
        self.negative_prompt_edit = QLineEdit()
        self.negative_prompt_edit.setPlaceholderText("leer = Standard-Negativ-Prompt verwenden")
        quality_form.addRow("Negativ-Prompt (Wan2.2):", self.negative_prompt_edit)
        layout.addWidget(quality_box)

        identity_box = QGroupBox("Identitäts-Szenen-Pipeline (experimentell)")
        identity_layout = QVBoxLayout(identity_box)
        identity_info = QLabel(
            "Standardmäßig aus. Wenn an: Jede Szene mit Referenzbild wird in zwei lokalen Schritten "
            "erzeugt - zuerst komponiert SDXL + InstantID eine ganz neue Szene nach deinem Prompt und "
            "übernimmt dabei NUR das Gesicht aus dem Referenzfoto (nicht Hintergrund/Pose/Komposition), "
            "danach animiert Wan2.2 dieses neue Bild wie gewohnt. Dauert pro Szene länger (zwei Durchläufe) "
            "und braucht zusätzlich ein SDXL-Modell + den ComfyUI_InstantID-Node (einmalig, kostenlos, "
            "selbst zu installieren - nicht in VOXini enthalten)."
        )
        identity_info.setWordWrap(True)
        identity_info.setProperty("role", "muted")
        identity_layout.addWidget(identity_info)
        self.identity_scene_checkbox = QCheckBox("Identitäts-Szenen-Pipeline verwenden")
        identity_layout.addWidget(self.identity_scene_checkbox)
        identity_form = QFormLayout()
        self.identity_checkpoint_edit = QLineEdit()
        identity_form.addRow("SDXL-Checkpoint-Dateiname:", self.identity_checkpoint_edit)
        self.identity_negative_prompt_edit = QLineEdit()
        self.identity_negative_prompt_edit.setPlaceholderText("leer = Standard-Negativ-Prompt verwenden")
        identity_form.addRow("Negativ-Prompt (SDXL/InstantID):", self.identity_negative_prompt_edit)
        identity_layout.addLayout(identity_form)
        layout.addWidget(identity_box)

        dir_box = QGroupBox("Installations- und Modellordner")
        dir_form = QFormLayout(dir_box)
        dir_row = QHBoxLayout()
        self.install_dir_edit = QLineEdit()
        self.install_dir_edit.setReadOnly(True)
        pick_install_btn = QPushButton(" Ordner wählen...")
        pick_install_btn.setIcon(icon("folder-open", theme.palette().text))
        pick_install_btn.clicked.connect(self._pick_install_dir)
        dir_row.addWidget(self.install_dir_edit, 1)
        dir_row.addWidget(pick_install_btn)
        dir_form.addRow("ComfyUI-Ordner:", dir_row)

        models_row = QHBoxLayout()
        self.models_dir_edit = QLineEdit()
        self.models_dir_edit.setReadOnly(True)
        pick_models_btn = QPushButton(" Ordner wählen...")
        pick_models_btn.setIcon(icon("folder-open", theme.palette().text))
        pick_models_btn.clicked.connect(self._pick_models_dir)
        models_row.addWidget(self.models_dir_edit, 1)
        models_row.addWidget(pick_models_btn)
        dir_form.addRow("Modellordner:", models_row)
        layout.addWidget(dir_box)

        check_row = QHBoxLayout()
        check_btn = QPushButton(" Jetzt prüfen (GPU/VRAM/ROCm/ComfyUI/Modelle/Speicher)")
        check_btn.setIcon(icon("check-circle", "#ffffff"))
        check_btn.setProperty("role", "primary")
        check_btn.clicked.connect(self._run_check)
        check_row.addWidget(check_btn)
        check_row.addStretch(1)
        layout.addLayout(check_row)

        self.report = QTextEdit()
        self.report.setReadOnly(True)
        layout.addWidget(self.report, 1)

        models_box = QGroupBox("Wan2.2 TI2V 5B Modelldateien")
        models_layout = QVBoxLayout(models_box)
        self.model_rows: list[tuple[dict, QLabel]] = []
        for spec in REQUIRED_MODELS:
            row = QHBoxLayout()
            label = QLabel(f"{spec['name']}  ({spec['filename']})")
            status = QLabel("")
            status.setProperty("role", "muted")
            row.addWidget(label, 1)
            row.addWidget(status)
            models_layout.addLayout(row)
            self.model_rows.append((spec, status))

        download_row = QHBoxLayout()
        download_all_btn = QPushButton(" Fehlende Modelle herunterladen...")
        download_all_btn.setIcon(icon("download", "#ffffff"))
        download_all_btn.setProperty("role", "primary")
        download_all_btn.clicked.connect(self._download_missing_models)
        download_row.addWidget(download_all_btn)
        download_row.addStretch(1)
        models_layout.addLayout(download_row)
        models_hint = QLabel(
            "Zeigt vor dem Start EINE zusammengefasste Bestätigung mit Gesamtgröße, benötigtem "
            "Speicherplatz und Zielordner für alle noch fehlenden Dateien. Danach läuft der Download "
            "aller Dateien automatisch nacheinander durch - ohne weitere Einzelabfrage pro Datei."
        )
        models_hint.setWordWrap(True)
        models_hint.setProperty("role", "muted")
        models_layout.addWidget(models_hint)
        layout.addWidget(models_box)

        self.refresh_from_project()

    # -- project <-> UI sync -----------------------------------------------
    def refresh_from_project(self) -> None:
        proj = self.pm.project
        if proj is None:
            return
        self.host_edit.setText(proj.comfyui_host)
        self.port_spin.setValue(proj.comfyui_port)
        self.restart_every_spin.setValue(proj.comfyui_restart_every_n_scenes)
        self.install_dir_edit.setText(proj.comfyui_install_dir or "(nicht gesetzt)")
        self.models_dir_edit.setText(proj.comfyui_models_dir or "(nicht gesetzt)")
        self.identity_scene_checkbox.setChecked(proj.comfyui_identity_scene_mode)
        self.identity_checkpoint_edit.setText(proj.comfyui_identity_checkpoint)
        idx = self.resolution_combo.findData(proj.comfyui_resolution)
        self.resolution_combo.setCurrentIndex(idx if idx >= 0 else self.resolution_combo.findData("720p"))
        self.upscale_checkbox.setChecked(proj.comfyui_upscale_to_1080p)
        self.negative_prompt_edit.setText(proj.comfyui_negative_prompt)
        self.identity_negative_prompt_edit.setText(proj.comfyui_identity_negative_prompt)

    def apply_to_project(self) -> None:
        proj = self.pm.project
        if proj is None:
            return
        proj.comfyui_host = self.host_edit.text().strip() or "127.0.0.1"
        proj.comfyui_port = self.port_spin.value()
        proj.comfyui_restart_every_n_scenes = self.restart_every_spin.value()
        proj.comfyui_identity_scene_mode = self.identity_scene_checkbox.isChecked()
        proj.comfyui_identity_checkpoint = (
            self.identity_checkpoint_edit.text().strip() or "sd_xl_base_1.0.safetensors"
        )
        proj.comfyui_resolution = self.resolution_combo.currentData() or "720p"
        proj.comfyui_upscale_to_1080p = self.upscale_checkbox.isChecked()
        proj.comfyui_negative_prompt = self.negative_prompt_edit.text().strip()
        proj.comfyui_identity_negative_prompt = self.identity_negative_prompt_edit.text().strip()

    # -- actions --------------------------------------------------------
    def _pick_install_dir(self) -> None:
        proj = self.pm.project
        if proj is None:
            return
        directory = QFileDialog.getExistingDirectory(self, "ComfyUI-Ordner wählen")
        if not directory:
            return
        proj.comfyui_install_dir = directory
        if not proj.comfyui_models_dir:
            proj.comfyui_models_dir = str(Path(directory) / "models")
        self.refresh_from_project()

    def _pick_models_dir(self) -> None:
        proj = self.pm.project
        if proj is None:
            return
        directory = QFileDialog.getExistingDirectory(self, "Modellordner wählen")
        if not directory:
            return
        proj.comfyui_models_dir = directory
        self.refresh_from_project()

    def _run_check(self) -> None:
        proj = self.pm.project
        if proj is None:
            QMessageBox.warning(self, "Kein Projekt", "Bitte zuerst ein Projekt öffnen.")
            return
        self.apply_to_project()
        report = check_environment(
            comfyui_host=proj.comfyui_host,
            comfyui_port=proj.comfyui_port,
            models_dir=proj.comfyui_models_dir,
            comfyui_install_dir=proj.comfyui_install_dir,
        )
        lines = []
        lines.append(f"GPU erkannt: {'Ja - ' + report.gpu.name if report.gpu.detected else 'Nein'}")
        if report.gpu.detected:
            lines.append(f"  VRAM: {report.gpu.vram_gb} GB (Quelle: {report.gpu.source})")
        lines.append(f"ROCm installiert: {'Ja (' + report.rocm.version + ')' if report.rocm.installed else 'Nein'}")
        lines.append(
            f"ComfyUI erreichbar unter {proj.comfyui_host}:{proj.comfyui_port}: "
            f"{'Ja' if report.comfyui.reachable else 'Nein - ' + report.comfyui.message}"
        )
        lines.append(f"Freier Speicherplatz: {report.free_disk_gb} GB")
        lines.append("")
        lines.append("Modelldateien:")
        for m in report.models:
            lines.append(f"  [{'OK' if m.found else 'FEHLT'}] {m.name}")
        if report.warnings:
            lines.append("")
            lines.append("Hinweise:")
            for w in report.warnings:
                lines.append(f"  - {w}")
        lines.append("")
        lines.append(
            "Status: BEREIT für lokale Generierung." if report.ready_for_local_generation
            else "Status: NOCH NICHT bereit - siehe Hinweise oben."
        )
        self.report.setPlainText("\n".join(lines))

        for spec, status_label in self.model_rows:
            m = next((x for x in report.models if x.filename == spec["filename"]), None)
            if m and m.found:
                status_label.setText(f"vorhanden ({m.size_bytes / (1024**3):.2f} GB)")
                status_label.setProperty("role", "success")
            else:
                status_label.setText("fehlt")
                status_label.setProperty("role", "warning")
            status_label.style().unpolish(status_label)
            status_label.style().polish(status_label)

    def _download_missing_models(self) -> None:
        """EINE kombinierte Bestätigung (Gesamtgröße/Speicherbedarf/Zielordner,
        siehe ModelDownloadConfirmDialog) statt einer eigenen QMessageBox pro
        Datei - danach laedt diese Methode alle fehlenden Dateien automatisch
        nacheinander, ohne weitere Einzelabfrage je Datei."""
        proj = self.pm.project
        if proj is None:
            QMessageBox.warning(self, "Kein Projekt", "Bitte zuerst ein Projekt öffnen.")
            return
        if not proj.comfyui_models_dir:
            QMessageBox.warning(
                self, "Kein Modellordner",
                "Bitte zuerst oben einen Modellordner wählen, bevor Modelle heruntergeladen werden.",
            )
            return

        items = plan_downloads(REQUIRED_MODELS, proj.comfyui_models_dir)
        missing = [item for item in items if not item.already_present]
        if not missing:
            QMessageBox.information(self, "Nichts zu tun", "Alle Modelldateien sind bereits vorhanden.")
            return

        free_gb = free_disk_space_gb(proj.comfyui_models_dir)
        confirm = ModelDownloadConfirmDialog(self, missing, proj.comfyui_models_dir, free_gb)
        if confirm.exec() != QDialog.Accepted:
            return

        progress = QProgressDialog("", "Abbrechen", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        cancelled = False

        for index, item in enumerate(missing, start=1):
            progress.setLabelText(f"({index}/{len(missing)}) Lade {item.spec['filename']} herunter...")
            progress.setValue(0)

            def on_progress(done: int, total: int) -> None:
                if total:
                    progress.setValue(int(done / total * 100))
                QApplication.processEvents()

            def cancel_check() -> bool:
                # NOTE: on_progress() already pumps the event queue on every
                # chunk - don't ALSO do it here (siehe Kommentar im vorigen
                # Einzel-Download-Pfad, gleiche Begründung).
                return progress.wasCanceled()

            try:
                download_file(item.spec["url"], item.dest, progress_callback=on_progress, cancel_check=cancel_check)
            except DownloadError as exc:
                progress.close()
                if progress.wasCanceled():
                    QMessageBox.information(
                        self, "Abgebrochen",
                        f"Download abgebrochen bei „{item.spec['filename']}“. "
                        f"Bereits fertig heruntergeladene Dateien bleiben erhalten.",
                    )
                else:
                    QMessageBox.critical(
                        self, "Download fehlgeschlagen",
                        f"„{item.spec['filename']}“: {exc}\n\nWeitere Dateien wurden nicht mehr gestartet.",
                    )
                cancelled = True
                break

        progress.close()
        self._run_check()  # Status-Labels + Bericht mit dem neuen Ist-Stand aktualisieren
        if not cancelled:
            QMessageBox.information(self, "Fertig", f"{len(missing)} Modelldatei(en) erfolgreich heruntergeladen.")


class RunwaySetupPage(QWidget):
    def __init__(self, pm: ProjectManager) -> None:
        super().__init__()
        self.pm = pm
        layout = QVBoxLayout(self)

        warn = QLabel(
            "Runway ist ein optionaler, KOSTENPFLICHTIGER Cloud-Anbieter. Er wird niemals automatisch "
            "verwendet - nur wenn du ihn explizit für ein Projekt oder eine Szene auswählst, und nur "
            "nach ausdrücklicher Kostenbestätigung vor jedem einzelnen Auftrag."
        )
        warn.setWordWrap(True)
        warn.setProperty("role", "warning")
        layout.addWidget(warn)

        key_box = QGroupBox("API-Schlüssel (sicher gespeichert über Windows Credential Manager)")
        key_form = QFormLayout(key_box)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-...")
        key_form.addRow("API-Schlüssel:", self.key_edit)

        key_btns = QHBoxLayout()
        save_key_btn = QPushButton(" Schlüssel speichern")
        save_key_btn.setIcon(icon("key", theme.palette().text))
        save_key_btn.clicked.connect(self._save_key)
        delete_key_btn = QPushButton(" Schlüssel löschen")
        delete_key_btn.setProperty("role", "danger")
        delete_key_btn.clicked.connect(self._delete_key)
        test_btn = QPushButton(" Verbindung testen (kostenlos)")
        test_btn.setIcon(icon("link", theme.palette().text))
        test_btn.clicked.connect(self._test_connection)
        key_btns.addWidget(save_key_btn)
        key_btns.addWidget(delete_key_btn)
        key_btns.addWidget(test_btn)
        key_form.addRow("", key_btns)

        self.key_status = QLabel("")
        self.key_status.setProperty("role", "muted")
        key_form.addRow("", self.key_status)
        layout.addWidget(key_box)

        settings_box = QGroupBox("Modell und Budget")
        settings_form = QFormLayout(settings_box)
        self.model_combo = QComboBox()
        for model_id, credits_per_sec in MODEL_CREDITS_PER_SEC.items():
            self.model_combo.addItem(f"{model_id}  (~{credits_per_sec * 0.01:.2f} €/s)", model_id)
        settings_form.addRow("Modell:", self.model_combo)

        self.budget_spin = QDoubleSpinBox()
        self.budget_spin.setRange(0.0, 100000.0)
        self.budget_spin.setDecimals(2)
        self.budget_spin.setSuffix(" €")
        self.budget_spin.setSpecialValueText("Kein Limit gesetzt")
        settings_form.addRow("Budgetlimit:", self.budget_spin)

        self.spent_label = QLabel("")
        self.spent_label.setProperty("role", "gold")
        settings_form.addRow("Bisher ausgegeben:", self.spent_label)
        layout.addWidget(settings_box)

        layout.addStretch(1)
        self.refresh_from_project()

    def refresh_from_project(self) -> None:
        proj = self.pm.project
        self.key_status.setText(
            "Gespeichert (Fallback-Datei, nicht Windows Credential Manager)"
            if credentials.using_fallback_store() and credentials.get_runway_api_key()
            else "Gespeichert (Windows Credential Manager)" if credentials.get_runway_api_key()
            else "Kein Schlüssel gespeichert"
        )
        if proj is None:
            return
        idx = self.model_combo.findData(proj.runway_model_id)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        self.budget_spin.setValue(proj.runway_budget_limit)
        self.spent_label.setText(f"{proj.runway_spent_total:.2f} €")

    def apply_to_project(self) -> None:
        proj = self.pm.project
        if proj is None:
            return
        proj.runway_model_id = self.model_combo.currentData() or "gen4_turbo"
        proj.runway_budget_limit = self.budget_spin.value()

    def _save_key(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            QMessageBox.warning(self, "Kein Schlüssel", "Bitte einen API-Schlüssel eingeben.")
            return
        credentials.set_runway_api_key(key)
        self.key_edit.clear()
        self.refresh_from_project()
        QMessageBox.information(self, "Gespeichert", "Der Runway API-Schlüssel wurde sicher gespeichert.")

    def _delete_key(self) -> None:
        credentials.delete_runway_api_key()
        self.refresh_from_project()

    def _test_connection(self) -> None:
        # tests whatever key is currently typed (if any), else the stored one -
        # calls ONLY GET /v1/organization, never generates a video, never costs anything
        typed = self.key_edit.text().strip()
        provider = RunwayProvider(api_key=typed or None)
        try:
            info = provider.check_connection()
        except RunwayAPIError as exc:
            QMessageBox.critical(self, "Verbindung fehlgeschlagen", str(exc))
            return
        balance = info.get("creditBalance", "?")
        QMessageBox.information(
            self, "Verbindung erfolgreich",
            f"Verbindung zu Runway erfolgreich hergestellt.\nGuthaben: {balance} Credits.",
        )


class SetupWizardDialog(QDialog):
    def __init__(self, parent, pm: ProjectManager) -> None:
        super().__init__(parent)
        self.pm = pm
        self.setWindowTitle("Einrichtungsassistent")
        layout = QVBoxLayout(self)

        self.local_page = LocalSetupPage(pm)
        self.runway_page = RunwaySetupPage(pm)

        def _scrollable(w: QWidget) -> QScrollArea:
            # Each page's own content (report box, model list, etc.) can grow
            # taller than most screens - without this, Qt would force the
            # whole dialog to that minimum height, making it impossible to
            # shrink the window or even reach the OK/Cancel buttons on
            # smaller displays. Wrapping in a resizable scroll area keeps
            # the DIALOG small while the PAGE CONTENT scrolls internally.
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setWidget(w)
            area.setFrameShape(QScrollArea.NoFrame)
            return area

        tabs = QTabWidget()
        tabs.addTab(_scrollable(self.local_page), "Lokal (ComfyUI) - kostenlos")
        tabs.addTab(_scrollable(self.runway_page), "Cloud (Runway) - kostenpflichtig")
        layout.addWidget(tabs, 1)

        # Size the dialog to fit comfortably within the actual screen instead
        # of a fixed 760x640 - on smaller/scaled displays that fixed size was
        # itself part of what pushed the OK/Cancel buttons off-screen.
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        target_w, target_h = 760, 640
        if avail is not None:
            target_w = min(target_w, avail.width() - 80)
            target_h = min(target_h, avail.height() - 80)
        self.resize(target_w, target_h)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(" Übernehmen und schließen")
        buttons.button(QDialogButtonBox.Ok).setProperty("role", "primary")
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        self.local_page.apply_to_project()
        self.runway_page.apply_to_project()
        if self.pm.project is not None:
            self.pm.save()
        self.accept()
