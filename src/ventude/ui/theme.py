"""Paleta, tipografia do sistema e folha de estilo única."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QLinearGradient
from PySide6.QtWidgets import QApplication

from ventude.core.paths import app_data_dir

# ---------- Ventude 3 ("Brisa"): grafite azulado + o gradiente do tornadinho (menta → céu) ----------
BG = "#08090D"
INK = BG
INK_RAISED = "#0F1118"
INK_KEY = "#161922"
SURFACE = "#11141B"
SURFACE_2 = "#181C25"
SURFACE_3 = "#222734"
STROKE = "#1B1F29"
STROKE_2 = "#2A303D"
TEXT = "#F2F4F8"
TEXT_2 = "#A8AFBF"
MUTED = "#697084"
ACCENT = "#5EE6C1"
ACCENT_2 = "#42B8F6"  # segunda cor do gradiente da marca (as voltas de baixo do tornadinho)
ACCENT_SOFT = "#0F302C"
ACCENT_LINE = "#2C7F6C"  # contorno de foco e de itens marcados
ACCENT_TEXT = "#03201A"
WARN = "#F6C35B"
DANGER = "#F57B83"
UP = "#5FD99B"
DOWN = "#F57B83"
SUN = "#F7C76A"
SKY = "#8DBBF7"
LIVE_MIC = "#FF9F43"
LIVE_CAM = "#34D399"
DOT_OPEN = TEXT_2
DOT_FOCUS = ACCENT
DOT_ATTENTION = WARN
TOGGLE_ON_BG = ACCENT_SOFT
TOGGLE_ON_FG = ACCENT
HAIRLINE = QColor(255, 255, 255, 18)
HALO = QColor(255, 255, 255, 14)
HALO_PRESS = QColor(255, 255, 255, 26)
# Os mesmos, escritos para folhas de estilo (um QColor dentro de uma f-string vira texto inválido).
HAIRLINE_QSS = "rgba(255,255,255,0.07)"
HALO_QSS = "rgba(255,255,255,0.055)"
R_SHEET = 28
R_CARD = 18
R_MENU = 14
R_ITEM = 10
PROVIDER_COLORS = {
    "claude": "#E8916D",
    "antigravity": "#7CA8FF",
    "grok": "#B79EFF",
    "codex": "#D9DEE6",
    "cursor": "#F592BC",
    "copilot": "#5ED6E8",
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


@lru_cache(maxsize=256)
def _font(size: int, weight: QFont.Weight, display: bool, tabular: bool) -> QFont:
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


def font(
    size: int,
    weight: QFont.Weight = QFont.Weight.Normal,
    display: bool = False,
    tabular: bool = False,
) -> QFont:
    """Fonte do sistema; montada uma vez por combinação (os paintEvent chamam isto a cada quadro)."""
    return QFont(_font(int(size), weight, bool(display), bool(tabular)))


@lru_cache(maxsize=512)
def _color(value: str, alpha: float) -> QColor:
    c = QColor(value)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def color(value: str, alpha: float = 1.0) -> QColor:
    if not isinstance(value, str):  # QColor pronta (ex.: HAIRLINE)
        c = QColor(value)
        c.setAlphaF(max(0.0, min(1.0, alpha)))
        return c
    return QColor(_color(value, round(float(alpha), 3)))


def mix(a: str, b: str, t: float) -> QColor:
    """Cor entre a e b (0 = a, 1 = b)."""
    t = max(0.0, min(1.0, float(t)))
    ca, cb = QColor(a), QColor(b)
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t),
    )


def accent_gradient(x1: float, y1: float, x2: float, y2: float, alpha: float = 1.0) -> QLinearGradient:
    """O gradiente da marca (menta → céu) entre dois pontos."""
    g = QLinearGradient(x1, y1, x2, y2)
    g.setColorAt(0.0, color(ACCENT, alpha))
    g.setColorAt(1.0, color(ACCENT_2, alpha))
    return g


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


def _grad(a: str, b: str, *, vertical: bool = False) -> str:
    x2, y2 = (0, 1) if vertical else (1, 0)
    return f"qlineargradient(x1:0, y1:0, x2:{x2}, y2:{y2}, stop:0 {a}, stop:1 {b})"


def stylesheet() -> str:
    chevron = (_assets() / "chevron.svg").as_posix()
    brand = _grad(ACCENT, ACCENT_2)
    brand_hover = _grad("#7BEFCF", "#63C7FA")
    card = _grad("#141821", SURFACE, vertical=True)
    card_hover = _grad("#181D27", "#131720", vertical=True)
    return f"""
QWidget {{ color: {TEXT}; background: transparent; }}
QToolTip {{ background: {INK_KEY}; color: {TEXT}; border: 1px solid {STROKE_2};
    border-radius: 9px; padding: 5px 9px; }}
QLabel {{ background: transparent; }}
QLabel#H1 {{ font-size: 20px; font-weight: 700; }}
QLabel#H2 {{ font-size: 15px; font-weight: 600; }}
QLabel#Muted {{ color: {TEXT_2}; font-size: 12px; }}
QLabel#Faint {{ color: {MUTED}; font-size: 11px; }}
QLabel#Caption {{ color: {MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.6px; }}
QLabel#Error {{ color: {DANGER}; font-size: 12px; }}
QLabel#Warn {{ color: {WARN}; font-size: 12px; }}
QFrame#Card {{ background: {card}; border: 1px solid {STROKE}; border-top-color: #232835;
    border-radius: {R_CARD}px; }}
