"""Central VOXini/Neon68 color palette and Qt stylesheet (QSS) builder.

This is the ONLY place color values may be defined for the UI - every
widget module must reference these constants (via `palette()`), the
generated stylesheet (`build_stylesheet()`), or the dynamic-property roles
it styles (e.g. `label.setProperty("role", "muted")`) rather than
hardcoding hex colors inline, so the whole application stays visually
consistent with the VOXini brand and a single edit here re-themes
everything at once.

Palette values are taken VERBATIM from the VOXini/Neon68 `style.css`
`:root` definitions, per the binding UI color specification:
pink #ff34c8, cyan #24d9ff, purple #251132, panel #1b1620,
panel2 #100c13, text #ffffff, muted #bdb0c7, red #ff416d, plus the
background radial gradient (#32113d -> #09060d -> #040306), the
pink->purple->cyan primary gradient, success/gold/warning accents, and the
karaoke accent colors. "voxini" is always the default theme; "midnight" and
"graphite" are optional alternates, only offered because theme switching
here is fully implemented and persisted (QSettings) - never the default.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from PySide6.QtCore import QSettings

ThemeName = Literal["voxini", "midnight", "graphite"]

DEFAULT_THEME: ThemeName = "voxini"

_SETTINGS_ORG = "VOXini"
_SETTINGS_APP = "VideoStudio"
_SETTINGS_KEY = "ui/theme"


@dataclass(frozen=True)
class Palette:
    pink: str
    cyan: str
    purple: str
    panel: str
    panel2: str
    text: str
    muted: str
    red: str
    success: str
    success2: str
    gold: str
    warning: str
    bg1: str
    bg2: str
    bg3: str
    karaoke_sung: str = "#ff43ce"
    karaoke_current: str = "#f5ff2e"
    mid_grad: str = "#9c4cff"
    """Middle color of the pink -> purple -> cyan primary gradient."""


PALETTES: dict[ThemeName, Palette] = {
    # Standard theme - VERBATIM from style.css :root. Always the default.
    "voxini": Palette(
        pink="#ff34c8", cyan="#24d9ff", purple="#251132",
        panel="#1b1620", panel2="#100c13",
        text="#ffffff", muted="#bdb0c7", red="#ff416d",
        success="#53e6a6", success2="#2eeaa0",
        gold="#ffd65a", warning="#ffbe6b",
        bg1="#32113d", bg2="#09060d", bg3="#040306",
    ),
    # Optional alternate themes, from the same style.css spec.
    "midnight": Palette(
        pink="#44a8ff", cyan="#45f0df", purple="#112941",
        panel="#111b29", panel2="#08111d",
        text="#ffffff", muted="#9fb2c4", red="#ff416d",
        success="#53e6a6", success2="#2eeaa0",
        gold="#ffd65a", warning="#ffbe6b",
        bg1="#173a5e", bg2="#0a1622", bg3="#050b12",
        mid_grad="#4ec9ff",
    ),
    "graphite": Palette(
        pink="#f0b64a", cyan="#b8c1cc", purple="#262626",
        panel="#1b1b1b", panel2="#0f0f0f",
        text="#ffffff", muted="#a8a8a8", red="#ff416d",
        success="#53e6a6", success2="#2eeaa0",
        gold="#ffd65a", warning="#ffbe6b",
        bg1="#2e2e2e", bg2="#141414", bg3="#090909",
        mid_grad="#d0d0d0",
    ),
}

THEME_LABELS: dict[ThemeName, str] = {
    "voxini": "VOXini (Standard)",
    "midnight": "Midnight",
    "graphite": "Graphite",
}


# -- resource resolution (dev + PyInstaller) ---------------------------------

def resource_root() -> Path:
    """Resolves voxini_studio/resources both when running from source and
    when frozen into a PyInstaller build, where bundled data lives under
    sys._MEIPASS instead of next to this file. The PyInstaller .spec must
    add `('voxini_studio/resources', 'voxini_studio/resources')` to
    `datas` for the frozen path to exist - see BUILD_WINDOWS.bat/spec."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "voxini_studio" / "resources"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent / "resources"


def icons_dir() -> Path:
    return resource_root() / "icons"


# -- theme preference persistence --------------------------------------------

def _make_settings() -> QSettings:
    """Factory for the QSettings instance theme preferences are stored in.
    Tests monkeypatch this (instead of load/save_theme_preference directly)
    to point at a throwaway QSettings.Format.IniFormat file, so they never
    touch the real Windows registry (HKEY_CURRENT_USER\\Software\\VOXini\\
    VideoStudio) or the real user's config on any other OS - setting the
    HOME/APPDATA env var alone does NOT isolate QSettings' NativeFormat on
    Windows, since the native backend there is the registry, which ignores
    those env vars entirely."""
    return QSettings(_SETTINGS_ORG, _SETTINGS_APP)


def load_theme_preference() -> ThemeName:
    settings = _make_settings()
    value = settings.value(_SETTINGS_KEY, DEFAULT_THEME)
    return value if value in PALETTES else DEFAULT_THEME


def save_theme_preference(name: ThemeName) -> None:
    settings = _make_settings()
    settings.setValue(_SETTINGS_KEY, name)
    settings.sync()


