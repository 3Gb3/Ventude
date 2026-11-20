"""Paleta, tipografia do sistema e folha de estilo única."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from ventude.core.paths import app_data_dir

BG = "#0C0D10"
INK = BG
INK_RAISED = "#111317"
INK_KEY = "#191C22"
SURFACE = "#15171C"
SURFACE_2 = "#1C1F25"
SURFACE_3 = "#262A31"
STROKE = "#22252B"
STROKE_2 = "#30343C"
TEXT = "#ECEEF2"
TEXT_2 = "#A3A9B5"
MUTED = "#6E7480"
ACCENT = "#6EE7B7"
ACCENT_SOFT = "#15362C"
ACCENT_TEXT = "#062A1E"
WARN = "#F4C152"
DANGER = "#F27A7A"
UP = "#5FD99B"
DOWN = "#F27A7A"
SUN = "#F6C86B"
SKY = "#8EB8F5"
LIVE_MIC = "#FF9F43"
LIVE_CAM = "#34D399"
DOT_OPEN = TEXT_2
DOT_FOCUS = ACCENT
DOT_ATTENTION = WARN
TOGGLE_ON_BG = ACCENT_SOFT
TOGGLE_ON_FG = ACCENT
HAIRLINE = QColor(255, 255, 255, 20)
HALO = QColor(255, 255, 255, 15)
HALO_PRESS = QColor(255, 255, 255, 26)
ASLEEP_SATURATION = 0.45
ASLEEP_OPACITY = 0.82
R_SHEET = 26
R_CARD = 18
R_MENU = 14
R_ITEM = 10
PROVIDER_COLORS = {
    "claude": "#E38B67",
    "antigravity": "#78A6FF",
    "grok": "#B49BFF",
    "codex": "#D7DCE3",
    "cursor": "#F58FB8",
    "copilot": "#5ED3E6",
}
ISLAND_RADIUS = 26
SEMI = QFont.Weight.DemiBold

_UI_FONTS = ("Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", "Noto Sans")
_DISPLAY_FONTS = ("Segoe UI Variable Display", "Segoe UI", "Inter Display", "Inter")
_family_cache: dict[str, str] = {}


def _pick(candidates: tuple[str, ...]) -> str:
    key = candidates[0]
    if key not in _family_cache:
        families = set(QFontDatabase.families())
        _family_cache[key] = next((f for f in candidates if f in families), QApplication.font().family())
    return _family_cache[key]


def ui_family() -> str:
    return _pick(_UI_FONTS)


def font(
    size: int,
    weight: QFont.Weight = QFont.Weight.Normal,
    display: bool = False,
    tabular: bool = False,
) -> QFont:
    f = QFont(_pick(_DISPLAY_FONTS) if display else ui_family())
    f.setPixelSize(size)
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    if tabular and hasattr(f, "setFeature") and hasattr(QFont, "Tag"):
        try:
            f.setFeature(QFont.Tag(b"tnum"), 1)
        except (TypeError, ValueError):
            try:
                f.setFeature(QFont.Tag("tnum"), 1)
            except (TypeError, ValueError):
                pass
    return f


def color(value: str, alpha: float = 1.0) -> QColor:
    c = QColor(value)
    c.setAlphaF(alpha)
    return c


def provider_color(provider: str) -> str:
    return PROVIDER_COLORS.get(provider, ACCENT)


def usage_color(provider: str, used: float | None) -> str:
    if used is not None and used >= 90:
        return DANGER
    if used is not None and used >= 75:
        return WARN
    return provider_color(provider)


def _assets() -> Path:
    folder = app_data_dir() / "assets"
    folder.mkdir(exist_ok=True)
    files = {
        "chevron.svg": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{TEXT_2}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m6 9 6 6 6-6"/></svg>',
    }
    for name, content in files.items():
        path = folder / name
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
    return folder


def stylesheet() -> str:
    chevron = (_assets() / "chevron.svg").as_posix()
    return f"""
QWidget {{ color: {TEXT}; background: transparent; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {STROKE_2};
    border-radius: 10px; padding: 6px 10px; }}
