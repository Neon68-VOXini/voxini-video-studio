"""Simple, functional preview timeline: a horizontal strip of scene blocks
sized proportionally to their duration and colored by status, plus the
total runtime. Deliberately kept lightweight for the MVP - a richer
scrub/playback timeline can follow later; this already gives an at-a-glance
view of storyboard coverage and generation progress.

All colors come from the central VOXini/Neon68 theme (voxini_studio.ui.theme)
- nothing here is a hardcoded hex value, so this strip re-themes correctly
when the user switches Standard/Midnight/Graphite."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.models.project import SceneStatus
from voxini_studio.ui import theme


def _status_color(status: SceneStatus) -> QColor:
    p = theme.palette(theme.load_theme_preference())
    mapping = {
        SceneStatus.PLANNED: p.muted,
        SceneStatus.QUEUED: p.cyan,
        SceneStatus.GENERATING: p.gold,
        SceneStatus.DONE: p.success,
        SceneStatus.FAILED: p.red,
        SceneStatus.REJECTED: p.mid_grad,
    }
    return QColor(mapping.get(status, p.muted))


class TimelineStrip(QWidget):
    def __init__(self, pm: ProjectManager) -> None:
        super().__init__()
        self.pm = pm
        self.setMinimumHeight(48)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        p = theme.palette(theme.load_theme_preference())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor(p.panel2))

        proj = self.pm.project
        if proj is None or not proj.scenes:
            painter.setPen(QPen(QColor(p.muted)))
            painter.drawText(rect, Qt.AlignCenter, "Kein Storyboard")
            painter.end()
            return

        scenes = proj.sorted_scenes()
        total = max(0.001, scenes[-1].end_seconds)
        width = rect.width()
        height = rect.height()

        for scene in scenes:
            x0 = (scene.start_seconds / total) * width
            x1 = (scene.end_seconds / total) * width
            block = QRectF(x0, 4, max(1.0, x1 - x0 - 1), height - 8)
            painter.fillRect(block, _status_color(scene.status))

        painter.setPen(QPen(QColor(p.muted)))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.end()


class TimelinePreview(QWidget):
    """Composite widget: the colored strip plus a small legend/status line."""

    def __init__(self, pm: ProjectManager) -> None:
        super().__init__()
        self.pm = pm
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.strip = TimelineStrip(pm)
        layout.addWidget(self.strip)
        self.info_label = QLabel("")
        self.info_label.setProperty("role", "muted")
        layout.addWidget(self.info_label)
        self.refresh()

    def refresh(self) -> None:
        self.strip.update()
        proj = self.pm.project
        if proj is None or not proj.scenes:
            self.info_label.setText("")
            return
        scenes = proj.sorted_scenes()
        total = scenes[-1].end_seconds
        counts: dict[SceneStatus, int] = {}
        for scene in scenes:
            counts[scene.status] = counts.get(scene.status, 0) + 1
        parts = [f"{status.value}: {n}" for status, n in counts.items()]
        minutes = int(total // 60)
        seconds = total - minutes * 60
        self.info_label.setText(f"Gesamtlänge {minutes:02d}:{seconds:05.2f}  ·  " + ", ".join(parts))
