"""A ilha: uma pílula discreta no topo da tela que se abre num painel."""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from PySide6.QtCore import (
    QEvent,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QCursor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRegion,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ventude.core.config import MODULES, TAB_GROUPS, Settings
from ventude.core.timefmt import WEEKDAYS_SHORT, clock
from ventude.services import hardware, system, winutil
from ventude.services.hardware import Snapshot
from ventude.services.media import MediaState
from ventude.services.privacy import PrivacyState
from ventude.services.timer import TimerState, countdown, finished_text
from ventude.services.weather import Forecast
from ventude.ui import brand, icons, motion, theme
from ventude.ui.capture import CaptureSession, color_hex, png_bytes, prepare_for_ocr
from ventude.ui.command import CommandBar
from ventude.ui.context import AppContext
from ventude.ui.pages.agenda import AgendaPage
from ventude.ui.pages.audio import AudioPage
from ventude.ui.pages.base import Page
from ventude.ui.pages.hardware import HardwarePage
from ventude.ui.pages.home import HomePage, fill_ring
from ventude.ui.pages.limits import LimitsPage
from ventude.ui.pages.market import MarketPage
from ventude.ui.pages.news import NewsPage
from ventude.ui.pages.settings import SettingsPage
from ventude.ui.pages.shelf import ShelfPage
from ventude.ui.pages.shortcuts import ShortcutsPage
from ventude.ui.pages.tasks import TasksPage
from ventude.ui.pages.weather import WeatherPage
from ventude.ui.pill_overlay import VolumeOverlay
from ventude.ui.power_menu import PowerMenu
from ventude.ui.qr import QrCard
from ventude.ui.shell.controls import CONTROLS_WIDTH, ControlsBody
from ventude.ui.tabs import TabButton, TabStrip
from ventude.ui.toast import TOAST_HEIGHT, TOAST_WIDTH, Toast, ToastView, event_toast
from ventude.ui.widgets import (
    BadgeButton,
    ElidedLabel,
    Equalizer,
    IconButton,
    Ring,
    label,
    rounded_pixmap,
    set_qss,
)

log = logging.getLogger(__name__)

PAGES: dict[str, Callable[[AppContext], Page]] = {
    "home": HomePage,
    "limits": LimitsPage,
    "hardware": HardwarePage,
    "audio": AudioPage,
    "shortcuts": ShortcutsPage,
    "shelf": ShelfPage,
    "agenda": AgendaPage,
    "tasks": TasksPage,
    "weather": WeatherPage,
    "market": MarketPage,
    "news": NewsPage,
    "settings": SettingsPage,
}
MIC_COLOR = theme.LIVE_MIC
CAM_COLOR = theme.LIVE_CAM
METRIC_GAP = "   "
COMPACT_HEIGHT = 38
EXPANDED = QSize(860, 568)
HEADER_HEIGHT = 54
TOP_MARGIN = 6
BACK_HOME_AFTER = 5 * 60
COLLAPSE_COOLDOWN = 0.6
MEDIA_HOVER_DELAY = 650  # ms: sobre a música, espera um pouco para dar tempo da roda/clique


class IslandMode(Enum):
    PILL = "pill"
    TOAST = "toast"
    CONTROLS = "controls"
    PANEL = "panel"


@dataclass(frozen=True)
class StatusState:
    network: str = "ethernet"
    wifi: str = ""
    speaker_muted: bool = False
    battery_low: bool = False
    battery_pct: int | None = None


def anchored_rect(area: QRect, width: int, height: int, anchor: QPoint) -> QRect:
    """Mantém a âncora (centro do topo) visível, inclusive em monitores negativos."""
    width, height = min(width, area.width() - 16), min(height, area.height() - 16)
    x = max(area.left() + 8, min(anchor.x() - width // 2, area.right() - width - 7))
    y = max(area.top() + 2, min(anchor.y(), area.bottom() - height - 7))
    return QRect(x, y, width, height)


def screen_for(index: int):  # noqa: ANN201
    screens = QGuiApplication.screens()
    return screens[index] if 0 <= index < len(screens) else QGuiApplication.primaryScreen()


class PrivacyDot(QWidget):
    """Ponto de privacidade (8×8). Quando mic está mutado, mostra um risco diagonal."""

    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self.muted = False
        self.setFixedSize(8, 8)
        self.hide()

    def set_muted(self, muted: bool) -> None:
        if self.muted != muted:
            self.muted = muted
            self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color(self._color, 0.55 if self.muted else 1.0))
        p.drawEllipse(QRectF(0, 0, 8, 8))
        if self.muted:
            pen = QPen(theme.color(theme.BG), 1.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(1.5, 6.5), QPointF(6.5, 1.5))


class StatusDot(QWidget):
    """Ponto permanente do fim da pílula (6 px): menta normal, âmbar em exceção. Clique → Controles."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.alert = False
        self.setFixedSize(10, 10)
        self.setAccessibleName("Controles")
        self.setToolTip("Controles (Win + A)")
        self.hide()

    def set_alert(self, alert: bool) -> None:
        if self.alert != alert:
            self.alert = alert
            self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color(theme.WARN if self.alert else theme.ACCENT))
        p.drawEllipse(QPointF(self.width() / 2.0, self.height() / 2.0), 3.0, 3.0)


def privacy_dot(color: str) -> PrivacyDot:
    return PrivacyDot(color)


class Pill(QWidget):
    """Conteúdo da ilha fechada: data, hora, música, atividades vivas, anéis e Ponto."""

    def __init__(self, ctx: AppContext, parent: QWidget) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.shell_mode = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)  # a ilha acompanha em qual parte da pílula o mouse está
        self.art = QLabel()
        self.art.setFixedSize(22, 22)
        self.date = label("", "")
        self.date.setFont(theme.font(12, theme.SEMI))
        self.date.setStyleSheet(f"color: {theme.TEXT_2};")
        self.title = ElidedLabel("")
        self.title.setFont(theme.font(12, theme.SEMI))
        self.eq = Equalizer(theme.ACCENT)
        self.time = label("", "")
        self.time.setFont(theme.font(13, theme.SEMI, display=True, tabular=True))
        self.rings: dict[str, Ring] = {p: Ring(16, 2.6) for p in ctx.limits.providers()}
        row = QHBoxLayout(self)
        row.setContentsMargins(15, 0, 13, 0)
        row.setSpacing(10)
        self.weather = QWidget()
        self.weather_icon = QLabel()
        self.weather_icon.setFixedSize(16, 16)
        self.weather_temp = label("", "")
        self.weather_temp.setFont(theme.font(12, theme.SEMI, tabular=True))
        self.weather_temp.setStyleSheet(f"color: {theme.TEXT_2};")
        self.weather_rain = label("", "")
        self.weather_rain.setFont(theme.font(11, QFont.Weight.Bold, tabular=True))
        self.weather_rain.setStyleSheet(f"color: {theme.SKY};")
        self.weather_rain.hide()
        weather_row = QHBoxLayout(self.weather)
        weather_row.setContentsMargins(0, 0, 0, 0)
        weather_row.setSpacing(4)
        weather_row.addWidget(self.weather_icon)
        weather_row.addWidget(self.weather_temp)
        weather_row.addWidget(self.weather_rain)
        self.weather.hide()
        self.timer_chip = QWidget()
        self.timer_ring = Ring(15, 2.4)
        self.timer_text = label("", "")
        self.timer_text.setFont(theme.font(12, QFont.Weight.Bold, tabular=True))
        timer_row = QHBoxLayout(self.timer_chip)
        timer_row.setContentsMargins(0, 0, 0, 0)
        timer_row.setSpacing(5)
        timer_row.addWidget(self.timer_ring)
        timer_row.addWidget(self.timer_text)
        self.timer_chip.hide()

        self.event_chip = label("", "")
        self.event_chip.setFont(theme.font(11, theme.SEMI, tabular=True))
        self.event_chip.setStyleSheet(
            f"background: {theme.ACCENT_SOFT}; color: {theme.ACCENT}; border-radius: 10px; padding: 2px 8px;"
        )
        self.event_chip.hide()
        self._upcoming_event: object | None = None
        self._event_zero_pulsed = False

        self.shelf_chip = QWidget()
        shelf_icon = QLabel()
        shelf_icon.setPixmap(icons.pixmap("inbox", theme.TEXT_2, 14))
        self.shelf_count = label("", "")
        self.shelf_count.setFont(theme.font(11, QFont.Weight.Bold, tabular=True))
        self.shelf_count.setStyleSheet(f"color: {theme.TEXT_2};")
        shelf_row = QHBoxLayout(self.shelf_chip)
        shelf_row.setContentsMargins(0, 0, 0, 0)
        shelf_row.setSpacing(3)
        shelf_row.addWidget(shelf_icon)
        shelf_row.addWidget(self.shelf_count)
        self.shelf_chip.setToolTip("Itens na estante")
        self.shelf_chip.hide()

        # Privacidade: laranja = microfone em uso, verde = câmera em uso.
        self.mic_dot, self.cam_dot = privacy_dot(MIC_COLOR), privacy_dot(CAM_COLOR)
        self.privacy_box = QWidget()
        privacy_row = QHBoxLayout(self.privacy_box)
        privacy_row.setContentsMargins(0, 0, 0, 0)
        privacy_row.setSpacing(5)
        privacy_row.addWidget(self.mic_dot)
        privacy_row.addWidget(self.cam_dot)
        self.privacy_box.hide()

        self.caffeine_icon = QLabel()
        self.caffeine_icon.setFixedSize(15, 15)
        self.caffeine_icon.setPixmap(icons.pixmap("coffee", theme.SUN, 15))
        self.caffeine_icon.hide()
        self.net_off_icon = QLabel()
        self.net_off_icon.setFixedSize(14, 14)
        self.net_off_icon.setPixmap(icons.pixmap("wifi-off", theme.WARN, 14))
        self.net_off_icon.hide()
        self.mute_icon = QLabel()
        self.mute_icon.setFixedSize(14, 14)
        self.mute_icon.setPixmap(icons.pixmap("volume-x", theme.TEXT_2, 14))
        self.mute_icon.hide()
        self.bat_low_icon = QLabel()
        self.bat_low_icon.setFixedSize(14, 14)
        self.bat_low_icon.setPixmap(icons.pixmap("battery-low", theme.WARN, 14))
        self.bat_low_icon.hide()

        self.status_box = QWidget()
        status_row = QHBoxLayout(self.status_box)
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(5)
        for icon_w in (self.caffeine_icon, self.net_off_icon, self.mute_icon, self.bat_low_icon):
            status_row.addWidget(icon_w)
        self.status_box.hide()
        self.dot = StatusDot(self)

        self.metrics_chip = label("", "")
        self.metrics_chip.setFont(theme.font(12, theme.SEMI, tabular=True))
        self.metrics_chip.setStyleSheet(f"color: {theme.TEXT_2};")
        self.metrics_chip.setToolTip("Sistema ao vivo (clique para abrir)")
        self.metrics_chip.hide()
        self.metrics: list[str] = []

        self._has_shelf = False
        self._has_metrics = False
        self._has_weather = False
        self._caffeine_on = False
        self._status = StatusState()
        self._overflow_hidden: set[str] = set()

        row.addWidget(self.privacy_box)
        row.addWidget(self.art)
        row.addWidget(self.date)
        row.addStretch(1)
        first_gap = row.count() - 1
        row.addWidget(self.title, 1)
        row.addWidget(self.eq)
        row.addWidget(self.event_chip)
        row.addWidget(self.timer_chip)
        row.addWidget(self.shelf_chip)
        row.addWidget(self.metrics_chip)
        row.addWidget(self.weather)
        row.addWidget(self.time)
        row.addStretch(1)
        self._gaps = (first_gap, row.count() - 1)
        self.row = row
        self.ring_box = QWidget()
        ring_row = QHBoxLayout(self.ring_box)
        ring_row.setContentsMargins(4, 0, 0, 0)
        ring_row.setSpacing(6)
        for ring in self.rings.values():
            ring_row.addWidget(ring)
        row.addWidget(self.ring_box)
        row.addWidget(self.status_box)
        row.addWidget(self.dot)
        for widget in (
            self.art,
            self.date,
            self.title,
            self.eq,
            self.time,
            self.weather,
            self.timer_chip,
            self.event_chip,
            self.shelf_chip,
            self.status_box,
            self.dot,
            self.metrics_chip,
        ):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.media: MediaState | None = None
        self.set_media(None)

    def set_shell_mode(self, on: bool) -> None:
        self.shell_mode = on
        self.dot.setVisible(on)
        self.caffeine_icon.setPixmap(icons.pixmap("coffee", theme.TEXT_2 if on else theme.SUN, 15))
        self._sync_status_box()

    def set_status(self, status: StatusState) -> None:
        self._status = status
        self._sync_status_box()

    def _sync_status_box(self) -> None:
        net_off = self.shell_mode and self._status.network not in ("wifi", "ethernet")
        muted = self.shell_mode and self._status.speaker_muted
        bat_low = self.shell_mode and self._status.battery_low
        self.caffeine_icon.setVisible(self._caffeine_on)
        self.net_off_icon.setVisible(net_off)
        self.mute_icon.setVisible(muted)
        self.bat_low_icon.setVisible(bat_low)
        self.dot.set_alert(net_off or bat_low)
        self.status_box.setVisible(self._caffeine_on or net_off or muted or bat_low)
        if self.shell_mode:
            parts = []
            if net_off:
                parts.append("Sem rede")
            if muted:
                parts.append("Áudio mudo")
            if bat_low and self._status.battery_pct is not None:
                parts.append(f"Bateria {self._status.battery_pct}%")
            tip = " · ".join(parts) + "\nControles (Win + A)" if parts else "Controles (Win + A)"
            self.dot.setToolTip(tip)

    # ---------- privacidade, cafeína e métricas ----------
    def set_privacy(self, state: PrivacyState, enabled: bool = True) -> None:
        mic, cam = bool(state.mic) and enabled, bool(state.cam) and enabled
        self.mic_dot.setVisible(mic)
        self.cam_dot.setVisible(cam)
        self.mic_dot.setToolTip(state.text("mic") + "\nClique para mutar/ativar o microfone")
        self.cam_dot.setToolTip(state.text("cam"))
        self.privacy_box.setVisible(mic or cam)

    def set_mic_muted(self, muted: bool) -> None:
        self.mic_dot.set_muted(muted)

    def set_caffeine(self, active: bool, tip: str = "") -> None:
        self._caffeine_on = bool(active)
        self.caffeine_icon.setToolTip(tip)
        self._sync_status_box()

    def set_metrics(self, metrics: list[str], snap: Snapshot | None) -> None:
        """Chip ao vivo das métricas fixadas (ex.: 'CPU 22%   GPU 75% · 64°   ↓ 12,4 MB/s')."""
        self.metrics = list(metrics)
        parts = hardware.pill_parts(metrics, snap)
        self._has_metrics = bool(parts)
        self.metrics_chip.setText(METRIC_GAP.join(parts))
        self.metrics_chip.setVisible(self._has_metrics and "metrics" not in self._overflow_hidden)
        if snap is not None:
            alert = (snap.gpu.temp or 0) >= hardware.TEMP_LIMIT or (snap.ram_pct or 0) >= hardware.RAM_LIMIT
            set_qss(self.metrics_chip, f"color: {theme.WARN if alert else theme.TEXT_2};")

    def set_upcoming_event(self, event: object | None) -> bool:
        before = not self.event_chip.isHidden()
        self._upcoming_event = event
        self._event_zero_pulsed = False
        self._update_event_chip()
        return before != (not self.event_chip.isHidden())

    def _update_event_chip(self) -> bool:
        """Atualiza contagem do compromisso ativo (10 min antes a 2 min depois). True se zerou."""
        event = self._upcoming_event
        if event is None or getattr(event, "start", None) is None:
            self.event_chip.hide()
            return False
        starts = datetime.combine(event.day, event.start)  # type: ignore[attr-defined]
        delta_min = (starts - datetime.now()).total_seconds() / 60.0
        if delta_min < -2.0:
            self._upcoming_event = None
            self.event_chip.hide()
            return False
        if delta_min > 10.5:
            self.event_chip.hide()
            return False
        if delta_min > 0.5:
            txt = f"{event.start:%H:%M} · em {max(1, round(delta_min))} min"  # type: ignore[attr-defined]
        elif delta_min >= -0.5:
            txt = f"{event.start:%H:%M} · agora"  # type: ignore[attr-defined]
        else:
            txt = f"{event.start:%H:%M} · começou"  # type: ignore[attr-defined]
        self.event_chip.setText(txt)
        self.event_chip.setToolTip(f"{event.title} ({event.when})")  # type: ignore[attr-defined]
        self.event_chip.show()
        if delta_min <= 0.0 and not self._event_zero_pulsed:
            self._event_zero_pulsed = True
            return True
        return False

    @property
    def playing(self) -> bool:
        return self.media is not None and self.media.is_playing

    @property
    def has_media(self) -> bool:
        """Tem música (tocando ou pausada): a ilha mostra capa e título."""
        return self.media is not None

    def refresh_clock(self) -> bool:
        now = datetime.now()
        self.time.setText(clock(now, self.ctx.settings.clock_24h))
        self.date.setText(f"{WEEKDAYS_SHORT[now.weekday()]} {now.day}")
        zero_pulse = self._update_event_chip()
        if self.playing:
            self.update()  # linha de andamento da música
        return zero_pulse

    def set_media(self, state: MediaState | None) -> None:
        self.media = state
        shown, playing = self.has_media, self.playing
        self.art.setVisible(shown)
        self.title.setVisible(shown)
        self.eq.setVisible(shown)
        self.eq.set_playing(playing)
        self.eq.set_dim(not playing)
        show_date = (
            not shown and self.ctx.settings.pill_date and "date" not in self._overflow_hidden
        )
        self.date.setVisible(show_date)
        for index in self._gaps:  # com música, o título ocupa o espaço livre
            self.row.setStretch(index, 0 if shown else 1)
        if state:
            color = theme.TEXT if playing else theme.TEXT_2
            set_qss(self.title, f"color: {color};")
            self.title.setText(f"{state.title}  ·  {state.artist}" if state.artist else state.title)
            self.art.setGraphicsEffect(None if playing else self._dim_effect())
            self.setToolTip("" if playing else "Pausado")

    def _dim_effect(self) -> QGraphicsOpacityEffect:
        effect = QGraphicsOpacityEffect(self.art)
        effect.setOpacity(0.55)
        return effect

    def set_art(self, data: bytes) -> None:
        art = rounded_pixmap(data, 22, 6)
        self.art.setPixmap(art if art else icons.pixmap("music", theme.ACCENT, 16))

    def _ring_shown(self, provider: str) -> bool:
        settings = self.ctx.settings
        return (
            settings.pill_rings
            and settings.ring_enabled(provider)
            and self.ctx.limits.report(provider).state != "off"
        )

    def visible_rings(self) -> int:
        return sum(self._ring_shown(p) for p in self.rings)

    def refresh_rings(self) -> None:
        for provider, ring in self.rings.items():
            ring.setVisible(self._ring_shown(provider))
            if ring.isVisibleTo(self):
                report = self.ctx.limits.report(provider)
                fill_ring(ring, report, self.ctx.limits.headline(report), compact=True)
        self.ring_box.setVisible(self.visible_rings() > 0)

    def set_shelf(self, count: int) -> None:
        self._has_shelf = count > 0
        self.shelf_count.setText(str(count))
        self.shelf_chip.setVisible(self._has_shelf and "shelf" not in self._overflow_hidden)

    def set_timer(self, state: TimerState | None) -> None:
        """Contagem do timer/Pomodoro ao lado da hora, com anel de progresso."""
        if state is None:
            self.timer_chip.hide()
            return
        color = theme.SKY if state.kind == "break" else theme.ACCENT
        self.timer_ring.set_value(state.progress(), color, dim=not state.running, animate=False)
        self.timer_text.setText(countdown(state.left()))
        set_qss(self.timer_text, f"color: {color if state.running else theme.TEXT_2};")
        self.timer_chip.setToolTip(f"{state.label}" + ("" if state.running else " (pausado)"))
        self.timer_chip.show()

    def set_weather(self, forecast: Forecast | None) -> None:
        """Clima de agora ao lado da hora (some sem cidade ou com o ajuste desligado)."""
        if forecast is None or not self.ctx.settings.pill_weather:
            self._has_weather = False
            self.weather.hide()
            return
        self._has_weather = True
        rain = forecast.rain_soon()
        glyph = "rain" if rain else forecast.icon
        self.weather_icon.setPixmap(icons.weather(glyph, 16))
        self.weather_temp.setText(f"{forecast.temp:.0f}°")
        self.weather_rain.setText(f"{rain}%" if rain else "")
        self.weather_rain.setVisible(bool(rain))
        tip = f"{forecast.place}: {forecast.temp:.0f}°, {forecast.description}"
        if rain:
            tip += f"\nChuva provável na próxima hora ({rain}%)"
        self.weather.setToolTip(tip)
        self.weather.setVisible("weather" not in self._overflow_hidden)

    @staticmethod
    def _text_width(widget: QLabel, text: str | None = None) -> int:
        widget.ensurePolished()
        return widget.fontMetrics().horizontalAdvance(widget.text() if text is None else text) + 2

    def _compute_parts(self, hidden: set[str], title_cap: int = 250) -> list[int]:
        parts: list[int] = []
        if not self.privacy_box.isHidden():
            dots = (not self.mic_dot.isHidden()) + (not self.cam_dot.isHidden())
            parts.append(dots * 8 + max(0, dots - 1) * 5)
        if self.has_media:
            title = self.title.full_text()
            parts += [22, max(80, min(self._text_width(self.title, title), title_cap)), 14]
        elif self.ctx.settings.pill_date and "date" not in hidden:
            parts.append(self._text_width(self.date))
        if self._upcoming_event is not None and not self.event_chip.isHidden():
            parts.append(self._text_width(self.event_chip) + 16)
        if not self.timer_chip.isHidden():
            parts.append(15 + 5 + self._text_width(self.timer_text, "00:00"))
        if self._has_shelf and "shelf" not in hidden:
            parts.append(14 + 3 + self._text_width(self.shelf_count))
        if self._has_metrics and "metrics" not in hidden:
            template = METRIC_GAP.join(hardware.pill_parts(self.metrics, None, template=True))
            parts.append(self._text_width(self.metrics_chip, template))
        if self._has_weather and "weather" not in hidden:
            width = 16 + 4 + self._text_width(self.weather_temp)
            if not self.weather_rain.isHidden():
                width += 4 + self._text_width(self.weather_rain)
            parts.append(width)
        parts.append(self._text_width(self.time, self.time.text() or "00:00"))
        rings = self.visible_rings()
        if rings:
            parts.append(4 + rings * 16 + max(0, rings - 1) * 6)
        if not self.status_box.isHidden():
            icons_n = sum(
                not w.isHidden()
                for w in (self.caffeine_icon, self.net_off_icon, self.mute_icon, self.bat_low_icon)
            )
            parts.append(icons_n * 15 + max(0, icons_n - 1) * 5)
        if self.shell_mode:
            parts.append(10)  # Ponto
        return parts

    def _total_for(self, parts: list[int]) -> int:
        margins = self.row.contentsMargins()
        spacing = self.row.spacing()
        breathing = 10 if self.has_media else 34
        return margins.left() + margins.right() + sum(parts) + spacing * max(0, len(parts) - 1) + breathing

    def preferred_width(self) -> int:
        """Largura com ordem de corte: estante → clima → data → título → métricas."""
        max_w = 720 if self._has_metrics else 560
        hidden: set[str] = set()
        total = self._total_for(self._compute_parts(hidden))
        for item in ("shelf", "weather", "date"):
            if total <= max_w:
                break
            hidden.add(item)
            total = self._total_for(self._compute_parts(hidden))
        if total > max_w and self.has_media:
            total = self._total_for(self._compute_parts(hidden, title_cap=80))
        if total > max_w and self._has_metrics:
            hidden.add("metrics")
            total = self._total_for(self._compute_parts(hidden, title_cap=80 if self.has_media else 250))
        self._overflow_hidden = hidden
        self.shelf_chip.setVisible(self._has_shelf and "shelf" not in hidden)
        self.metrics_chip.setVisible(self._has_metrics and "metrics" not in hidden)
        self.weather.setVisible(self._has_weather and "weather" not in hidden)
        self.date.setVisible(not self.has_media and self.ctx.settings.pill_date and "date" not in hidden)
        return max(150, min(total, max_w))

    def zone_at(self, pos: QPoint) -> str:
        """Qual parte da pílula está sob o ponto: status, rings, weather, metrics, mic, cam, media..."""

        def hit(widget: QWidget, pad: int = 4) -> bool:
            if not widget.isVisibleTo(self):
                return False
            rect = QRect(widget.mapTo(self, QPoint(0, 0)), widget.size())
            return rect.adjusted(-pad, -20, pad, 20).contains(pos)

        if self.shell_mode and (hit(self.dot, 6) or hit(self.status_box, 4)):
            return "status"
        if hit(self.mic_dot, 3):
            return "mic"
        if hit(self.cam_dot, 3):
            return "cam"
        if hit(self.ring_box):
            return "rings"
        if hit(self.metrics_chip):
            return "metrics"
        if not self.shell_mode and hit(self.caffeine_icon):
            return "caffeine"
        if hit(self.weather):
            return "weather"
        if hit(self.shelf_chip):
            return "shelf"
        if hit(self.event_chip):
            return "event"
        if hit(self.timer_chip):
            return "timer"
        if self.has_media and (hit(self.art) or hit(self.eq)):
            return "media"
        if self.has_media and hit(self.title, 0):
            return "title"
        if hit(self.date):
            return "date"
        if hit(self.time):
            return "time"
        return ""

    def paintEvent(self, _event: object) -> None:
        """Linha fina com o andamento da música, na base da pílula."""
        state = self.media
        if not state or state.duration_s <= 0 or not self.ctx.settings.pill_progress:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right = 20.0, self.width() - 20.0
        y = self.height() - 2.5
        pen = QPen(theme.color(theme.STROKE_2, 0.9), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(left, y), QPointF(right, y))
        done = max(0.0, min(1.0, state.position_at() / state.duration_s))
        if done > 0:
            if state.is_playing:
                pen.setBrush(theme.accent_gradient(left, 0, right, 0, 0.95))
            else:
                pen.setColor(theme.color(theme.TEXT_2, 0.9))
            p.setPen(pen)
            p.drawLine(QPointF(left, y), QPointF(left + (right - left) * done, y))


DROP_ZONES = {
    "shelf": ("inbox", "Guardar na estante", "Fica na ilha para arrastar para outro lugar"),
    "shortcuts": ("plus", "Adicionar aos atalhos", "Apps, pastas, arquivos e links fixos"),
    "launcher": ("pin", "Fixar no Lançador", "Apps, pastas, arquivos e links fixos"),
}


class DropHint(QWidget):
    """Enquanto algo é arrastado sobre a ilha: uma ou duas áreas para soltar."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.zones: list[str] = []
        self.active = ""
        self.hide()

    def set_zones(self, zones: list[str]) -> None:
        self.zones = zones
        self.active = zones[0] if len(zones) == 1 else ""
        self.update()

    def _rects(self) -> list[QRectF]:
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if len(self.zones) < 2:
            return [rect]
        half = (rect.width() - 10) / 2
        return [
            QRectF(rect.left(), rect.top(), half, rect.height()),
            QRectF(rect.left() + half + 10, rect.top(), half, rect.height()),
        ]

    def zone_at(self, pos: QPoint) -> str:
        for zone, rect in zip(self.zones, self._rects(), strict=False):
            if rect.contains(QPointF(pos)):
                return zone
        return self.zones[0] if len(self.zones) == 1 else ""

    def set_active(self, zone: str) -> None:
        if zone != self.active:
            self.active = zone
            self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = theme.ISLAND_RADIUS - 6
        for zone, rect in zip(self.zones, self._rects(), strict=False):
            active = zone == self.active
            icon_name, title, text = DROP_ZONES[zone]
            p.setBrush(theme.color(theme.ACCENT_SOFT if active else theme.BG, 0.92 if active else 0.78))
            pen = QPen(
                theme.color(theme.ACCENT, 1.0 if active else 0.55),
                1.8 if active else 1.4,
                Qt.PenStyle.DashLine,
            )
            p.setPen(pen)
            p.drawRoundedRect(rect, radius, radius)
            center = rect.center()
            p.drawPixmap(
                int(center.x() - 17), int(center.y() - 46), icons.pixmap(icon_name, theme.ACCENT, 34)
            )
            p.setPen(theme.color(theme.TEXT))
            p.setFont(theme.font(16, theme.SEMI))
            p.drawText(
                QRectF(rect.left(), center.y() - 4, rect.width(), 24), Qt.AlignmentFlag.AlignCenter, title
            )
            p.setPen(theme.color(theme.TEXT_2))
            p.setFont(theme.font(12))
            p.drawText(
                QRectF(rect.left() + 12, center.y() + 22, rect.width() - 24, 20),
                Qt.AlignmentFlag.AlignCenter,
                text,
            )


class Panel(QWidget):
    """Conteúdo da ilha aberta: abas + páginas criadas sob demanda."""

    tool_requested = Signal(str)  # "color" | "ocr"
    controls_requested = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget) -> None:
        super().__init__(parent)
        self.ctx = ctx
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 12, 16)
        root.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(4)
        self.strip = TabStrip(self)
        self.strip.picked.connect(self.select)
        header.addWidget(self.strip, 1)
        # Ferramentas (cafeína, conta-gotas, texto da tela) moram em Controles; aqui fica só a porta.
        self.controls_btn = IconButton("sliders", "Controles: cafeína, foco, ferramentas…", 32, 16)
        self.controls_btn.clicked.connect(self.controls_requested.emit)
        header.addWidget(self.controls_btn)
        self.power_btn = BadgeButton("power", "Energia: bloquear, suspender, reiniciar, desligar…", 32, 16)
        self.power_btn.clicked.connect(self.show_power)
        ctx.power.changed.connect(self._power_changed)
        header.addWidget(self.power_btn)
        header.addSpacing(6)
        self.clock = label("", "Muted")
        self.clock.setFont(theme.font(12, theme.SEMI, tabular=True))
        self.clock.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header.addWidget(self.clock)
        header.addSpacing(4)
        self.pin = IconButton("pin", "Manter aberto", 32, 15)
        self.pin.setCheckable(True)
        self.pin.toggled.connect(self._pin_changed)
        self.close_btn = IconButton("chevron-up", "Fechar (Esc)", 32, 16)
        header.addWidget(self.pin)
        header.addWidget(self.close_btn)
        root.addLayout(header)
        self.shell_mode = False
        self.stack = QStackedWidget()
        body = QVBoxLayout()
        body.setContentsMargins(4, 0, 4, 0)
        body.addWidget(self.stack)
        root.addLayout(body, 1)
        self.tabs: dict[str, TabButton] = {}
        self._tab_of: dict[str, str] = {}  # página -> chave da aba (grupo) onde ela mora
        self._signature: list[tuple[str, ...]] = []
        self.pages: dict[str, Page] = {}
        self.current = "home"
        self._transition: tuple[QParallelAnimationGroup, QWidget] | None = None
        self.drop_hint = DropHint(self)
        self.command = CommandBar(ctx, self)
        self.command.navigate.connect(self.select)
        self.command.tool.connect(self.tool_requested.emit)
        self.qr = QrCard(self)
        self.power_menu: PowerMenu | None = None
        self.rebuild_tabs()
        self._power_changed(ctx.power.pending)

    # ---------- Ventude Shell ----------
    def set_shell_mode(self, on: bool) -> None:
        """Com o Shell ligado, a Energia mora no Lançador (o botão sai daqui)."""
        self.shell_mode = on
        self.power_btn.setVisible(not on)
        tip = "Controles: cafeína, foco, ferramentas…"
        self.controls_btn.setToolTip(f"{tip} (Win + A)" if on else tip)
        self.rebuild_tabs()
        self.clock.setVisible(not (on and self.current == "home"))

    # ---------- energia ----------
    def _power_changed(self, pending: bool) -> None:
        self.power_btn.set_badge(theme.WARN if pending else "")
        self.power_btn.setToolTip(
            "Energia · atualização do Windows esperando reiniciar"
            if pending
            else "Energia: bloquear, suspender, reiniciar, desligar…"
        )

    def show_power(self) -> None:
        self.power_menu = PowerMenu(self.ctx.power.available(), self, runner=self.ctx.request_power)
        self.power_menu.acted.connect(self._power_done)
        self.power_menu.popup_below(self.power_btn)

    def _power_done(self, result: str) -> None:
        if result.startswith("erro:"):
            self.ctx.tray_message.emit("Energia", result[5:] or "O Windows recusou o pedido.")

    def show_qr(self, text: str) -> None:
        self.command.close_bar()
        self.qr.show_text(text)

    def _pin_changed(self, pinned: bool) -> None:
        self.pin.set_icon("pin", theme.ACCENT if pinned else None)
        self.pin.setToolTip("Fixado: não fecha sozinho" if pinned else "Manter aberto")

    def enabled_keys(self) -> list[str]:
        s = self.ctx.settings
        hide_shortcuts = bool(getattr(s, "replace_start_menu", False))
        return [
            key
            for key, _, _ in MODULES
            if s.module_enabled(key) and not (hide_shortcuts and key == "shortcuts")
        ]

    def _groups(self) -> list[tuple[str, ...]]:
        """Grupos de abas só com as páginas ligadas (a aba some quando o grupo fica vazio)."""
        keys = set(self.enabled_keys())
        groups = [tuple(k for k in group if k in keys) for group in TAB_GROUPS]
        grouped = {k for group in TAB_GROUPS for k in group}
        groups += [(k,) for k in self.enabled_keys() if k not in grouped]  # módulo novo sem grupo
        return [g for g in groups if g]

    def rebuild_tabs(self) -> None:
        groups = self._groups()
        if groups == self._signature:
            return
        self._signature = groups
        info = {key: (title, icon_name) for key, title, icon_name in MODULES}
        self.strip.clear()
        self.tabs.clear()
        self._tab_of.clear()
        for group in groups:
            members = [(k, info[k][0]) for k in group]
            button = TabButton(info[group[0]][1], members)
            self.strip.add(button)
            self.tabs[group[0]] = button
            for k in group:
                self._tab_of[k] = group[0]
        keys = self.enabled_keys()
        for key in list(self.pages):
            if key not in keys:
                page = self.pages.pop(key)
                self.stack.removeWidget(page)
                page.deleteLater()
        self.select(self.current if self.current in keys else "home")

    def page(self, key: str) -> Page | None:
        if key not in self.pages:
            try:
                page = PAGES[key](self.ctx)
            except Exception:  # noqa: BLE001
                log.exception("Não foi possível abrir a aba %s", key)
                return None
            self.pages[key] = page
            self.stack.addWidget(page)
        return self.pages[key]

    def select(self, key: str) -> None:
        if key not in self._tab_of:
            key = "home"
        page = self.page(key)
        if page is None:
            return
        order = [k for group in self._signature for k in group]
        previous = self.current
        changed = self.stack.currentWidget() is not page
        self.current = key
        self.stack.setCurrentWidget(page)
        self.clock.setVisible(not (self.shell_mode and key == "home"))
        # Sem animação de aba enquanto o painel ainda está aparecendo (fade da abertura).
        if changed and previous in order and self.isVisible() and self.graphicsEffect() is None:
            self._slide_in(page, 1 if order.index(key) >= order.index(previous) else -1)
        self.strip.activate(self.tabs.get(self._tab_of[key]), key, self.ctx.settings)
        try:
            page.on_show()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao atualizar a aba %s", key)

    # ---------- animação entre abas ----------
    def _slide_in(self, page: QWidget, direction: int) -> None:
        """A aba nova entra deslizando do lado da aba clicada, com um fade curto."""
        self._end_transition()
        duration = motion.ms("tab", self.ctx.settings)
        if duration <= 0:
            return
        page.setGeometry(self.stack.contentsRect())
        end = page.pos()
        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(0.0)
        page.setGraphicsEffect(effect)
        group = QParallelAnimationGroup(self)
        slide = QPropertyAnimation(page, b"pos", group)
        slide.setDuration(duration)
        slide.setStartValue(end + QPoint(22 * direction, 0))
        slide.setEndValue(end)
        slide.setEasingCurve(motion.easing("enfase"))
        fade = QPropertyAnimation(effect, b"opacity", group)
        fade.setDuration(int(duration * 0.7))
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(motion.easing("padrao"))
        group.finished.connect(self._end_transition)
        self._transition = (group, page)
        group.start()

    def _end_transition(self) -> None:
        if not self._transition:
            return
        group, page = self._transition
        self._transition = None
        group.stop()
        page.setGraphicsEffect(None)
        page.setGeometry(self.stack.contentsRect())
        group.deleteLater()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        self.drop_hint.setGeometry(self.rect().adjusted(6, 6, -6, -6))
        if self.qr.isVisible():
            self.qr.setGeometry(self.rect().adjusted(6, 6, -6, -6))

    def select_index(self, index: int) -> None:
        buttons = list(self.tabs.values())
        if 0 <= index < len(buttons):
            self.select(buttons[index].current)

    def refresh_clock(self) -> None:
        self.clock.setText(clock(datetime.now(), self.ctx.settings.clock_24h))


