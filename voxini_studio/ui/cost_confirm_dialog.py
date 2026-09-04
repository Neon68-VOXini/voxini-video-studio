"""Modal shown before any (simulated or, in a later phase, real paid)
generation call - lists every scene's prompt, chosen provider/model and
estimated cost, and requires explicit confirmation."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout

from voxini_studio.models.project import Scene
from voxini_studio.ui.icons import icon


class CostConfirmDialog(QDialog):
    def __init__(
        self,
        parent,
        scenes_and_costs: list[tuple[Scene, float]],
        provider_name: str,
        model_id: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Generierung bestätigen")
        self.resize(640, 480)
        layout = QVBoxLayout(self)

        total = sum(cost for _, cost in scenes_and_costs)
        provider_label = QLabel(f"Anbieter: {provider_name}    Modell: {model_id or '-'}")
        provider_label.setProperty("role", "muted")
        layout.addWidget(provider_label)
        header = QLabel(
            f"{len(scenes_and_costs)} Szene(n) zur Generierung ausgewählt. "
            f"Geschätzte Gesamtkosten: {total:.2f} €"
        )
        header.setProperty("role", "gold" if total > 0 else "success")
        layout.addWidget(header)

        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        blocks = []
        for scene, cost in scenes_and_costs:
            blocks.append(
                f"[{scene.order}] {scene.label}  ({scene.duration:.2f}s, {cost:.2f} €)\n"
                f"{scene.prompt_text[:280]}"
            )
        preview.setPlainText("\n\n".join(blocks))
        layout.addWidget(preview, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(" Generierung bestätigen")
        buttons.button(QDialogButtonBox.Ok).setIcon(icon("check-circle", "#ffffff"))
        buttons.button(QDialogButtonBox.Ok).setProperty("role", "primary")
        buttons.button(QDialogButtonBox.Cancel).setText(" Abbrechen")
        buttons.button(QDialogButtonBox.Cancel).setIcon(icon("x-circle", "#ffffff"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
