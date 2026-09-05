"""Modal shown before any (simulated or, in a later phase, real paid)
generation call - lists every scene's prompt, chosen provider/model,
estimated cost, AND the reference-binding review protocol required by the
binding reference rule (docs/Referenzbindung_Luecken_und_Plan.md,
"Verbindliche Entscheidungen" Punkt 9): which characters are recognized,
which actual reference image(s) will be sent, the wardrobe state used, and
whether a continuity ("Anschlussbild") frame is available - so Neon68 sees
exactly what identity information a job will use BEFORE confirming it, not
only a prompt excerpt and a price.

A scene that generation_service.resolve_scene_references() would hard-block
(missing/non-transmittable reference, see Punkt 1-3) is shown here as
GESPERRT with the concrete reason, and is excluded from the cost total and
from the confirmed count - confirming this dialog only ever commits to the
scenes that are actually generatable, it never silently attempts (and then
fails) a blocked one. All of that decision logic lives in
generation_service.build_cost_review() (Qt-free, unit-testable) - this
dialog only renders its result."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout

from voxini_studio.core.generation_service import SceneReviewItem, build_cost_review
from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.models.project import Scene
from voxini_studio.ui.icons import icon


def _short(path_str: str) -> str:
    """Just the filename, for a compact display - the dialog is about
    WHICH image and WHOSE wardrobe/view state, not the full project path."""
    return Path(path_str).name


def _format_item(item: SceneReviewItem) -> str:
    scene = item.scene
    lines = [
        f"[{scene.order}] {scene.label}  ({scene.duration:.2f}s, {item.cost:.2f} €)",
        f"Charaktere: {', '.join(item.character_names) if item.character_names else 'keine'}",
    ]
    if item.blocked:
        lines.append(f"GESPERRT - wird NICHT generiert: {item.block_reason}")
    else:
        lines.append(
            "Referenzbilder: "
            + (", ".join(_short(p) for p in item.reference_paths) if item.reference_paths else "keine (Szene ohne Charakter)")
        )
        if item.wardrobe_states:
            bits = ", ".join(f"{name}={state}" for name, state in item.wardrobe_states.items())
            lines.append(f"Kleidungszustand: {bits}")
        lines.append("Anschlussbild vorhanden: " + ("ja" if item.continuity_available else "nein"))
    lines.append(scene.prompt_text[:280])
    return "\n".join(lines)


class CostConfirmDialog(QDialog):
    def __init__(
        self,
        parent,
        pm: ProjectManager,
        scenes_and_costs: list[tuple[Scene, float]],
        provider_name: str,
        model_id: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Generierung bestätigen")
        self.resize(720, 560)
        layout = QVBoxLayout(self)

        report = build_cost_review(pm, scenes_and_costs)
        total_count = len(report.items)
        allowed_count = total_count - report.blocked_count

        total_label_text = f"{total_count} Szene(n) ausgewaehlt, davon {allowed_count} generierbar"
        if report.blocked_count:
            total_label_text += f" und {report.blocked_count} gesperrt (siehe unten, werden uebersprungen)"
        total_label_text += f". Geschaetzte Gesamtkosten (nur generierbare Szenen): {report.allowed_cost_total:.2f} €"

        provider_label = QLabel(f"Anbieter: {provider_name}    Modell: {model_id or '-'}")
        provider_label.setProperty("role", "muted")
        layout.addWidget(provider_label)
        header = QLabel(total_label_text)
        header.setProperty("role", "gold" if report.allowed_cost_total > 0 else "success")
        header.setWordWrap(True)
        layout.addWidget(header)

        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setPlainText("\n\n".join(_format_item(item) for item in report.items))
        layout.addWidget(preview, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        if total_count and allowed_count == 0:
            # every selected scene is blocked - nothing to confirm at all,
            # per Punkt 2/3 ("sichtbare Figur ohne Referenz sperrt den
            # Auftrag vollstaendig") this must not offer a no-op confirm.
            ok_button.setEnabled(False)
            ok_button.setText(" Alle Szenen gesperrt")
        else:
            ok_button.setText(" Generierung bestätigen")
        ok_button.setIcon(icon("check-circle", "#ffffff"))
        ok_button.setProperty("role", "primary")
        buttons.button(QDialogButtonBox.Cancel).setText(" Abbrechen")
        buttons.button(QDialogButtonBox.Cancel).setIcon(icon("x-circle", "#ffffff"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
