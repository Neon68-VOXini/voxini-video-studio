"""Tests for the central VOXini/Neon68 theme module and bundled SVG icons.
Requires QT_QPA_PLATFORM=offscreen (QPixmap needs a QGuiApplication even
off-screen) - see README for the exact env vars used in CI/dev."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from voxini_studio.ui import icons, theme  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# -- palette --------------------------------------------------------------

def test_default_theme_is_voxini():
    assert theme.DEFAULT_THEME == "voxini"


def test_voxini_palette_matches_style_css_spec_exactly():
    p = theme.palette("voxini")
    assert p.pink == "#ff34c8"
    assert p.cyan == "#24d9ff"
    assert p.purple == "#251132"
    assert p.panel == "#1b1620"
    assert p.panel2 == "#100c13"
    assert p.text == "#ffffff"
    assert p.muted == "#bdb0c7"
    assert p.red == "#ff416d"
    assert (p.bg1, p.bg2, p.bg3) == ("#32113d", "#09060d", "#040306")
    assert p.success in ("#53e6a6", "#2eeaa0")
    assert p.gold == "#ffd65a"
    assert p.warning == "#ffbe6b"
    assert p.mid_grad == "#9c4cff"


def test_all_three_themes_exist_and_are_distinct():
    names = set(theme.PALETTES.keys())
    assert names == {"voxini", "midnight", "graphite"}
    pinks = {theme.palette(n).pink for n in names}
    assert len(pinks) == 3  # each theme has a genuinely different accent


def test_unknown_theme_name_falls_back_to_default():
    p = theme.palette("does-not-exist")  # type: ignore[arg-type]
    assert p == theme.palette("voxini")


# -- stylesheet -------------------------------------------------------------

def test_build_stylesheet_contains_palette_colors():
    css = theme.build_stylesheet("voxini")
    assert "#ff34c8" in css
    assert "#24d9ff" in css
    assert "#1b1620" in css
    assert "qlineargradient" in css  # primary buttons/progress use the brand gradient


def test_build_stylesheet_differs_per_theme():
    voxini_css = theme.build_stylesheet("voxini")
    midnight_css = theme.build_stylesheet("midnight")
    assert voxini_css != midnight_css
    assert "#44a8ff" in midnight_css
    assert "#44a8ff" not in voxini_css


def test_badge_style_uses_success_for_free_and_gold_for_paid():
    free_css = theme.badge_style("free")
    paid_css = theme.badge_style("paid")
    assert theme.palette().success in free_css
    assert theme.palette().gold in paid_css
    assert free_css != paid_css


# -- theme preference persistence -------------------------------------------

def test_theme_preference_roundtrip(qapp, monkeypatch, tmp_path):
    # Isolate QSettings to a throwaway .ini file, regardless of OS. Setting
    # HOME/APPDATA alone is NOT sufficient isolation on Windows: QSettings'
    # NativeFormat there is the real registry (HKCU\Software\VOXini\
    # VideoStudio), which ignores those env vars entirely - a real Windows
    # test run confirmed this test was silently touching the real registry.
    # Monkeypatching the module's settings factory instead guarantees this
    # test only ever reads/writes a temp .ini file, on every platform.
    ini_path = str(tmp_path / "voxini_theme_test_settings.ini")
    monkeypatch.setattr(
        theme, "_make_settings", lambda: QSettings(ini_path, QSettings.Format.IniFormat)
    )
    theme.save_theme_preference("graphite")
    assert theme.load_theme_preference() == "graphite"
    theme.save_theme_preference("voxini")
    assert theme.load_theme_preference() == "voxini"


def test_make_settings_factory_is_the_only_seam_used(qapp, monkeypatch, tmp_path):
    """Regression guard: load/save_theme_preference must go through
    `theme._make_settings()` and nothing else, so isolating that one factory
    (as the test above does) is always sufficient - if either function ever
    starts constructing its own QSettings(...) again directly, this test
    catches it."""
    calls: list[str] = []

    def spy_factory():
        calls.append("called")
        ini_path = str(tmp_path / "voxini_theme_spy.ini")
        return QSettings(ini_path, QSettings.Format.IniFormat)

    monkeypatch.setattr(theme, "_make_settings", spy_factory)
    theme.save_theme_preference("graphite")
    theme.load_theme_preference()
    assert len(calls) == 2


# -- resource resolution ------------------------------------------------------

def test_resource_root_points_at_real_resources_dir():
    root = theme.resource_root()
    assert root.name == "resources"
    assert (root / "icons").is_dir()


def test_icons_dir_contains_logo_and_core_icons():
    names = set(p.stem for p in theme.icons_dir().glob("*.svg"))
    for required in ("logo", "folder-open", "save", "cpu", "cloud", "wand", "check-circle", "alert-triangle"):
        assert required in names


# -- icon loading (needs a live QGuiApplication, even offscreen) ------------

def test_icon_loads_as_nonnull_pixmap(qapp):
    pix = icons.pixmap("check-circle", color="#ffffff", size=24)
    assert not pix.isNull()
    assert pix.width() == 24
    assert pix.height() == 24


def test_icon_recolors_svg(qapp):
    pix_white = icons.pixmap("cpu", color="#ffffff", size=16)
    pix_pink = icons.pixmap("cpu", color="#ff34c8", size=16)
    assert pix_white.toImage() != pix_pink.toImage()


def test_icon_missing_name_raises(qapp):
    with pytest.raises(FileNotFoundError):
        icons.pixmap("this-icon-does-not-exist")


def test_available_icons_nonempty():
    assert len(icons.available_icons()) >= 15