QLabel {{ background: transparent; }}
QLabel#H1 {{ font-size: 19px; font-weight: 600; }}
QLabel#H2 {{ font-size: 15px; font-weight: 600; }}
QLabel#Muted {{ color: {TEXT_2}; font-size: 12px; }}
QLabel#Faint {{ color: {MUTED}; font-size: 11px; }}
QLabel#Caption {{ color: {MUTED}; font-size: 11px; font-weight: 600; }}
QLabel#Error {{ color: {DANGER}; font-size: 12px; }}
QLabel#Warn {{ color: {WARN}; font-size: 12px; }}
QFrame#Card {{ background: {SURFACE}; border: 1px solid {STROKE}; border-radius: 16px; }}
QFrame#Card[clickable="true"]:hover {{ border-color: {STROKE_2}; background: #181A20; }}
QFrame#Divider {{ background: {STROKE}; max-height: 1px; min-height: 1px; border: none; }}
QPushButton {{ background: {SURFACE_2}; border: 1px solid {STROKE_2}; border-radius: 10px;
    padding: 7px 13px; color: {TEXT}; }}
QPushButton:hover {{ background: {SURFACE_3}; }}
QPushButton:pressed {{ background: {STROKE_2}; }}
QPushButton:disabled {{ color: {MUTED}; background: {SURFACE}; border-color: {STROKE}; }}
QPushButton#Primary {{ background: {ACCENT}; color: {ACCENT_TEXT}; border: none; font-weight: 600; }}
QPushButton#Primary:hover {{ background: #86EDC4; }}
QPushButton#Danger {{ color: {DANGER}; background: #24171A; border-color: #43252A; }}
QPushButton#Danger:hover {{ background: #2E1B1F; }}
QPushButton#Icon {{ background: transparent; border: none; border-radius: 9px; padding: 0; }}
QPushButton#Icon:hover {{ background: {SURFACE_2}; }}
QPushButton#Icon:pressed {{ background: {SURFACE_3}; }}
QPushButton#Icon:checked {{ background: {SURFACE_3}; }}
QPushButton#Tab {{ background: transparent; border: none; border-radius: 10px;
    padding: 0 9px; color: {TEXT_2}; font-weight: 600; }}
QPushButton#Tab:hover {{ background: {SURFACE}; color: {TEXT}; }}
QPushButton#Tab:checked {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton#Chip {{ background: transparent; border: 1px solid {STROKE_2}; border-radius: 13px;
    padding: 4px 11px; color: {TEXT_2}; }}
QPushButton#Chip:hover {{ color: {TEXT}; }}
QPushButton#Chip:checked {{ background: {ACCENT_SOFT}; border-color: #2B6B55; color: {ACCENT}; }}
QPushButton#Link {{ background: transparent; border: none; padding: 0; color: {ACCENT};
    text-align: left; }}
QPushButton#Link:hover {{ color: #9CF0CF; }}
QLineEdit {{ background: {SURFACE_2}; border: 1px solid {STROKE_2}; border-radius: 10px;
    padding: 7px 10px; selection-background-color: {ACCENT_SOFT}; color: {TEXT}; }}
QLineEdit:focus {{ border-color: #3C8C70; }}
QLineEdit#Inline {{ background: transparent; border: 1px solid transparent; padding: 4px 6px; }}
QLineEdit#Inline:focus {{ background: {SURFACE_2}; border-color: #3C8C70; }}
QComboBox {{ background: {SURFACE_2}; border: 1px solid {STROKE_2}; border-radius: 10px;
    padding: 6px 10px; min-height: 18px; }}
QComboBox:hover {{ border-color: #3D424C; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({chevron}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {STROKE_2};
    selection-background-color: {SURFACE_3}; outline: none; padding: 4px; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 10px; padding: 6px; color: {TEXT}; }}
QListWidget::item:hover {{ background: {SURFACE_2}; }}
QListWidget::item:selected {{ background: {SURFACE_3}; color: {TEXT}; }}
QMenu {{ background: {INK_RAISED}; border: 1px solid {STROKE_2}; border-radius: {R_MENU}px; padding: 6px; }}
QMenu::item {{ padding: 7px 22px 7px 12px; border-radius: {R_ITEM}px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMenu::separator {{ height: 1px; background: {STROKE_2}; margin: 4px 6px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {STROKE_2}; min-height: 28px; border-radius: 3px; }}
QScrollBar::handle:vertical:hover {{ background: #3D424C; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0; }}
QProgressBar {{ background: {SURFACE_3}; border: none; border-radius: 3px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::groove:horizontal {{ height: 4px; background: {STROKE_2}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {TEXT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 10px; height: 10px; margin: -3px 0; background: {TEXT};
    border-radius: 5px; }}
QSlider::handle:horizontal:disabled {{ background: transparent; }}
QSlider::sub-page:horizontal:disabled {{ background: {MUTED}; }}
"""


def apply(app: QApplication) -> None:
    app.setFont(font(13))
    app.setStyleSheet(stylesheet())
