"""Loads the bundled SVG icons (voxini_studio/resources/icons/*.svg) as
QIcon/QPixmap, recoloring the currentColor-based single-path outline icons
on the fly. Used everywhere in the UI instead of emoji or plain text
glyphs, per the binding UI spec ("professionelle gebuendelte SVG-Symbole
anstelle von Emojis oder einfachen Textzeichen")."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from voxini_studio.ui.theme import icons_dir


@lru_cache(maxsize=None)
def _svg_source(name: str) -> str:
    path = icons_dir() / f"{name}.svg"
    if not path.exists():
        raise FileNotFoundError(f"Icon nicht gefunden: {path}")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _pixmap(name: str, color: str, size: int) -> QPixmap:
    svg = _svg_source(name).replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(QSize(size, size))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


def pixmap(name: str, color: str = "#ffffff", size: int = 24) -> QPixmap:
    return _pixmap(name, color, size)


def icon(name: str, color: str = "#ffffff", size: int = 24) -> QIcon:
    return QIcon(pixmap(name, color, size))


def available_icons() -> list[str]:
    return sorted(p.stem for p in icons_dir().glob("*.svg"))