def palette(name: Optional[ThemeName] = None) -> Palette:
    return PALETTES.get(name or DEFAULT_THEME, PALETTES[DEFAULT_THEME])


# -- stylesheet ---------------------------------------------------------------

def build_stylesheet(name: Optional[ThemeName] = None) -> str:
    p = palette(name)
    return f"""
    QMainWindow, QDialog {{
        background: qradialgradient(cx:0.5, cy:0.12, radius:1.15,
            stop:0 {p.bg1}, stop:0.55 {p.bg2}, stop:1 {p.bg3});
        color: {p.text};
    }}
    QWidget {{
        color: {p.text};
        font-size: 13px;
    }}
    QWidget#TopBar {{ background: transparent; }}
    QLabel {{ background: transparent; }}
    QLabel[role="muted"] {{ color: {p.muted}; }}
    QLabel[role="heading"] {{ font-size: 17px; font-weight: 700; color: {p.text}; }}
    QLabel[role="success"] {{ color: {p.success}; font-weight: 600; }}
    QLabel[role="warning"] {{ color: {p.warning}; font-weight: 600; }}
    QLabel[role="error"] {{ color: {p.red}; font-weight: 600; }}
    QLabel[role="gold"] {{ color: {p.gold}; font-weight: 600; }}

    QGroupBox {{
        background-color: {p.panel};
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 10px;
        margin-top: 14px;
        padding: 10px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
        color: {p.muted};
    }}

    QTabWidget::pane {{
        background-color: {p.panel2};
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 8px;
        top: -1px;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {p.muted};
        padding: 8px 18px;
        margin-right: 2px;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
    }}
    QTabBar::tab:selected {{
        color: {p.text};
        font-weight: 700;
        border-bottom: 3px solid {p.pink};
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
            stop:0 rgba(255,52,200,0.18), stop:1 rgba(36,217,255,0.18));
    }}
    QTabBar::tab:hover:!selected {{ color: {p.text}; }}

    QPushButton {{
        background-color: {p.panel};
        color: {p.text};
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 7px;
        padding: 7px 14px;
    }}
    QPushButton:hover {{ border-color: {p.cyan}; }}
    QPushButton:pressed {{ background-color: {p.panel2}; }}
    QPushButton:disabled {{ color: {p.muted}; border-color: rgba(255,255,255,0.05); }}

    QPushButton[role="primary"] {{
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {p.pink}, stop:0.5 {p.mid_grad}, stop:1 {p.cyan});
        color: #ffffff;
        border: none;
        font-weight: 700;
    }}
    QPushButton[role="primary"]:hover {{
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {p.pink}, stop:0.5 {p.mid_grad}, stop:1 {p.cyan});
    }}
    QPushButton[role="primary"]:disabled {{ background: {p.panel}; color: {p.muted}; }}

    QPushButton[role="danger"] {{ border-color: {p.red}; color: {p.red}; }}
    QPushButton[role="danger"]:hover {{ background-color: rgba(255,65,109,0.14); }}

    QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background-color: {p.panel2};
        color: {p.text};
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 6px;
        padding: 5px 8px;
        selection-background-color: {p.pink};
    }}
    QComboBox QAbstractItemView {{
        background-color: {p.panel2};
        color: {p.text};
        selection-background-color: {p.pink};
        outline: none;
    }}

    QListWidget, QTableWidget {{
        background-color: {p.panel2};
        color: {p.text};
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 8px;
        gridline-color: rgba(255,255,255,0.06);
    }}
    QHeaderView::section {{
        background-color: {p.panel};
        color: {p.muted};
        border: none;
        padding: 6px;
        font-weight: 600;
    }}
    QTableWidget::item:selected, QListWidget::item:selected {{
        background-color: rgba(255,52,200,0.22);
        color: {p.text};
    }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.18); border-radius: 5px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {p.cyan}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: rgba(255,255,255,0.18); border-radius: 5px; min-width: 24px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    QProgressBar {{
        background-color: {p.panel2};
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 6px;
        text-align: center;
        color: {p.text};
    }}
    QProgressBar::chunk {{
        border-radius: 6px;
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {p.pink}, stop:1 {p.cyan});
    }}

    QCheckBox, QRadioButton {{ color: {p.text}; spacing: 8px; }}
    QMenu {{ background-color: {p.panel2}; color: {p.text}; border: 1px solid rgba(255,255,255,0.1); }}
    QMenu::item:selected {{ background-color: rgba(255,52,200,0.2); }}

    QToolTip {{
        background-color: {p.panel2};
        color: {p.text};
        border: 1px solid {p.pink};
        padding: 4px 8px;
    }}
    """


def badge_style(kind: Literal["free", "paid", "test"], name: Optional[ThemeName] = None) -> str:
    """Stylesheet fragment for the 'Lokal - kostenlos' / 'Runway -
    kostenpflichtig' / 'Mock - Testmodus' badges used in the storyboard and
    generation views, so provider cost is always unambiguous at a glance."""
    p = palette(name)
    if kind == "free":
        color, bg = p.success, "rgba(83,230,166,0.16)"
    elif kind == "paid":
        color, bg = p.gold, "rgba(255,214,90,0.16)"
    else:
        color, bg = p.muted, "rgba(189,176,199,0.16)"
    return f"color: {color}; background-color: {bg}; border-radius: 5px; padding: 2px 8px; font-weight: 700;"