QFrame#Card[clickable="true"]:hover {{ background: {card_hover}; border-color: {STROKE_2}; }}
QFrame#Divider {{ background: {STROKE}; max-height: 1px; min-height: 1px; border: none; }}
QPushButton {{ background: {SURFACE_2}; border: 1px solid {STROKE_2}; border-radius: 11px;
    padding: 7px 14px; color: {TEXT}; font-weight: 500; }}
QPushButton:hover {{ background: {SURFACE_3}; border-color: #353B4A; }}
QPushButton:pressed {{ background: {STROKE_2}; }}
QPushButton:focus {{ border-color: {ACCENT_LINE}; }}
QPushButton:disabled {{ color: {MUTED}; background: {SURFACE}; border-color: {STROKE}; }}
QPushButton#Primary {{ background: {brand}; color: {ACCENT_TEXT}; border: none; font-weight: 700; }}
QPushButton#Primary:hover {{ background: {brand_hover}; }}
QPushButton#Primary:pressed {{ background: {ACCENT}; }}
QPushButton#Danger {{ color: {DANGER}; background: #22141A; border-color: #452530; }}
QPushButton#Danger:hover {{ background: #2C1820; }}
QPushButton#Icon {{ background: transparent; border: none; border-radius: 10px; padding: 0; }}
QPushButton#Icon:hover {{ background: rgba(255,255,255,0.06); }}
QPushButton#Icon:pressed {{ background: rgba(255,255,255,0.10); }}
QPushButton#Icon:checked {{ background: rgba(255,255,255,0.08); }}
QPushButton#Tab {{ background: transparent; border: none; border-radius: 11px;
    padding: 0 10px; color: {TEXT_2}; font-weight: 600; }}
QPushButton#Tab:hover {{ color: {TEXT}; }}
QPushButton#Tab:checked {{ color: {TEXT}; }}
QPushButton#Chip {{ background: rgba(255,255,255,0.03); border: 1px solid {STROKE_2}; border-radius: 12px;
    padding: 3px 12px; min-height: 18px; color: {TEXT_2}; font-weight: 500; }}
QPushButton#Chip:hover {{ color: {TEXT}; border-color: #3A4050; }}
QPushButton#Chip:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT_LINE}; color: {ACCENT}; }}
QPushButton#Link {{ background: transparent; border: none; padding: 0; color: {ACCENT};
    text-align: left; font-weight: 600; }}
QPushButton#Link:hover {{ color: #A3F3DD; }}
QLineEdit, QSpinBox {{ background: {INK_RAISED}; border: 1px solid {STROKE_2}; border-radius: 11px;
    padding: 7px 11px; selection-background-color: {ACCENT_SOFT}; color: {TEXT}; }}
QLineEdit:hover, QSpinBox:hover {{ border-color: #353B4A; }}
QLineEdit:focus, QSpinBox:focus {{ border-color: {ACCENT_LINE}; background: {SURFACE}; }}
QLineEdit#Inline {{ background: transparent; border: 1px solid transparent; padding: 4px 6px; }}
QLineEdit#Inline:focus {{ background: {INK_RAISED}; border-color: {ACCENT_LINE}; }}
QPlainTextEdit {{ background: {INK_RAISED}; border: 1px solid {STROKE_2}; border-radius: 11px;
    padding: 6px; selection-background-color: {ACCENT_SOFT}; }}
QPlainTextEdit:focus {{ border-color: {ACCENT_LINE}; }}
QComboBox {{ background: {SURFACE_2}; border: 1px solid {STROKE_2}; border-radius: 11px;
    padding: 6px 11px; min-height: 18px; }}
QComboBox:hover {{ border-color: #3A4050; background: {SURFACE_3}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({chevron}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {INK_RAISED}; border: 1px solid {STROKE_2};
    selection-background-color: rgba(255,255,255,0.08); outline: none; padding: 4px; border-radius: 10px; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 10px; padding: 6px; color: {TEXT}; }}
QListWidget::item:hover {{ background: rgba(255,255,255,0.05); }}
QListWidget::item:selected {{ background: rgba(255,255,255,0.09); color: {TEXT}; }}
QMenu {{ background: {INK_RAISED}; border: 1px solid {STROKE_2}; border-radius: {R_MENU}px; padding: 6px; }}
QMenu::item {{ padding: 7px 22px 7px 12px; border-radius: {R_ITEM}px; }}
QMenu::item:selected {{ background: rgba(255,255,255,0.08); }}
QMenu::separator {{ height: 1px; background: {STROKE_2}; margin: 4px 6px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.10); min-height: 32px; border-radius: 2px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.20); }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0; }}
QProgressBar {{ background: {SURFACE_3}; border: none; border-radius: 3px; }}
QProgressBar::chunk {{ background: {brand}; border-radius: 3px; }}
QSlider::groove:horizontal {{ height: 4px; background: {STROKE_2}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {brand}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; background: {TEXT};
    border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: #FFFFFF; }}
QSlider::handle:horizontal:disabled {{ background: transparent; }}
QSlider::sub-page:horizontal:disabled {{ background: {MUTED}; }}
"""


def apply(app: QApplication) -> None:
    app.setFont(font(13))
    app.setStyleSheet(stylesheet())
