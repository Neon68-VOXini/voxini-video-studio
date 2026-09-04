"""Generierung tab: the hybrid local/cloud generation cockpit.

Provider choice works on two levels, exactly per the binding hybrid spec:
- a project-wide default (top toolbar "Projekt-Standard") - defaults to
  ComfyUI (lokal, kostenlos) for every new project;
- a per-scene override (the "Anbieter" column in the table) - lets a
  handful of difficult scenes go to Runway while everything else stays
  local/free, without ever switching the whole project.

Every row is always labeled "Lokal - kostenlos", "Runway - kostenpflichtig"
or "Mock - Testmodus" so cost is never ambiguous, and the shared
CostConfirmDialog is the only path that can trigger an actual (mocked
during tests, real if the user has a key) paid Runway call - there is no
automatic local->Runway fallback anywhere in this file."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from voxini_studio.core.environment_check import (
    check_comfyui_connection,
    find_pid_listening_on_port,
    kill_process_tree,
)
from voxini_studio.core.generation_service import estimate_costs, generate_scene, resolve_provider
from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.models.project import Scene, SceneStatus
from voxini_studio.providers.registry import available_providers, get_provider
from voxini_studio.ui import theme
from voxini_studio.ui.cost_confirm_dialog import CostConfirmDialog
from voxini_studio.ui.icons import icon

_STATUS_LABEL = {
    SceneStatus.PLANNED: "geplant",
    SceneStatus.QUEUED: "in Warteschlange",
    SceneStatus.GENERATING: "wird generiert",
    SceneStatus.DONE: "fertig",
    SceneStatus.FAILED: "fehlgeschlagen",
    SceneStatus.REJECTED: "abgelehnt",
}

_PROVIDER_ICON = {"comfyui": "cpu", "runway": "cloud", "mock": "wand"}
_PROVIDER_BADGE_KIND = {"comfyui": "free", "runway": "paid", "mock": "test"}
_PROVIDER_BADGE_TEXT = {
    "comfyui": "Lokal - kostenlos",
    "runway": "Runway - kostenpflichtig",
    "mock": "Mock - Testmodus",
}

_AUTO_LABEL = "Automatisch (Projekt-/Szene-Einstellung)"
_COLUMNS = ["", "#", "Label", "Dauer", "Anbieter", "Kostenlabel", "Status", "Geschätzte Kosten"]


def _badge_item(provider_id: str) -> QTableWidgetItem:
    item = QTableWidgetItem(_PROVIDER_BADGE_TEXT.get(provider_id, provider_id))
    kind = _PROVIDER_BADGE_KIND.get(provider_id, "test")
    p = theme.palette()
    color = {"free": p.success, "paid": p.gold, "test": p.muted}[kind]
    from PySide6.QtGui import QColor

    item.setForeground(QColor(color))
    return item


class _GenerationWorker(QObject):
    """Runs generate_scene() for a batch of scenes on a background thread.

    generate_scenes() used to be called directly on the Qt main thread,
    which blocks the whole event loop for the entire batch - for a local
    ComfyUI job that can be many minutes per scene, Windows shows the
    window as "(Keine Rueckmeldung)" and no per-scene status is visible
    until everything finishes. Running the same synchronous provider calls
    here, on a worker QObject moved to a QThread, keeps the UI responsive
    and lets the panel show live per-scene progress via queued signals
    (the default for cross-thread Qt signal/slot connections, so the
    connected slots below always run safely on the receiving UI thread)."""

    sceneStarted = Signal(int)  # scene.order, about to start
    sceneFinished = Signal(int)  # scene.order, just finished (success or fail)
    statusMessage = Signal(str)  # free-text progress update (e.g. ComfyUI restart)
    allFinished = Signal(int, int, int, int)  # n_ok, n_fail, n_budget_blocked, n_skipped

    def __init__(
        self,
        pm: ProjectManager,
        scenes: list[Scene],
        provider: Optional[object],
        model_id: str = "",
    ) -> None:
        super().__init__()
        self.pm = pm
        self.scenes = scenes
        self.provider = provider
        self.model_id = model_id

    def _scene_provider_id(self, scene: Scene) -> str:
        if self.provider is not None:
            return self.provider.id
        return resolve_provider(self.pm, scene).id

    def _restart_comfyui(self, proj) -> bool:
        """Fully kills and relaunches the ComfyUI process, instead of only
        calling POST /free between jobs. Root cause (confirmed live via a
        real batch run + Task-Manager on 2026-09-03): on this AMD/ROCm
        setup, PyTorch's caching allocator can't return all fragmented VRAM
        to the driver within one long-running process - ROCm doesn't support
        CUDA's 'expandable_segments' allocator feature (visible as a
        UserWarning in ComfyUI's own console) - so a long unattended batch
        gets progressively slower even though /free correctly unloads every
        model each time (observed: 14.1/16 GB VRAM reserved at only 5% GPU
        load after just 2 scenes). A full process restart is the only way
        to guarantee a clean VRAM state. See
        Project.comfyui_restart_every_n_scenes."""
        if not proj.comfyui_install_dir:
            self.statusMessage.emit(
                "ComfyUI-Neustart übersprungen: kein Installationspfad im Projekt gesetzt."
            )
            return True  # nothing we can do - keep going with /free only
        bat_path = Path(proj.comfyui_install_dir).parent / "START_COMFYUI.bat"
        if not bat_path.exists():
            self.statusMessage.emit(
                f"ComfyUI-Neustart übersprungen: {bat_path} nicht gefunden."
            )
            return True

        self.statusMessage.emit("ComfyUI wird zur Speicherbereinigung neu gestartet ...")
        pid = find_pid_listening_on_port(proj.comfyui_port)
        if pid is not None:
            kill_process_tree(pid)
            time.sleep(3.0)  # give Windows a moment to actually release the port

        try:
            subprocess.Popen(
                [str(bat_path)], cwd=str(bat_path.parent),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        except Exception as exc:
            self.statusMessage.emit(f"ComfyUI-Neustart fehlgeschlagen: {exc}")
            return False

        deadline = time.monotonic() + 180.0
        while time.monotonic() < deadline:
            conn = check_comfyui_connection(proj.comfyui_host, proj.comfyui_port, timeout=2.0)
            if conn.reachable:
                self.statusMessage.emit("ComfyUI neu gestartet - Generierung läuft weiter ...")
                return True
            time.sleep(2.0)
        self.statusMessage.emit(
            "ComfyUI wurde nach dem Neustart innerhalb von 180s nicht erreichbar - Lauf wird angehalten."
        )
        return False

    def run(self) -> None:
        n_ok = 0
        n_fail = 0
        n_budget_blocked = 0
        n_skipped = 0
        proj = self.pm.project
        restart_every = proj.comfyui_restart_every_n_scenes if proj else 0
        comfyui_scenes_since_restart = 0

        for i, scene in enumerate(self.scenes):
            # generate_scene() only flips scene.status to GENERATING deep
            # inside its own body, right before the (potentially many
            # minutes long) provider.generate() call - set it here too,
            # before emitting sceneStarted, so a refresh() triggered by the
            # signal shows "wird generiert" immediately instead of the old
            # status for the whole duration of the job. generate_scene()
            # then either confirms it (sets GENERATING again, harmless) or
            # overrides it straight to FAILED if the Runway budget gate
            # blocks the job before ever calling the provider.
            scene.status = SceneStatus.GENERATING
            self.sceneStarted.emit(scene.order)
            provider_id = self._scene_provider_id(scene)
            try:
                version = generate_scene(self.pm, scene, self.provider, self.model_id)
            except Exception as exc:
                # Safety net: generate_scene()/the provider have no top-level
                # try/except of their own, so any unexpected exception (e.g.
                # a malformed workflow template) used to propagate out of
                # this slot entirely - which PySide6 only prints to stderr,
                # never emits sceneFinished/allFinished, and leaves the
                # QThread's event loop running forever with the UI frozen in
                # "wird generiert" and no visible error. Catching it here
                # turns it into a normal failed scene instead.
                scene.status = SceneStatus.FAILED
                n_fail += 1
            else:
                if version.error_message:
                    n_fail += 1
                    if "Budget" in version.error_message:
                        n_budget_blocked += 1
                else:
                    n_ok += 1
            self.sceneFinished.emit(scene.order)

            # Scheduled full ComfyUI restart (see Project.
            # comfyui_restart_every_n_scenes / _restart_comfyui) - only
            # counts scenes that actually used the local provider, and only
            # bothers restarting if there's still a comfyui-based scene left
            # to run afterwards.
            is_last = i == len(self.scenes) - 1
            if provider_id == "comfyui" and restart_every > 0 and not is_last:
                comfyui_scenes_since_restart += 1
                if comfyui_scenes_since_restart >= restart_every:
                    comfyui_scenes_since_restart = 0
                    if not self._restart_comfyui(proj):
                        n_skipped = len(self.scenes) - (i + 1)
                        break
        self.allFinished.emit(n_ok, n_fail, n_budget_blocked, n_skipped)


class GenerationPanel(QWidget):
    def __init__(self, pm: ProjectManager, on_changed=None) -> None:
        super().__init__()
        self.pm = pm
        self.on_changed = on_changed or (lambda: None)

        # background-generation state (see _GenerationWorker) - kept as
        # plain attributes rather than local variables so the queued
        # cross-thread signal slots below can reach them
        self._thread: QThread | None = None
        self._worker: _GenerationWorker | None = None
        self._total_in_run = 0
        self._done_in_run = 0
        self._last_run_summary = ""

        outer = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Projekt-Standardanbieter:"))
        self.default_provider_combo = QComboBox()
        for provider in available_providers():
            self.default_provider_combo.addItem(
                icon(_PROVIDER_ICON.get(provider.id, "cpu"), theme.palette().text),
                provider.display_name,
                provider.id,
            )
        self.default_provider_combo.currentIndexChanged.connect(self._on_default_provider_changed)
        toolbar.addWidget(self.default_provider_combo)

        toolbar.addSpacing(16)
        toolbar.addWidget(QLabel("Anbieter für diesen Lauf erzwingen:"))
        self.run_provider_combo = QComboBox()
        self.run_provider_combo.addItem(_AUTO_LABEL, None)
        for provider in available_providers():
            self.run_provider_combo.addItem(
                icon(_PROVIDER_ICON.get(provider.id, "cpu"), theme.palette().text),
                provider.display_name,
                provider.id,
            )
        toolbar.addWidget(self.run_provider_combo)
        toolbar.addStretch(1)
        outer.addLayout(toolbar)

        actions = QHBoxLayout()
        self.gen_selected_btn = QPushButton(" Ausgewählte generieren")
        self.gen_selected_btn.setIcon(icon("play", "#ffffff"))
        self.gen_selected_btn.setProperty("role", "primary")
        self.gen_selected_btn.clicked.connect(self._generate_selected)
        self.gen_open_btn = QPushButton(" Alle offenen generieren")
        self.gen_open_btn.setIcon(icon("play", theme.palette().text))
        self.gen_open_btn.clicked.connect(self._generate_all_open)
        self.retry_btn = QPushButton(" Fehlgeschlagene/abgelehnte erneut versuchen")
        self.retry_btn.setIcon(icon("refresh-cw", theme.palette().text))
        self.retry_btn.clicked.connect(self._retry_failed)
        actions.addWidget(self.gen_selected_btn)
        actions.addWidget(self.gen_open_btn)
        actions.addWidget(self.retry_btn)
        actions.addStretch(1)
        outer.addLayout(actions)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        outer.addWidget(self.table, 1)

        self.status_label = QLabel("")
        self.status_label.setProperty("role", "muted")
        outer.addWidget(self.status_label)

        self.refresh()

    # -- helpers ------------------------------------------------------
    def _current_run_provider(self):
        """The provider explicitly forced for the next batch run, or None
        to auto-resolve per scene (project default / scene override) -
        the normal hybrid path."""
        provider_id = self.run_provider_combo.currentData()
        if provider_id is None:
            return None
        return get_provider(provider_id)

    def _on_default_provider_changed(self) -> None:
        proj = self.pm.project
        if proj is None:
            return
        provider_id = self.default_provider_combo.currentData()
        if provider_id:
            proj.default_provider_id = provider_id
            self.refresh()

    def refresh(self) -> None:
        proj = self.pm.project
        self.table.setRowCount(0)
        if proj is None:
            return

        idx = self.default_provider_combo.findData(proj.default_provider_id)
        if idx >= 0 and idx != self.default_provider_combo.currentIndex():
            self.default_provider_combo.blockSignals(True)
            self.default_provider_combo.setCurrentIndex(idx)
            self.default_provider_combo.blockSignals(False)

        scenes = proj.sorted_scenes()
        self.table.setRowCount(len(scenes))
        for row, scene in enumerate(scenes):
            checkbox_item = QTableWidgetItem()
            checkbox_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            checkbox_item.setCheckState(Qt.Unchecked)
            checkbox_item.setData(Qt.UserRole, scene.order)
            self.table.setItem(row, 0, checkbox_item)

            resolved_provider = resolve_provider(self.pm, scene)
            cost = resolved_provider.estimate_cost(scene.duration)

            values = [
                str(scene.order),
                scene.label,
                f"{scene.duration:.2f}s",
            ]
            for col, value in enumerate(values, start=1):
                self.table.setItem(row, col, QTableWidgetItem(value))

            # per-scene provider override combo (col 4)
            override_combo = QComboBox()
            override_combo.addItem(_AUTO_LABEL, None)
            for provider in available_providers():
                override_combo.addItem(provider.display_name, provider.id)
            sel_idx = override_combo.findData(scene.provider_override)
            override_combo.setCurrentIndex(sel_idx if sel_idx >= 0 else 0)
            override_combo.currentIndexChanged.connect(
                lambda _i, s=scene, c=override_combo: self._on_scene_override_changed(s, c)
            )
            self.table.setCellWidget(row, 4, override_combo)

            self.table.setItem(row, 5, _badge_item(resolved_provider.id))

            # Status column also carries a warning icon+tooltip when the
            # most recent generation attempt had more usable character
            # reference images than any provider actually sends (see
            # generation_service.generate_scene() / ClipVersion.
            # reference_warning) - otherwise this silently drops identity
            # information with zero visible trace in the UI.
            status_item = QTableWidgetItem(_STATUS_LABEL.get(scene.status, scene.status.value))
            last_version = scene.versions[-1] if scene.versions else None
            if last_version and last_version.reference_warning:
                status_item.setIcon(icon("alert-triangle", theme.palette().gold))
                status_item.setToolTip(last_version.reference_warning)
            self.table.setItem(row, 6, status_item)

            self.table.setItem(row, 7, QTableWidgetItem(f"{cost:.2f} €"))

    def _on_scene_override_changed(self, scene: Scene, combo: QComboBox) -> None:
        scene.provider_override = combo.currentData()
        # only the badge/cost columns need to change, a full refresh is fine
        # here since batches are small (per-song scene counts, not huge lists)
        self.refresh()

    def _checked_scene_orders(self) -> list[int]:
        orders = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.Checked:
                orders.append(item.data(Qt.UserRole))
        return orders

    def _scenes_by_orders(self, orders: list[int]):
        proj = self.pm.project
        if proj is None:
            return []
        by_order = {s.order: s for s in proj.scenes}
        return [by_order[o] for o in orders if o in by_order]

    # -- generation actions -------------------------------------------
    def _ensure_comfyui_running(self, scenes, forced_provider) -> bool:
        """If this run would use the local ComfyUI provider and ComfyUI
        isn't reachable yet, offers to start comfyui_env/START_COMFYUI.bat
        automatically and waits (with a cancellable progress dialog) until
        it responds - instead of letting every scene in the run just fail
        with a connection error a few seconds later. Returns True if it's
        safe to proceed (ComfyUI reachable, or not needed for this run),
        False if the run should be aborted."""
        proj = self.pm.project
        if proj is None:
            return True
        if forced_provider is not None:
            uses_comfyui = forced_provider.id == "comfyui"
        else:
            uses_comfyui = any(resolve_provider(self.pm, s).id == "comfyui" for s in scenes)
        if not uses_comfyui:
            return True

        conn = check_comfyui_connection(proj.comfyui_host, proj.comfyui_port, timeout=3.0)
        if conn.reachable:
            return True

        reply = QMessageBox.question(
            self, "ComfyUI läuft nicht",
            "ComfyUI ist gerade nicht erreichbar, wird aber für diesen Lauf benötigt.\n\n"
            "Jetzt automatisch starten?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return False

        if not proj.comfyui_install_dir:
            QMessageBox.warning(
                self, "ComfyUI-Pfad unbekannt",
                "Der ComfyUI-Installationspfad ist im Projekt nicht gesetzt "
                "(Einrichtungsassistent -> Lokale Installation). Bitte dort "
                "einmal einrichten oder ComfyUI manuell starten.",
            )
            return False

        # comfyui_install_dir points at .../comfyui_env/ComfyUI - the
        # launcher script generated by INSTALL_LOCAL_AI.bat lives one level
        # up, at .../comfyui_env/START_COMFYUI.bat.
        bat_path = Path(proj.comfyui_install_dir).parent / "START_COMFYUI.bat"
        if not bat_path.exists():
            QMessageBox.warning(
                self, "START_COMFYUI.bat nicht gefunden",
                f"Erwartet unter:\n{bat_path}\n\nBitte ComfyUI manuell starten.",
            )
            return False

        try:
            subprocess.Popen(
                [str(bat_path)], cwd=str(bat_path.parent),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Start fehlgeschlagen", f"Konnte ComfyUI nicht starten:\n{exc}")
            return False

        return self._wait_for_comfyui(proj.comfyui_host, proj.comfyui_port)

    def _wait_for_comfyui(self, host: str, port: int, timeout_seconds: float = 180.0) -> bool:
        progress = QProgressDialog(
            "Warte auf ComfyUI-Start ... (Konsolenfenster prüfen, falls das lange dauert)",
            "Abbrechen", 0, 0, self,
        )
        progress.setWindowTitle("ComfyUI startet")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        state = {"ok": False, "elapsed": 0.0}
        interval = 2.0

        def poll() -> None:
            if progress.wasCanceled():
                timer.stop()
                progress.close()
                return
            conn = check_comfyui_connection(host, port, timeout=2.0)
            if conn.reachable:
                state["ok"] = True
                timer.stop()
                progress.close()
                return
            state["elapsed"] += interval
            if state["elapsed"] >= timeout_seconds:
                timer.stop()
                progress.close()

        timer = QTimer(self)
        timer.setInterval(int(interval * 1000))
        timer.timeout.connect(poll)
        timer.start()
        progress.exec()
        timer.stop()

        if not state["ok"]:
            QMessageBox.warning(
                self, "ComfyUI nicht erreichbar",
                "ComfyUI ist innerhalb der Wartezeit nicht erreichbar geworden. Bitte im "
                "ComfyUI-Konsolenfenster prüfen, ob es fehlerfrei hochgefahren ist, und die "
                "Generierung danach erneut starten.",
            )
        return state["ok"]

    def _set_running(self, running: bool) -> None:
        self.gen_selected_btn.setEnabled(not running)
        self.gen_open_btn.setEnabled(not running)
        self.retry_btn.setEnabled(not running)

    def _run_generation(self, scenes) -> None:
        if not scenes:
            QMessageBox.information(self, "Keine Auswahl", "Bitte mindestens eine Szene auswählen.")
            return
        if self._thread is not None:
            QMessageBox.information(
                self,
                "Generierung läuft bereits",
                "Es läuft bereits eine Generierung. Bitte warten, bis sie abgeschlossen ist.",
            )
            return

        forced_provider = self._current_run_provider()
        if forced_provider is not None:
            scenes_and_costs = estimate_costs(scenes, forced_provider)
            provider_label = forced_provider.display_name
        else:
            scenes_and_costs = [(s, resolve_provider(self.pm, s).estimate_cost(s.duration)) for s in scenes]
            provider_label = "Automatisch (pro Szene: Lokal oder Runway laut Einstellung)"

        total_cost = sum(c for _, c in scenes_and_costs)
        dialog = CostConfirmDialog(self, scenes_and_costs, provider_label, "")
        if dialog.exec() != CostConfirmDialog.Accepted:
            return

        if not self._ensure_comfyui_running(scenes, forced_provider):
            return

        # Run the batch on a background thread (see _GenerationWorker) so
        # the app stays responsive and shows live per-scene status instead
        # of freezing ("Keine Rueckmeldung") for the whole run.
        self._total_in_run = len(scenes)
        self._done_in_run = 0
        self._last_run_summary = ""
        self._set_running(True)
        self.status_label.setText(f"Generierung läuft: 0/{self._total_in_run} Szenen fertig ...")

        thread = QThread(self)
        worker = _GenerationWorker(self.pm, scenes, forced_provider)
        worker.moveToThread(thread)
        self._thread = thread
        self._worker = worker

        thread.started.connect(worker.run)
        worker.sceneStarted.connect(self._on_scene_started)
        worker.sceneFinished.connect(self._on_scene_finished)
        worker.statusMessage.connect(self._on_status_message)
        worker.allFinished.connect(self._on_generation_finished)
        worker.allFinished.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        thread.start()

    def _on_scene_started(self, order: int) -> None:
        self.status_label.setText(
            f"Generierung läuft: Szene #{order} wird generiert "
            f"({self._done_in_run}/{self._total_in_run} fertig) ..."
        )
        self.refresh()

    def _on_scene_finished(self, order: int) -> None:
        self._done_in_run += 1
        self.status_label.setText(
            f"Generierung läuft: {self._done_in_run}/{self._total_in_run} Szenen fertig ..."
        )
        self.refresh()
        self.on_changed()

    def _on_status_message(self, text: str) -> None:
        self.status_label.setText(text)

    def _on_generation_finished(self, n_ok: int, n_fail: int, n_budget_blocked: int, n_skipped: int) -> None:
        msg = f"{n_ok} erfolgreich, {n_fail} fehlgeschlagen."
        if n_budget_blocked:
            msg += f" ({n_budget_blocked} durch Runway-Budgetlimit blockiert, kein Auftrag gesendet.)"
        if n_skipped:
            msg += f" {n_skipped} nicht gestartet (ComfyUI-Neustart fehlgeschlagen)."
        self._last_run_summary = msg

    def _on_thread_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None
        self._set_running(False)
        if self._last_run_summary:
            self.status_label.setText(self._last_run_summary)
            self._last_run_summary = ""
        self.refresh()
        self.on_changed()

    def _generate_selected(self) -> None:
        orders = self._checked_scene_orders()
        self._run_generation(self._scenes_by_orders(orders))

    def _generate_all_open(self) -> None:
        proj = self.pm.project
        if proj is None:
            return
        open_scenes = [s for s in proj.sorted_scenes() if s.status in (SceneStatus.PLANNED,)]
        self._run_generation(open_scenes)

    def _retry_failed(self) -> None:
        proj = self.pm.project
        if proj is None:
            return
        failed = [
            s for s in proj.sorted_scenes()
            if s.status in (SceneStatus.FAILED, SceneStatus.REJECTED)
        ]
        self._run_generation(failed)