class Island(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.ctx = ctx
        ctx.island = self
        self.setWindowTitle("Ventude")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)  # arrastar apps, pastas e links para os Atalhos/Lançador
        self.mode = IslandMode.PILL
        self._drag_files = False
        self._media_hold = False  # usou a roda/clique na música: não abre por hover
        self._hover_suspended = False
        self._controls_by_key = False
        self._suppress_caffeine_toast = False
        self._suppress_mic_toast = False
        self._hover_zone = ""
        self._holds = 0
        self._press: QPoint | None = None
        self._origin = QPoint()
        self._dragging = False
        self._auto_hidden = False
        self._user_hidden = False
        self._hole: object | None = None  # BlackHole rodando (Win 3 vezes)
        self._hole_pixmap: QPixmap | None = None
        self._surface: tuple[tuple[int, int, bool, float], QPixmap] | None = None
        self._quitting = False
        self._collapsed_at = time.time()
        self._hotkey = False
        self.pill = Pill(ctx, self)
        self.panel = Panel(ctx, self)
        self.panel.hide()
        self.controls = ControlsBody(ctx, self)
        self.controls.hide()
        self.toast_view = ToastView(self)
        self.volume_overlay = VolumeOverlay(self)
        self._toast: Toast | None = None
        self._toasts: list[Toast] = []
        self._deferred_toasts: list[Toast] = []
        self._last_track: tuple[str, str] | None = None
        self.toast_timer = QTimer(self, singleShot=True)
        self.toast_timer.timeout.connect(self._end_toast)
        self.panel.close_btn.clicked.connect(self.collapse)
        self.panel.controls_requested.connect(lambda: self.open_controls(by_key=False))
        self.controls.close_requested.connect(self.collapse)
        self.controls.size_changed.connect(self._resize_controls)
        self.controls.open_audio.connect(lambda: self.expand("audio"))
        self.controls.caffeine_clicked.connect(lambda: setattr(self, "_suppress_caffeine_toast", True))
        self.controls.mic_clicked.connect(lambda: setattr(self, "_suppress_mic_toast", True))
        self.controls.tool_requested.connect(self.run_tool)
        self.anim = QPropertyAnimation(self, b"geometry", self)
        self.anim.setEasingCurve(motion.easing("padrao"))
        self.anim.finished.connect(self._settled)
        self.fade = QPropertyAnimation(self)
        self.hover_timer = QTimer(self, singleShot=True, interval=0)
        self.hover_timer.timeout.connect(self._hover_expand)
        self.collapse_timer = QTimer(self, singleShot=True)
        self.collapse_timer.timeout.connect(self._try_collapse)
        self.clock_timer = QTimer(self, interval=1000)
        self.clock_timer.timeout.connect(self._tick)
        self.clock_timer.start()
        self.fullscreen_timer = QTimer(self, interval=700)
        self.fullscreen_timer.timeout.connect(self._check_fullscreen)
        if system.autostart_supported():
            self.fullscreen_timer.start()

        ctx.hold_open.connect(self._hold)
        ctx.settings_changed.connect(self._settings_changed)
        ctx.navigate_requested.connect(self.expand)
        ctx.reset_position_requested.connect(self.reset_position)
        ctx.media.state_changed.connect(self._media_changed)
        ctx.media.thumbnail_changed.connect(self.pill.set_art)
        ctx.limits.changed.connect(lambda _p: self._rings_changed())
        ctx.limits.ticked.connect(self._rings_changed)
        self.pill.set_art(b"")
        self._tick()
        self.pill.refresh_rings()
        self.setGeometry(self.target_rect(IslandMode.PILL))
        ctx.weather_changed.connect(self._weather_done)
        self._weather_done(ctx.forecast)
        ctx.limits.alert.connect(self._limit_alert)
        ctx.timer.changed.connect(self._timer_changed)
        ctx.shelf.changed.connect(self._shelf_changed)
        self.panel.tool_requested.connect(self.run_tool)
        self._capture: CaptureSession | None = None
        ctx.timer.finished.connect(self._timer_finished)
        self.pulse = QVariantAnimation(self)
        self.pulse.setDuration(1800)
        self.pulse.setStartValue(0.0)
        self.pulse.setEndValue(1.0)
        self.pulse.valueChanged.connect(lambda _v: self.update())
        ctx.reminders.due.connect(self._reminder)
        ctx.media.thumbnail_changed.connect(self._toast_art)
        # ---------- Sistema, áudio, privacidade, cafeína, QR e tecla Windows ----------
        ctx.hardware.updated.connect(self._metrics_changed)
        ctx.hardware.alert.connect(self._hardware_alert)
        ctx.privacy.changed.connect(self._privacy_changed)
        ctx.caffeine.changed.connect(lambda _on: self._caffeine_changed())
        ctx.audio.master_feedback.connect(self._volume_feedback)
        ctx.audio.mic_feedback.connect(self._mic_feedback)
        ctx.qr_requested.connect(self.show_qr)
        ctx.tool_requested.connect(self.run_tool)
        self._metrics_changed(ctx.hardware.last)

    @property
    def expanded(self) -> bool:
        return self.mode == IslandMode.PANEL

    @property
    def open_body(self) -> bool:
        return self.mode in (IslandMode.PANEL, IslandMode.CONTROLS)

    # ---------- Sistema (métricas fixadas), privacidade, estado e cafeína na pílula ----------
    def _metrics_changed(self, snap: Snapshot | None = None) -> None:
        metrics = self.ctx.settings.pinned_metrics()
        before = (self.pill.metrics_chip.isHidden(), list(self.pill.metrics))
        self.pill.set_metrics(metrics, snap if snap is not None else self.ctx.hardware.last)
        if before != (self.pill.metrics_chip.isHidden(), metrics):
            self._resize_compact()

    def _hardware_alert(self, kind: str, title: str, text: str) -> None:
        icon = "memory" if kind == "ram" else "thermo"
        self.show_toast(Toast(title, text, icon=icon, color=theme.DANGER, seconds=6, kind="hardware"))

    def _privacy_changed(self, state: PrivacyState | None = None) -> None:
        before = self.pill.privacy_box.isHidden()
        self.pill.set_privacy(state or self.ctx.privacy.state, self.ctx.settings.pill_privacy)
        if before != self.pill.privacy_box.isHidden():
            self._resize_compact()

    def _caffeine_changed(self) -> None:
        caffeine = self.ctx.caffeine
        before = self.pill.caffeine_icon.isHidden()
        self.pill.set_caffeine(caffeine.active, caffeine.describe())
        if before != self.pill.caffeine_icon.isHidden():
            self._resize_compact()
        if self._suppress_caffeine_toast:
            self._suppress_caffeine_toast = False
            return
        self.show_toast(
            Toast(
                "Cafeína ligada" if caffeine.active else "Cafeína desligada",
                caffeine.describe() if caffeine.active else "O Windows volta a apagar a tela e suspender",
                icon="coffee",
                color=theme.SUN,
                seconds=2.5,
                kind="tool",
            )
        )

    def _volume_feedback(self, level: float, muted: bool) -> None:
        """Depois da roda do mouse na pílula: barra dentro da pílula por 1,2 s."""
        if self.mode == IslandMode.PILL:
            self.volume_overlay.show_volume(level, muted, self.ctx.settings)

    def _mic_feedback(self, muted: bool) -> None:
        self.pill.set_mic_muted(muted)
        if self._suppress_mic_toast:
            self._suppress_mic_toast = False
            return
        if self.panel.shell_mode:
            return
        self.show_toast(
            Toast(
                "Microfone mutado" if muted else "Microfone ativo",
                "Clique no ponto laranja da ilha para mudar" if muted else "",
                icon="mic-off" if muted else "mic",
                color=theme.DANGER if muted else MIC_COLOR,
                seconds=2.2,
                kind="tool",
            )
        )

    def show_qr(self, text: str) -> None:
        self.expand()
        self.panel.show_qr(text)

    # ---------- Busca Universal (na ilha) e Ventude Shell ----------
    def open_start(self, toggle: bool = False, text: str = "") -> None:
        """Busca dentro da ilha (sem o Shell). Com o Shell, abre o Lançador."""
        s = self.ctx.settings
        if (s.replace_start_menu or s.replace_taskbar) and self.ctx.open_search(text):
            return
        if toggle and self.expanded and self.panel.command.isVisible():
            self.collapse()
            return
        if not self.isVisible():
            self.user_hidden(False)
        self.expand()
        self.raise_()
        self.activateWindow()
        from ventude.services.winutil import force_foreground

        force_foreground(int(self.winId()))
        self.panel.command.open_bar(text, start=True)

    def set_shell_mode(self, on: bool) -> None:
        self.panel.set_shell_mode(on)
        self.pill.set_shell_mode(on)
        self._resize_compact()

    def set_status(self, status: StatusState) -> None:
        before = self.pill.status_box.isHidden()
        self.pill.set_status(status)
        self.controls.set_network(status.network, status.wifi)
        if before != self.pill.status_box.isHidden():
            self._resize_compact()

    def suspend_hover(self, on: bool) -> None:
        self._hover_suspended = bool(on)
        if on:
            self.hover_timer.stop()
        elif self.mode == IslandMode.PILL and self._deferred_toasts:
            QTimer.singleShot(250, self._drain_deferred_toasts)

    def _search_elsewhere(self, text: str = "") -> bool:
        """No Shell, digitar na ilha (ou Ctrl + K) abre o Lançador."""
        if not self.ctx.settings.replace_start_menu or self.ctx.shell is None:
            return False
        self.collapse()
        return self.ctx.open_search(text)

    def show_shell_settings(self) -> None:
        page = self.panel.pages.get("settings")
        if page is not None and hasattr(page, "scroll_to_shell"):
            page.scroll_to_shell()

    # ---------- clima na ilha ----------
    def _weather_done(self, forecast: Forecast | None) -> None:
        before = self.pill.weather.isHidden()
        self.pill.set_weather(forecast)
        if before != self.pill.weather.isHidden():
            self._resize_compact()

    # ---------- geometria ----------
    def _sizes(self, mode: IslandMode | bool) -> tuple[int, int]:
        if mode is True or mode == IslandMode.PANEL:
            return EXPANDED.width(), EXPANDED.height()
        if mode == IslandMode.CONTROLS:
            return CONTROLS_WIDTH, self.controls.preferred_height()
        if self._toast is not None or mode == IslandMode.TOAST:
            return TOAST_WIDTH, TOAST_HEIGHT
        return self.pill.preferred_width(), COMPACT_HEIGHT

    def _anchor(self) -> tuple[QRect, QPoint]:
        try:
            saved = json.loads(self.ctx.settings.island_position_json)
            anchor = QPoint(int(saved["x"]), int(saved["y"]))
            for screen in QGuiApplication.screens():
                if screen.geometry().contains(anchor):
                    return screen.availableGeometry(), anchor
        except (ValueError, TypeError, KeyError, OverflowError):
            pass
        area = screen_for(self.ctx.settings.monitor_index).availableGeometry()
        return area, QPoint(area.center().x(), area.top() + TOP_MARGIN)

    def target_rect(self, mode: IslandMode | bool) -> QRect:
        area, anchor = self._anchor()
        return anchored_rect(area, *self._sizes(mode), anchor)

    def reposition(self) -> None:
        self.anim.stop()
        self.setGeometry(self.target_rect(self.mode))
        self._settled()

    def reset_position(self) -> None:
        self.ctx.update_settings(island_position_json="")
        self.reposition()

    def _animate(self, target: QRect, *, opening: bool = True) -> None:
        self.anim.stop()
        if self.geometry() == target:
            self._settled()
            return
        dur = motion.ms("morfose_open" if opening else "morfose_close", self.ctx.settings)
        if dur <= 0:
            self.setGeometry(target)
            self._settled()
            return
        self.anim.setDuration(dur)
        # Abrindo, a ilha passa um tiquinho do tamanho final e assenta (mola); fechando, recolhe direto.
        growing = target.width() * target.height() >= self.width() * self.height()
        self.anim.setEasingCurve(motion.easing("mola" if opening and growing else "padrao"))
        self.anim.setStartValue(self.geometry())
        self.anim.setEndValue(target)
        self.anim.start()

    def _active_body(self) -> QWidget | None:
        if self.mode == IslandMode.PANEL:
            return self.panel
        if self.mode == IslandMode.CONTROLS:
            return self.controls
        return None

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        self.pill.setGeometry(self.rect())
        self.toast_view.setGeometry(self.rect())
        self.volume_overlay.place_in(self.rect())
        self._layout_panel()

    def moveEvent(self, event: object) -> None:
        super().moveEvent(event)
        if self._revealing():
            self._layout_panel()

    def _revealing(self) -> bool:
        return self.open_body and self.anim.state() == QPropertyAnimation.State.Running

    def _layout_panel(self) -> None:
        """Abrindo: o corpo (painel ou Controles) fica no tamanho final e a ilha crescendo o revela."""
        body = self._active_body()
        if body is None:
            self.panel.clearMask()
            self.controls.clearMask()
            self.panel.setGeometry(self.rect())
            self.controls.setGeometry(self.rect())
            return
        if not self._revealing():
            body.clearMask()
            body.setGeometry(self.rect())
            return
        target = self.anim.endValue()
        offset = target.topLeft() - self.pos()
        body.setGeometry(QRect(offset, target.size()))
        radius = min(theme.ISLAND_RADIUS, self.height() / 2)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), radius, radius)
        region = QRegion(path.toFillPolygon().toPolygon())
        region.translate(-offset)
        body.setMask(region)

    # ---------- abrir e fechar ----------
    def open_controls(self, by_key: bool = False) -> None:
        if self._dragging:
            return
        if self.mode == IslandMode.CONTROLS:
            if by_key:
                self.collapse()
            return
        if not self.isVisible():
            self.show()
        self.hover_timer.stop()
        self.collapse_timer.stop()
        self._cancel_toasts()
        self.volume_overlay.dismiss_now()
        self.panel.command.close_bar()
        self.panel.pin.setChecked(False)
        self.panel.hide()
        self.pill.hide()
        self._controls_by_key = by_key
        self.mode = IslandMode.CONTROLS
        self.controls.refresh_clock()
        self.controls.refresh()
        self._animate(self.target_rect(IslandMode.CONTROLS), opening=True)
        if self._revealing():
            self._layout_panel()
            self._fade_in(self.controls, motion.ms("morfose_open", self.ctx.settings))
        if by_key:
            self.raise_()
            self.activateWindow()
            self.setFocus()
            try:
                from ventude.services.winutil import force_foreground

                force_foreground(int(self.winId()))
            except Exception:  # noqa: BLE001
                pass

    def _resize_controls(self) -> None:
        if self.mode == IslandMode.CONTROLS and not self._dragging:
            self._animate(self.target_rect(IslandMode.CONTROLS), opening=True)

    def expand(self, page: str | None = None) -> None:
        if self._dragging:
            return
        if not self.isVisible():
            self.show()
        self.hover_timer.stop()
        self.collapse_timer.stop()
        if page:
            self.panel.select(page)
        elif not self.expanded and time.time() - self._collapsed_at > BACK_HOME_AFTER:
            self.panel.select("home")
        if self.expanded:
            return
        self._cancel_toasts()
        self.volume_overlay.dismiss_now()
        self.controls.hide()
        self._controls_by_key = False
        self.mode = IslandMode.PANEL
        self.pill.hide()
        self.panel.refresh_clock()
        self._animate(self.target_rect(IslandMode.PANEL), opening=True)
        if self._revealing():  # o conteúdo aparece junto com a animação, sem esperar ela acabar
            self._layout_panel()
            dur = motion.ms("morfose_open", self.ctx.settings)
            self._fade_in(self.panel, dur)

    def collapse(self) -> None:
        if self.mode in (IslandMode.PILL, IslandMode.TOAST):
            return
        self.mode = IslandMode.PILL
        self._controls_by_key = False
        self._collapsed_at = time.time()
        self.panel.command.close_bar()
        self.panel.pin.setChecked(False)
        self.panel.hide()
        self.controls.hide()
        self.pill.hide()
        focus = QApplication.focusWidget()
        if focus and self.isAncestorOf(focus):
            focus.clearFocus()
        self._animate(self.target_rect(IslandMode.PILL), opening=False)

    def toggle(self) -> None:
        if not self.isVisible():
            self.show()
        if self.open_body:
            self.collapse()
        else:
            self.expand()
            self.raise_()
            self.activateWindow()

    def _settled(self) -> None:
        if self.anim.state() == QPropertyAnimation.State.Running:
            return
        repaint = getattr(getattr(self.ctx, "shell", None), "_repaint_docks", None)
        if callable(repaint):
            repaint()  # o botão Controles da Doca acompanha a ilha
        self._layout_panel()
        if self.mode == IslandMode.PANEL:
            if not self.panel.isVisible():
                self._fade_in(self.panel, motion.ms("morfose_open", self.ctx.settings))
        elif self.mode == IslandMode.CONTROLS:
            if not self.controls.isVisible():
                self._fade_in(self.controls, motion.ms("morfose_open", self.ctx.settings))
        elif self._toast is not None:
            self.mode = IslandMode.TOAST
            self.pill.hide()
            if not self.toast_view.isVisible():
                self._fade_in(self.toast_view, motion.ms("desdobrar_fade", self.ctx.settings))
        else:
            self.mode = IslandMode.PILL
            self.toast_view.hide()
            self.pill.show()
            if self._deferred_toasts and not self._hover_suspended:
                QTimer.singleShot(250, self._drain_deferred_toasts)
        self.update()

    def _fade_in(self, widget: QWidget, duration: int = 140) -> None:
        self.fade.stop()
        if duration <= 0 or motion.scale(self.ctx.settings) <= 0:
            widget.setGraphicsEffect(None)
            widget.show()
            return
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        widget.show()
        self.fade = QPropertyAnimation(effect, b"opacity", self)
        self.fade.setDuration(duration)
        self.fade.setStartValue(0.0)
        self.fade.setEndValue(1.0)
        self.fade.setEasingCurve(motion.easing("suave"))
        self.fade.finished.connect(lambda: widget.setGraphicsEffect(None))
        self.fade.start()

    def _keep_open(self) -> bool:
        if self.mode == IslandMode.PANEL:
            if self.panel.pin.isChecked():
                return True
            if self.panel.command.isVisible() or self.panel.qr.isVisible():
                return True
        if self.mode == IslandMode.CONTROLS and self._controls_by_key and self.isActiveWindow():
            return True
        if self._holds > 0 or self._dragging or self._drag_files:
            return True
        if QApplication.activePopupWidget() or QApplication.activeModalWidget():
            return True
        if self.geometry().contains(QCursor.pos()):
            return True
        focus = QApplication.focusWidget()
        if focus and self.isAncestorOf(focus):
            if isinstance(focus, QLineEdit) and not focus.isReadOnly() and focus.text().strip():
                return True
            if isinstance(focus, QPlainTextEdit) and not focus.isReadOnly():
                return True
        return False

    def _try_collapse(self) -> None:
        if not self.open_body:
            return
        if self._keep_open():
            self.collapse_timer.start(max(400, self.ctx.settings.collapse_delay_ms))
            return
        self.collapse()

    def _hold(self, on: bool) -> None:
        self._holds = max(0, self._holds + (1 if on else -1))
        if on:
            self.collapse_timer.stop()
        elif self.open_body:
            self.collapse_timer.start(max(600, self.ctx.settings.collapse_delay_ms))

    # ---------- zonas da pílula ----------
    def _zone(self) -> str:
        if self.open_body:
            return ""
        return self.pill.zone_at(self.mapFromGlobal(QCursor.pos()))

    def _zone_page(self, zone: str, *, click: bool) -> str | None:
        """Aba que cada parte da pílula abre. No hover, só anéis, clima e métricas."""
        if zone == "status":
            return None
        if not click and not self.ctx.settings.zone_open:
            return None
        pages = {"rings": "limits", "weather": "weather", "shelf": "shelf", "metrics": "hardware"}
        if click:
            pages |= {
                "date": "agenda",
                "time": "agenda" if self.pill.shell_mode else "home",
                "event": "agenda",
                "title": "home",
                "timer": "home",
                "cam": "audio",
                "mic": "audio",
                "caffeine": "home",
            }
        page = pages.get(zone)
        return page if page and (page == "home" or self.ctx.settings.module_enabled(page)) else None

    def enterEvent(self, _event: object) -> None:
        self.collapse_timer.stop()
        self._media_hold = False
        self._hover_zone = self._zone()
        if self.open_body or self._dragging or self._hover_suspended or not self.ctx.settings.hover_open:
            return
        if self._hover_zone == "status":
            self.hover_timer.stop()
            return
        if time.time() - self._collapsed_at < COLLAPSE_COOLDOWN:
            return  # o mouse ainda estava sobre o painel que acabou de fechar
        self._schedule_hover(self._hover_zone)

    def _schedule_hover(self, zone: str) -> None:
        if zone == "status" or self._hover_suspended:
            self.hover_timer.stop()
            return
        delay = self.ctx.settings.hover_delay_ms
        if zone in ("media", "title") and self.pill.has_media:
            delay = max(delay, MEDIA_HOVER_DELAY)  # dá tempo de usar a roda ou o clique na música
        if delay <= 0:
            self._hover_expand()  # abre na hora
        else:
            self.hover_timer.start(delay)

    def _hover_expand(self) -> None:
        if (
            self.open_body
            or self._dragging
            or self._press is not None
            or self._media_hold
            or self._hover_suspended
        ):
            return
        zone = self._zone()
        if zone == "status":
            return
        if self.geometry().contains(QCursor.pos()):
            self.expand(self._zone_page(zone, click=False))

    def leaveEvent(self, _event: object) -> None:
        self.hover_timer.stop()
        self._media_hold = False
        self._controls_by_key = False
        if self.open_body and not self._dragging:
            self.collapse_timer.start(self.ctx.settings.collapse_delay_ms)

    def wheelEvent(self, event: object) -> None:
        """Roda sobre a pílula: volume (overlay). Com música, Shift ou roda lateral troca."""
        if self.open_body:
            return
        self.hover_timer.stop()
        self._media_hold = True  # mexeu no volume/música: não abre o painel por cima
        delta = event.angleDelta()  # type: ignore[attr-defined]
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)  # type: ignore[attr-defined]
        horizontal = delta.x() if delta.x() else (delta.y() if shift else 0)
        if horizontal and self.pill.has_media:
            (self.ctx.media.next if horizontal < 0 else self.ctx.media.previous)()
        elif delta.y() and not horizontal:
            up, steps = delta.y() > 0, max(1, abs(delta.y()) // 120)
            if self.ctx.audio.running:
                self.ctx.audio.step_master(up, steps)  # muda direto e mostra barra na pílula
            else:
                system.volume_step(up, steps)  # sem Core Audio: teclas de volume
        event.accept()  # type: ignore[attr-defined]

    # ---------- arrastar e soltar (Estante e Atalhos/Lançador) ----------
    def _drop_zones(self, event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> list[str]:
        mime, settings = event.mimeData(), self.ctx.settings
        zones = []
        if settings.module_enabled("shelf") and (mime.hasUrls() or mime.hasImage() or mime.hasText()):
            zones.append("shelf")
        if mime.hasUrls():
            if getattr(settings, "replace_start_menu", False):
                zones.append("launcher")
            elif settings.module_enabled("shortcuts"):
                zones.append("shortcuts")
        return zones

    def _accepts_files(self, event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> bool:
        return bool(self._drop_zones(event))

    def _hint_zone(self, event: QDragMoveEvent | QDropEvent) -> str:
        hint = self.panel.drop_hint
        return hint.zone_at(hint.mapFrom(self, event.position().toPoint()))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        zones = self._drop_zones(event)
        if not zones:
            event.ignore()
            return
        event.acceptProposedAction()
        self._drag_files = True
        self.hover_timer.stop()
        self.collapse_timer.stop()
        target = zones[0] if len(zones) == 1 else self.panel.current
        if not self.expanded:
            self.expand(target if target in zones and target != "launcher" else zones[0])
        self.panel.drop_hint.set_zones(zones)
        self.panel.drop_hint.show()
        self.panel.drop_hint.raise_()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._accepts_files(event):
            event.acceptProposedAction()
            self.panel.drop_hint.set_active(self._hint_zone(event))
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._drag_files = False
        self.panel.drop_hint.hide()
        if self.open_body:
            self.collapse_timer.start(max(800, self.ctx.settings.collapse_delay_ms))

    def dropEvent(self, event: QDropEvent) -> None:
        from pathlib import Path

        from ventude.services import launcher

        self._drag_files = False
        zone = self._hint_zone(event) or self.panel.drop_hint.active
        self.panel.drop_hint.hide()
        zones = self._drop_zones(event)
        if not zones:
            event.ignore()
            return
        zone = zone if zone in zones else zones[0]
        mime = event.mimeData()
        paths = [u.toLocalFile() or u.toString() for u in mime.urls()]
        if zone == "shelf":
            shelf = self.ctx.shelf
            if mime.hasImage() and not any(u.isLocalFile() for u in mime.urls()):
                shelf.add_image(mime.imageData())
            elif paths:
                shelf.add_paths(paths)
            elif mime.hasText():
                shelf.add_text(mime.text())
            self.panel.select("shelf")
            event.acceptProposedAction()
        elif zone == "launcher" and paths:
            from ventude.ui.shell.items import find_matching_pin

            shell = getattr(self.ctx, "shell", None)
            added = 0
            for raw in paths:
                raw = (raw or "").strip()
                if raw.startswith(("http://", "https://")):
                    lbl = launcher.default_label(raw, "url")
                    existing = (
                        shell.find_pin("start", "url", lbl, raw)
                        if shell is not None and hasattr(shell, "find_pin")
                        else find_matching_pin(self.ctx.store.pins("start"), "url", lbl, raw)
                    )
                    if existing is None and self.ctx.store.add_pin("start", "url", lbl, raw):
                        added += 1
                elif raw and Path(raw).exists():
                    kind = launcher.kind_for(raw)
                    p = str(Path(raw))
                    lbl = launcher.default_label(p, kind)
                    existing = (
                        shell.find_pin("start", kind, lbl, p)
                        if shell is not None and hasattr(shell, "find_pin")
                        else find_matching_pin(self.ctx.store.pins("start"), kind, lbl, p)
                    )
                    if existing is None and self.ctx.store.add_pin("start", kind, lbl, p):
                        added += 1
            if added:
                self.ctx.data_changed.emit("pins")
            event.acceptProposedAction()
        else:
            page = self.panel.page("shortcuts")
            if page is not None and paths:
                self.panel.select("shortcuts")
                page.add_paths(paths)  # type: ignore[attr-defined]
                event.acceptProposedAction()
        # Depois de soltar, o Windows nem sempre avisa que o mouse saiu: confere sozinho.
        self.collapse_timer.start(max(1500, self.ctx.settings.collapse_delay_ms))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self.open_body:
            if self.mode == IslandMode.PANEL:
                self.panel.command.close_bar()  # clicou fora: a barra de comandos some
            self._controls_by_key = False
            self.collapse_timer.start(250)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self.mode == IslandMode.PANEL and self.panel.qr.isVisible():
                self.panel.qr.close_card()  # Esc fecha o QR primeiro
            else:
                self.collapse()
            return
        if self.mode == IslandMode.CONTROLS:
            super().keyPressEvent(event)
            return
        if self.panel.qr.isVisible():
            return
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if self.expanded and ctrl and event.key() == Qt.Key.Key_K:
            if not self._search_elsewhere():
                self.panel.command.open_bar()
            return
        typed = event.text()
        plain = not (
            event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        )
        if self.expanded and plain and typed and typed.isprintable() and not typed.isspace():
            if self._focus_in_field():
                super().keyPressEvent(event)
                return
            if not self.panel.command.isVisible() and self._search_elsewhere(typed):
                return
            bar = self.panel.command
            if not bar.isVisible():
                bar.open_bar(typed)  # começou a digitar: vira busca
            else:
                bar.input.setFocus()
                bar.input.insert(typed)
            return
        if ctrl and Qt.Key.Key_1 <= event.key() <= Qt.Key.Key_9:
            self.panel.select_index(event.key() - Qt.Key.Key_1)
            return
        if ctrl and event.key() in (Qt.Key.Key_0, Qt.Key.Key_Comma):
            self.panel.select("settings")  # Ctrl + 0 ou Ctrl + , : Ajustes
            return
        super().keyPressEvent(event)

    def _focus_in_field(self) -> bool:
        """Digitando num campo de uma aba (ex.: nova tarefa): o texto fica lá."""
        focus = QApplication.focusWidget()
        return (
            isinstance(focus, QLineEdit | QPlainTextEdit)
            and focus is not self.panel.command.input
            and self.isAncestorOf(focus)
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.MouseButton.RightButton
            and self.mode == IslandMode.PILL
            and self.pill.shell_mode
        ):
            self.hover_timer.stop()
            self.open_controls(by_key=False)
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton and not self.open_body and self.pill.has_media:
            self.hover_timer.stop()
            self._media_hold = True
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.open_body and event.position().y() > HEADER_HEIGHT:
            return
        self._press = event.globalPosition().toPoint()
        self._origin = self.pos()
        self.hover_timer.stop()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press is None:
            if not self.open_body and not self._media_hold:
                zone = self.pill.zone_at(event.position().toPoint())
                if zone != self._hover_zone:
                    self._hover_zone = zone
                    self.hover_timer.stop()
                    if zone != "status" and not self._hover_suspended and self.ctx.settings.hover_open:
                        if time.time() - self._collapsed_at >= COLLAPSE_COOLDOWN:
                            self._schedule_hover(zone)
            return
        delta = event.globalPosition().toPoint() - self._press
        if not self._dragging and delta.manhattanLength() >= QApplication.startDragDistance():
            self._dragging = True
            self.anim.stop()
            self.collapse_timer.stop()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._dragging:
            self.move(self._origin + delta)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and not self.open_body and self.pill.has_media:
            self.ctx.media.play_pause()  # clique do meio: tocar/pausar sem abrir
            return
        if self._press is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self._press = None
        if self._dragging:
            self._dragging = False
            self.unsetCursor()
            self._save_position()
            self.reposition()
        elif not self.open_body and self._toast is not None:
            kind = self._toast.kind
            pages = {"limits": "limits", "agenda": "agenda", "volume": "audio", "hardware": "hardware"}
            page = pages.get(kind, "home")
            self.expand(page if page == "home" or self.ctx.settings.module_enabled(page) else "home")
        elif not self.open_body:
            zone = self.pill.zone_at(event.position().toPoint())
            if zone == "status":
                self.open_controls(by_key=False)
                return
            if zone == "media":  # capa ou equalizador: tocar/pausar
                self._media_hold = True
                self.ctx.media.play_pause()
                return
            if zone == "mic" and self.ctx.audio.running:  # ponto laranja: muta/desmuta o microfone
                self._media_hold = True
                self.ctx.audio.toggle_mic_mute()
                return
            self.expand(self._zone_page(zone, click=True))

    def _save_position(self) -> None:
        anchor = QPoint(self.x() + self.width() // 2, self.y())
        screen = QApplication.screenAt(anchor) or screen_for(self.ctx.settings.monitor_index)
        rect = anchored_rect(screen.availableGeometry(), self.width(), self.height(), anchor)
        anchor = QPoint(rect.x() + rect.width() // 2, rect.y())
        screens = QGuiApplication.screens()
        self.ctx.update_settings(
            island_position_json=json.dumps({"x": anchor.x(), "y": anchor.y()}),
            monitor_index=screens.index(screen) if screen in screens else 0,
        )

    def _paint_surface(self, p: QPainter, rect: QRectF, radius: float) -> None:
        path = brand.paint_ink(p, rect, radius, bg=theme.INK, alpha=1.0)
        if self.open_body:  # aberta, a ilha ganha o brilho da marca no alto (cresce junto com ela)
            brand.paint_aurora(p, path, rect, min(1.0, max(0.0, (rect.height() - 60) / 260)))

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        rect = QRectF(self.rect())
        radius = min(theme.ISLAND_RADIUS, rect.height() / 2)
        if self.anim.state() == QPropertyAnimation.State.Running:
            self._paint_surface(p, rect, radius)  # mudando de tamanho a cada quadro: sem cache
        else:
            # Parada, a superfície (fundo, brilho e fio de luz) vira um pixmap: os filhos que se
            # repintam (equalizador, relógio, barras) não refazem gradientes a cada quadro.
            ratio = self.devicePixelRatioF()
            key = (self.width(), self.height(), self.open_body, ratio)
            if self._surface is None or self._surface[0] != key:
                pm = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
                pm.setDevicePixelRatio(ratio)
                pm.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pm)
                self._paint_surface(painter, rect, radius)
                painter.end()
                self._surface = (key, pm)
            p.drawPixmap(0, 0, self._surface[1])
        path = brand.rounded_path(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius)
        if self.pulse.state() == QVariantAnimation.State.Running:
            glow = abs(math.sin(float(self.pulse.currentValue()) * math.pi * 3))
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(theme.color(theme.ACCENT, 0.85 * glow), 2.2))
            p.drawPath(path)

    def prepare_quit(self) -> None:
        self._quitting = True
        for timer in (
            self.clock_timer,
            self.fullscreen_timer,
            self.hover_timer,
            self.collapse_timer,
            self.toast_timer,
        ):
            timer.stop()
        for anim in (self.anim, self.fade, self.pulse):
            anim.stop()
        if self._hotkey:
            try:
                system.unregister_hotkey(int(self.winId()))
            except Exception:  # noqa: BLE001
                pass
            self._hotkey = False
        self.hide()

    def closeEvent(self, event: QEvent) -> None:
        if self._quitting or not event.spontaneous():
            event.accept()
            return
        self.hide()
        event.ignore()

    # ---------- reações ----------
    def _tick(self) -> None:
        if self.mode == IslandMode.PANEL:
            self.panel.refresh_clock()
        elif self.mode == IslandMode.CONTROLS:
            self.controls.refresh_clock()
        else:
            had_event = not self.pill.event_chip.isHidden()
            zero_pulse = self.pill.refresh_clock()
            if zero_pulse:
                self._pulse_once()
            if had_event and self.pill.event_chip.isHidden():
                self._resize_compact()
            if self.ctx.timer.state is not None:
                self.pill.set_timer(self.ctx.timer.state)

    def _pulse_once(self) -> None:
        if motion.scale(self.ctx.settings) <= 0:
            return
        self.pulse.stop()
        self.pulse.setDuration(600)
        self.pulse.start()

    def _resize_compact(self) -> None:
        if self.mode == IslandMode.PILL and not self._dragging:
            target = self.target_rect(IslandMode.PILL)
            if target != self.geometry():
                self._animate(target, opening=False)

    def _media_changed(self, state: MediaState | None) -> None:
        self.pill.set_media(state)
        self.pill.update()
        self._resize_compact()
        track = (state.title, state.artist) if state else None
        if track and track != self._last_track and self._last_track is not None and state.is_playing:
            if self.ctx.settings.toast_music:
                art = self.ctx.media.thumbnail
                self.show_toast(
                    Toast(state.title, state.artist, icon="music", art=art, seconds=3, kind="music")
                )
        if track:
            self._last_track = track

    # ---------- avisos na ilha ----------
    def _allows_in_quiet(self, toast: Toast) -> bool:
        if not getattr(self.ctx.settings, "quiet_mode", False):
            return True
        if toast.kind in ("timer", "agenda", "hardware", "error", "alert"):
            return True
        return toast.kind == "limits" and bool(toast.extra.get("critical"))

    def show_toast(self, toast: Toast) -> None:
        """A ilha cresce um pouco com o aviso e volta a ser pílula sozinha."""
        center = getattr(self.ctx, "notifications", None)
        if center is not None:
            center.add(toast.title, toast.text, kind=toast.kind, icon=toast.icon, color=toast.color)
        if not self._allows_in_quiet(toast):
            return
        blocked_by_ui = self.open_body or self._hover_suspended
        if (
            self.panel.shell_mode
            and blocked_by_ui
            and self.ctx.settings.island_toasts
            and self.isVisible()
            and not self._auto_hidden
            and not self._dragging
            and toast.kind != "timer"
        ):
            if toast.kind not in ("music", "volume"):
                if not any(t.kind == toast.kind and t.title == toast.title for t in self._deferred_toasts):
                    self._deferred_toasts.append(toast)
                    self._deferred_toasts = self._deferred_toasts[-3:]
            return
        can_show = (
            self.ctx.settings.island_toasts
            and self.isVisible()
            and not self._auto_hidden
            and not self.open_body
            and not self._hover_suspended
            and not self._dragging
        )
        if not can_show:
            if toast.kind in ("limits", "agenda", "timer", "error"):
                self.ctx.tray_message.emit(toast.title, toast.text)
            return
        if toast.kind in ("music", "volume"):  # só a música mais recente importa
            self._toasts = [t for t in self._toasts if t.kind != toast.kind]
            if self._toast is not None and self._toast.kind == toast.kind:
                self._toast = toast
                self.toast_view.show_toast(toast)
                self.toast_timer.start(int(toast.seconds * 1000))
                return
        self._toasts.append(toast)
        if self._toast is None:
            self._next_toast()

    def _drain_deferred_toasts(self) -> None:
        if self.mode == IslandMode.PILL and not self._hover_suspended and self._deferred_toasts:
            queued = list(self._deferred_toasts)
            self._deferred_toasts.clear()
            for toast in queued:
                self.show_toast(toast)

    def _next_toast(self) -> None:
        if self._toast is not None or not self._toasts or self.open_body or self._hover_suspended:
            return
        self._toast = self._toasts.pop(0)
        self.mode = IslandMode.TOAST
        self.volume_overlay.dismiss_now()
        self.toast_view.show_toast(self._toast)
        self.toast_timer.start(int(self._toast.seconds * 1000))
        self.pill.hide()
        self._animate(self.target_rect(IslandMode.TOAST), opening=True)
        if not self.toast_view.isVisible():
            self._fade_in(self.toast_view, motion.ms("desdobrar_fade", self.ctx.settings))

    def _end_toast(self) -> None:
        if self._toast is None:
            return
        self._toast = None
        self.toast_view.hide()
        if not self.open_body:
            self.mode = IslandMode.PILL
            self._animate(self.target_rect(IslandMode.PILL), opening=False)
        if self._toasts:
            QTimer.singleShot(350, self._next_toast)

    def _cancel_toasts(self) -> None:
        self.toast_timer.stop()
        self._toasts.clear()
        had_toast = self._toast is not None
        self._toast = None
        self.toast_view.hide()
        if had_toast and not self.open_body:
            self.mode = IslandMode.PILL
            self._resize_compact()

    def _toast_art(self, data: bytes) -> None:
        if self._toast is not None and self._toast.kind == "music" and data:
            self._toast.art = data
            self.toast_view.show_toast(self._toast)

    # ---------- conta-gotas e OCR ----------
    def run_tool(self, mode: str) -> None:
        """Fecha o painel, tira a ilha da frente e congela a tela para escolher."""
        if self._capture is not None:
            return
        self.collapse()
        delay = motion.ms("morfose_close", self.ctx.settings) + 60
        QTimer.singleShot(delay, lambda: self._start_capture(mode))

    def _start_capture(self, mode: str) -> None:
        self.hide()
        QApplication.processEvents()

        def go() -> None:
            session = CaptureSession(mode)
            session.finished.connect(self._capture_done)
            self._capture = session
            if not session.start():
                self._capture_done(mode, None)

        QTimer.singleShot(90, go)  # um quadro para a ilha sumir da captura

    def _capture_done(self, mode: str, value: object) -> None:
        self._capture = None
        self.show()
        if value is None:
            return
        if mode == "color":
            hexa = color_hex(value)  # type: ignore[arg-type]
            QGuiApplication.clipboard().setText(hexa)
            self.show_toast(
                Toast(f"Cor {hexa} copiada", "Cole onde quiser", icon="droplet", color=hexa, kind="tool")
            )
            return
        self.show_toast(Toast("Lendo o texto…", "OCR do Windows", icon="scan", seconds=8, kind="tool"))
        data = png_bytes(prepare_for_ocr(value))  # type: ignore[arg-type]
        from ventude.services import ocr

        self.ctx.run(ocr.recognize, data, done=self._ocr_done, fail=self._ocr_failed, owner=self)

    def _ocr_done(self, text: str) -> None:
        self._end_toast()
        if not text:
            self.show_toast(
                Toast("Nenhum texto encontrado", "Tente uma região maior", icon="scan", kind="tool")
            )
            return
        QGuiApplication.clipboard().setText(text)
        preview = " ".join(text.split())[:80]
        self.show_toast(
            Toast(f"Texto copiado ({len(text)} caracteres)", preview, icon="scan", seconds=5, kind="tool")
        )

    def _ocr_failed(self, error: BaseException) -> None:
        self._end_toast()
        self.show_toast(
            Toast(
                "Não deu para ler o texto", str(error), icon="alert", color=theme.WARN, seconds=6, kind="tool"
            )
        )

    def _shelf_changed(self) -> None:
        self.pill.set_shelf(len(self.ctx.shelf.items))
        self._resize_compact()

    # ---------- timer / Pomodoro ----------
    def _timer_changed(self, state: TimerState | None) -> None:
        before = self.pill.timer_chip.isHidden()
        self.pill.set_timer(state)
        if before != self.pill.timer_chip.isHidden():
            self._resize_compact()

    def _timer_finished(self, state: TimerState) -> None:
        title, text = finished_text(state, self.ctx.timer.pomodoro)
        self.show_toast(Toast(title, text, icon="clock", color=theme.ACCENT, seconds=6, kind="timer"))
        if motion.scale(self.ctx.settings) > 0:
            self.pulse.stop()
            self.pulse.setDuration(1800)
            self.pulse.start()  # a ilha pulsa ao terminar
        QApplication.beep()

    def _limit_alert(self, provider: str, title: str, message: str) -> None:
        color = theme.provider_color(provider)
        critical = "95%" in title or "100%" in title
        self.show_toast(
            Toast(
                title,
                message,
                icon="gauge",
                color=color,
                seconds=5,
                kind="limits",
                extra={"critical": critical},
            )
        )

    def _reminder(self, event: object, minutes: int) -> None:
        if minutes >= 10:
            changed = self.pill.set_upcoming_event(event)
            if changed:
                self._resize_compact()
            if self.ctx.settings.agenda_reminders:
                self.show_toast(event_toast(event, minutes))  # type: ignore[arg-type]
        elif minutes <= 5:
            self.pill.set_upcoming_event(event)
            self._pulse_once()

    def _rings_changed(self) -> None:
        before = self.pill.visible_rings()
        self.pill.refresh_rings()
        if before != self.pill.visible_rings() or self.width() != self.pill.preferred_width():
            self._resize_compact()

    def _settings_changed(self, settings: Settings) -> None:
        self.panel.rebuild_tabs()
        self.pill.refresh_rings()
        self._weather_done(self.ctx.forecast)
        self._tick()
        self._update_hotkey()
        self.pill.set_media(self.pill.media)  # data na ilha (ajuste)
        self._metrics_changed()
        self._privacy_changed()
        if not self._dragging and self.anim.state() != QPropertyAnimation.State.Running:
            target = self.target_rect(self.mode)
            if target.topLeft() != self.geometry().topLeft() or not self.open_body:
                self._animate(target, opening=False)

    def _keep_on_top(self) -> None:
        app = QApplication.instance()
        if app is not None and app.platformName().lower() == "offscreen":
            return
        if QApplication.activePopupWidget() is not None:
            return  # um menu aberto (ex.: botão direito num app em segundo plano) fica por cima da ilha
        winutil.keep_topmost(int(self.winId()))

    def _check_fullscreen(self) -> None:
        covered = False if self.open_body else system.fullscreen_app_over(int(self.winId()))
        if covered and self.ctx.settings.hide_fullscreen:
            if self.isVisible():
                self._auto_hidden = True
                self.hide()
            return
        if self._auto_hidden:
            self._auto_hidden = False
            self.show()
        if self.isVisible() and not self._user_hidden and not covered:
            self._keep_on_top()  # nunca se reafirma por cima de um jogo em tela cheia

    # ---------- atalho global (Windows) ----------
    def _update_hotkey(self) -> None:
        want = self.ctx.settings.hotkey_enabled
        if want and not self._hotkey:
            self._hotkey = system.register_hotkey(int(self.winId()))
        elif not want and self._hotkey:
            system.unregister_hotkey(int(self.winId()))
            self._hotkey = False

    def showEvent(self, event: object) -> None:
        super().showEvent(event)
        self._update_hotkey()

    def nativeEvent(self, event_type: object, message: object) -> tuple[bool, int]:  # noqa: N802
        if self._hotkey and system.is_hotkey_message(message):
            if (
                (self.ctx.settings.replace_start_menu or self.panel.shell_mode)
                and self.ctx.settings.hotkey_command
                and self.ctx.open_search()
            ):
                return True, 0  # no Shell, o atalho abre o Lançador
            opening = not self.expanded
            self.toggle()
            if opening and self.expanded and self.ctx.settings.hotkey_command:
                self.panel.command.open_bar()
            return True, 0
        return False, 0

    def user_hidden(self, hidden: bool) -> None:
        """Ocultar/mostrar pela bandeja (sem confundir com a tela cheia)."""
        self._auto_hidden = False
        self._user_hidden = hidden
        self.setVisible(not hidden)

    def black_hole(self) -> None:
        """Tecla Windows 3 vezes: a ilha é sugada para o meio e some; de novo, volta do ponto."""
        from ventude.ui.blackhole import BlackHole

        if getattr(self, "_hole", None) is not None:
            return  # uma animação por vez
        hiding = self.isVisible() and not self._user_hidden
        if hiding:
            if self.mode != IslandMode.PILL or self._toast is not None:
                self._cancel_toasts()
                self.collapse()
                self.setGeometry(self.target_rect(IslandMode.PILL))
            self._hole_pixmap = self.grab()
            rect = self.geometry()
            self.user_hidden(True)
        else:
            rect = self.target_rect(IslandMode.PILL)
            if self._hole_pixmap is None or self._hole_pixmap.isNull():
                self.setGeometry(rect)
                self._hole_pixmap = self.grab()
        hole = BlackHole(self._hole_pixmap, rect, inward=hiding, settings=self.ctx.settings)
        self._hole = hole

        def done() -> None:
            self._hole = None
            if not hiding:
                self.user_hidden(False)
                self.reposition()
                self.raise_()
            shell = getattr(self.ctx, "shell", None)
            if shell is not None and hasattr(shell, "sync_echos"):
                shell.sync_echos()  # type: ignore[attr-defined]

        hole.play(done)
