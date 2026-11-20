"""A ilha: uma pílula discreta no topo da tela que se abre num painel."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import (
    QEasingCurve,
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
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QGuiApplication,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QRegion,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ventude.core.config import MODULES, Settings
from ventude.core.timefmt import WEEKDAYS_SHORT, clock
from ventude.services import system
from ventude.services.media import MediaState
from ventude.services.weather import Forecast
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.pages.agenda import AgendaPage
from ventude.ui.pages.base import Page
from ventude.ui.pages.home import HomePage, fill_ring
from ventude.ui.pages.limits import LimitsPage
from ventude.ui.pages.market import MarketPage
from ventude.ui.pages.news import NewsPage
from ventude.ui.pages.settings import SettingsPage
from ventude.ui.pages.shortcuts import ShortcutsPage
from ventude.ui.pages.tasks import TasksPage
from ventude.ui.pages.weather import WeatherPage
from ventude.ui.toast import TOAST_HEIGHT, TOAST_WIDTH, Toast, ToastView, event_toast
from ventude.ui.widgets import ElidedLabel, Equalizer, IconButton, Ring, label, rounded_pixmap

log = logging.getLogger(__name__)

PAGES: dict[str, Callable[[AppContext], Page]] = {
    "home": HomePage,
    "limits": LimitsPage,
    "shortcuts": ShortcutsPage,
    "agenda": AgendaPage,
    "tasks": TasksPage,
    "weather": WeatherPage,
    "market": MarketPage,
    "news": NewsPage,
    "settings": SettingsPage,
}
COMPACT_HEIGHT = 38
EXPANDED = QSize(860, 568)
HEADER_HEIGHT = 54
TOP_MARGIN = 6
BACK_HOME_AFTER = 5 * 60
COLLAPSE_COOLDOWN = 0.6
MEDIA_HOVER_DELAY = 650  # ms: sobre a música, espera um pouco para dar tempo da roda/clique


def anchored_rect(area: QRect, width: int, height: int, anchor: QPoint) -> QRect:
    """Mantém a âncora (centro do topo) visível, inclusive em monitores negativos."""
    width, height = min(width, area.width() - 16), min(height, area.height() - 16)
    x = max(area.left() + 8, min(anchor.x() - width // 2, area.right() - width - 7))
    y = max(area.top() + 2, min(anchor.y(), area.bottom() - height - 7))
    return QRect(x, y, width, height)


def screen_for(index: int):  # noqa: ANN201
    screens = QGuiApplication.screens()
    return screens[index] if 0 <= index < len(screens) else QGuiApplication.primaryScreen()


class Pill(QWidget):
    """Conteúdo da ilha fechada: data, hora, música e anéis das IAs."""

    def __init__(self, ctx: AppContext, parent: QWidget) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)  # a ilha acompanha em qual parte da pílula o mouse está
        self.art = QLabel()
        self.art.setFixedSize(22, 22)
        self.date = label("", "")
        self.date.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 12px; font-weight: 600;")
        self.title = ElidedLabel("")
        self.title.setStyleSheet("font-size: 12px; font-weight: 600;")
        self.eq = Equalizer(theme.ACCENT)
        self.time = label("", "")
        self.time.setStyleSheet("font-size: 13px; font-weight: 600;")
        self.rings: dict[str, Ring] = {p: Ring(16, 2.6) for p in ctx.limits.providers()}
        row = QHBoxLayout(self)
        row.setContentsMargins(15, 0, 13, 0)
        row.setSpacing(10)
        self.weather = QWidget()
        self.weather_icon = QLabel()
        self.weather_icon.setFixedSize(16, 16)
        self.weather_temp = label("", "")
        self.weather_temp.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 12px; font-weight: 600;")
        self.weather_rain = label("", "")
        self.weather_rain.setStyleSheet(f"color: {theme.SKY}; font-size: 11px; font-weight: 700;")
        self.weather_rain.hide()
        weather_row = QHBoxLayout(self.weather)
        weather_row.setContentsMargins(0, 0, 0, 0)
        weather_row.setSpacing(4)
        weather_row.addWidget(self.weather_icon)
        weather_row.addWidget(self.weather_temp)
        weather_row.addWidget(self.weather_rain)
        self.weather.hide()
        row.addWidget(self.art)
        row.addWidget(self.date)
        row.addStretch(1)
        first_gap = row.count() - 1
        row.addWidget(self.title, 1)
        row.addWidget(self.eq)
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
        for widget in (self.art, self.date, self.title, self.eq, self.time, self.weather):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.media: MediaState | None = None
        self.set_media(None)

    @property
    def playing(self) -> bool:
        return self.media is not None and self.media.is_playing

    @property
    def has_media(self) -> bool:
        """Tem música (tocando ou pausada): a ilha mostra capa e título."""
        return self.media is not None

    def refresh_clock(self) -> None:
        now = datetime.now()
        self.time.setText(clock(now, self.ctx.settings.clock_24h))
        self.date.setText(f"{WEEKDAYS_SHORT[now.weekday()]} {now.day}")
        if self.playing:
            self.update()  # linha de andamento da música

    def set_media(self, state: MediaState | None) -> None:
        self.media = state
        shown, playing = self.has_media, self.playing
        self.art.setVisible(shown)
        self.title.setVisible(shown)
        self.eq.setVisible(shown)
        self.eq.set_playing(playing)
        self.eq.set_dim(not playing)
        self.date.setVisible(not shown)
        for index in self._gaps:  # com música, o título ocupa o espaço livre
            self.row.setStretch(index, 0 if shown else 1)
        if state:
            color = theme.TEXT if playing else theme.TEXT_2
            self.title.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {color};")
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

    def set_weather(self, forecast: Forecast | None) -> None:
        """Clima de agora ao lado da hora (some sem cidade ou com o ajuste desligado)."""
        if forecast is None or not self.ctx.settings.pill_weather:
            self.weather.hide()
            return
        rain = forecast.rain_soon()
        glyph = "rain" if rain else forecast.icon
        color = theme.SUN if glyph in ("sun", "cloud-sun") else theme.SKY
        self.weather_icon.setPixmap(icons.pixmap(glyph, color, 16))
        self.weather_temp.setText(f"{forecast.temp:.0f}°")
        self.weather_rain.setText(f"{rain}%" if rain else "")
        self.weather_rain.setVisible(bool(rain))
        tip = f"{forecast.place}: {forecast.temp:.0f}°, {forecast.description}"
        if rain:
            tip += f"\nChuva provável na próxima hora ({rain}%)"
        self.weather.setToolTip(tip)
        self.weather.show()

    @staticmethod
    def _text_width(widget: QLabel, text: str | None = None) -> int:
        widget.ensurePolished()
        return widget.fontMetrics().horizontalAdvance(widget.text() if text is None else text) + 2

    def preferred_width(self) -> int:
        """Largura pelo texto de verdade (data, clima, hora, música e anéis), com limites."""
        spacing = self.row.spacing()
        parts: list[int] = []
        if self.has_media:
            title = self.title.full_text()
            parts += [22, max(80, min(self._text_width(self.title, title), 250)), 14]
        else:
            parts.append(self._text_width(self.date))
        if not self.weather.isHidden():
            width = 16 + 4 + self._text_width(self.weather_temp)
            if not self.weather_rain.isHidden():
                width += 4 + self._text_width(self.weather_rain)
            parts.append(width)
        parts.append(self._text_width(self.time, self.time.text() or "00:00"))
        rings = self.visible_rings()
        if rings:
            parts.append(4 + rings * 16 + (rings - 1) * 6)
        margins = self.row.contentsMargins()
        breathing = 10 if self.has_media else 34  # sem música, a hora fica mais ao centro
        total = margins.left() + margins.right() + sum(parts) + spacing * (len(parts) - 1) + breathing
        return max(150, min(total, 560))

    def zone_at(self, pos: QPoint) -> str:
        """Qual parte da pílula está sob o ponto: rings, weather, media, date, time ou ''."""

        def hit(widget: QWidget, pad: int = 4) -> bool:
            return widget.isVisibleTo(self) and widget.geometry().adjusted(-pad, -20, pad, 20).contains(pos)

        if hit(self.ring_box):
            return "rings"
        if hit(self.weather):
            return "weather"
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
            pen.setColor(theme.color(theme.ACCENT if state.is_playing else theme.TEXT_2, 0.9))
            p.setPen(pen)
            p.drawLine(QPointF(left, y), QPointF(left + (right - left) * done, y))


class DropHint(QWidget):
    """Moldura que aparece enquanto um arquivo é arrastado sobre a ilha."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(theme.color(theme.BG, 0.72))
        pen = QPen(theme.color(theme.ACCENT), 1.6, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(rect, theme.ISLAND_RADIUS - 6, theme.ISLAND_RADIUS - 6)
        icon = icons.pixmap("plus", theme.ACCENT, 34)
        center = rect.center()
        p.drawPixmap(int(center.x() - 17), int(center.y() - 46), icon)
        p.setPen(theme.color(theme.TEXT))
        p.setFont(theme.font(16, theme.SEMI))
        p.drawText(
            QRectF(rect.left(), center.y() - 4, rect.width(), 24),
            Qt.AlignmentFlag.AlignCenter,
            "Solte para adicionar aos atalhos",
        )
        p.setPen(theme.color(theme.TEXT_2))
        p.setFont(theme.font(12))
        p.drawText(
            QRectF(rect.left(), center.y() + 22, rect.width(), 20),
            Qt.AlignmentFlag.AlignCenter,
            "Apps, pastas, arquivos e links",
        )


class Panel(QWidget):
    """Conteúdo da ilha aberta: abas + páginas criadas sob demanda."""

    def __init__(self, ctx: AppContext, parent: QWidget) -> None:
        super().__init__(parent)
        self.ctx = ctx
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 12, 16)
        root.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(4)
        self.tab_row = QHBoxLayout()
        self.tab_row.setSpacing(2)
        header.addLayout(self.tab_row)
        header.addStretch(1)
        self.clock = label("", "Muted")
        self.clock.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header.addWidget(self.clock)
        header.addSpacing(6)
        self.pin = IconButton("pin", "Manter aberto", 30, 15)
        self.pin.setCheckable(True)
        self.pin.toggled.connect(self._pin_changed)
        self.close_btn = IconButton("chevron-up", "Fechar (Esc)", 30, 16)
        header.addWidget(self.pin)
        header.addWidget(self.close_btn)
        root.addLayout(header)
        self.stack = QStackedWidget()
        body = QVBoxLayout()
        body.setContentsMargins(4, 0, 4, 0)
        body.addWidget(self.stack)
        root.addLayout(body, 1)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.tabs: dict[str, QPushButton] = {}
        self.pages: dict[str, Page] = {}
        self.current = "home"
        self._transition: tuple[QParallelAnimationGroup, QWidget] | None = None
        self.drop_hint = DropHint(self)
        self.rebuild_tabs()

    def _pin_changed(self, pinned: bool) -> None:
        self.pin.set_icon("pin", theme.ACCENT if pinned else None)
        self.pin.setToolTip("Fixado: não fecha sozinho" if pinned else "Manter aberto")

    def enabled_keys(self) -> list[str]:
        s = self.ctx.settings
        return [key for key, _, _ in MODULES if s.module_enabled(key)]

    def rebuild_tabs(self) -> None:
        keys = self.enabled_keys()
        if list(self.tabs) == keys:
            return
        for button in self.tabs.values():
            self.group.removeButton(button)
            button.deleteLater()
        self.tabs.clear()
        for key, title, icon_name in MODULES:
            if key not in keys:
                continue
            button = QPushButton()
            button.setObjectName("Tab")
            button.setCheckable(True)
            button.setFixedHeight(32)
            button.setMinimumWidth(36)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(title)
            button.setProperty("title", title)
            button.setProperty("icon_name", icon_name)
            button.clicked.connect(lambda _c=False, k=key: self.select(k))
            self.group.addButton(button)
            self.tab_row.addWidget(button)
            self.tabs[key] = button
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
        if key not in self.tabs:
            key = "home"
        page = self.page(key)
        if page is None:
            return
        keys = list(self.tabs)
        previous = self.current
        changed = self.stack.currentWidget() is not page
        self.current = key
        self.stack.setCurrentWidget(page)
        # Sem animação de aba enquanto o painel ainda está aparecendo (fade da abertura).
        if changed and previous in keys and self.isVisible() and self.graphicsEffect() is None:
            self._slide_in(page, 1 if keys.index(key) >= keys.index(previous) else -1)
        for tab_key, button in self.tabs.items():
            active = tab_key == key
            button.setChecked(active)
            button.setText(f" {button.property('title')}" if active else "")
            button.setIcon(
                icons.icon(button.property("icon_name"), theme.TEXT if active else theme.TEXT_2, 17)
            )
        try:
            page.on_show()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao atualizar a aba %s", key)

    # ---------- animação entre abas ----------
    def _slide_in(self, page: QWidget, direction: int) -> None:
        """A aba nova entra deslizando do lado da aba clicada, com um fade curto."""
        self._end_transition()
        duration = int(self.ctx.settings.animation_ms * 0.85)
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
        slide.setStartValue(end + QPoint(28 * direction, 0))
        slide.setEndValue(end)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade = QPropertyAnimation(effect, b"opacity", group)
        fade.setDuration(int(duration * 0.8))
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutQuad)
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

    def select_index(self, index: int) -> None:
        keys = list(self.tabs)
        if 0 <= index < len(keys):
            self.select(keys[index])

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
        self.setWindowTitle("Ventude")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)  # arrastar apps, pastas e links para os Atalhos
        self.expanded = False
        self._drag_files = False
        self._media_hold = False  # usou a roda/clique na música: não abre por hover
        self._hover_zone = ""
        self._holds = 0
        self._press: QPoint | None = None
        self._origin = QPoint()
        self._dragging = False
        self._auto_hidden = False
        self._collapsed_at = time.time()
        self._hotkey = False
        self.pill = Pill(ctx, self)
        self.panel = Panel(ctx, self)
        self.panel.hide()
        self.toast_view = ToastView(self)
        self._toast: Toast | None = None
        self._toasts: list[Toast] = []
        self._last_track: tuple[str, str] | None = None
        self.toast_timer = QTimer(self, singleShot=True)
        self.toast_timer.timeout.connect(self._end_toast)
        self.panel.close_btn.clicked.connect(self.collapse)
        self.anim = QPropertyAnimation(self, b"geometry", self)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(self._settled)
        self.fade = QPropertyAnimation(self)
        self.hover_timer = QTimer(self, singleShot=True, interval=0)
        self.hover_timer.timeout.connect(self._hover_expand)
        self.collapse_timer = QTimer(self, singleShot=True)
        self.collapse_timer.timeout.connect(self._try_collapse)
        self.clock_timer = QTimer(self, interval=1000)
        self.clock_timer.timeout.connect(self._tick)
        self.clock_timer.start()
        self.fullscreen_timer = QTimer(self, interval=1500)
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
        self.setGeometry(self.target_rect(False))
        ctx.weather_changed.connect(self._weather_done)
        self._weather_done(ctx.forecast)
        ctx.limits.alert.connect(self._limit_alert)
        ctx.reminders.due.connect(self._reminder)
        ctx.media.thumbnail_changed.connect(self._toast_art)

    # ---------- clima na ilha ----------
    def _weather_done(self, forecast: Forecast | None) -> None:
        before = self.pill.weather.isHidden()
        self.pill.set_weather(forecast)
        if before != self.pill.weather.isHidden():
            self._resize_compact()

    # ---------- geometria ----------
    def _sizes(self, expanded: bool) -> tuple[int, int]:
        if expanded:
            return EXPANDED.width(), EXPANDED.height()
        if self._toast is not None:
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

    def target_rect(self, expanded: bool) -> QRect:
        area, anchor = self._anchor()
        return anchored_rect(area, *self._sizes(expanded), anchor)

    def reposition(self) -> None:
        self.anim.stop()
        self.setGeometry(self.target_rect(self.expanded))
        self._settled()

    def reset_position(self) -> None:
        self.ctx.update_settings(island_position_json="")
        self.reposition()

    def _animate(self, target: QRect) -> None:
        self.anim.stop()
        if self.geometry() == target:
            self._settled()
            return
        self.anim.setDuration(self.ctx.settings.animation_ms)
        self.anim.setStartValue(self.geometry())
        self.anim.setEndValue(target)
        self.anim.start()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        self.pill.setGeometry(self.rect())
        self.toast_view.setGeometry(self.rect())
        self._layout_panel()

    def moveEvent(self, event: object) -> None:
        super().moveEvent(event)
        if self._revealing():
            self._layout_panel()

    def _revealing(self) -> bool:
        return self.expanded and self.anim.state() == QPropertyAnimation.State.Running

    def _layout_panel(self) -> None:
        """Abrindo: o painel já fica no tamanho final e a ilha crescendo o revela."""
        if not self._revealing():
            self.panel.clearMask()
            self.panel.setGeometry(self.rect())
            return
        target = self.anim.endValue()
        offset = target.topLeft() - self.pos()
        self.panel.setGeometry(QRect(offset, target.size()))
        radius = min(theme.ISLAND_RADIUS, self.height() / 2)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), radius, radius)
        region = QRegion(path.toFillPolygon().toPolygon())
        region.translate(-offset)
        self.panel.setMask(region)

    # ---------- abrir e fechar ----------
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
        self.expanded = True
        self.pill.hide()
        self.panel.refresh_clock()
        self._animate(self.target_rect(True))
        if self._revealing():  # o conteúdo aparece junto com a animação, sem esperar ela acabar
            self._layout_panel()
            self._fade_in(self.panel, max(120, int(self.ctx.settings.animation_ms * 0.9)))

    def collapse(self) -> None:
        if not self.expanded:
            return
        self.expanded = False
        self._collapsed_at = time.time()
        self.panel.pin.setChecked(False)
        self.panel.hide()
        self.pill.hide()
        focus = QApplication.focusWidget()
        if focus and self.isAncestorOf(focus):
            focus.clearFocus()
        self._animate(self.target_rect(False))

    def toggle(self) -> None:
        if not self.isVisible():
            self.show()
        if self.expanded:
            self.collapse()
        else:
            self.expand()
            self.raise_()
            self.activateWindow()

    def _settled(self) -> None:
        if self.anim.state() == QPropertyAnimation.State.Running:
            return
        self._layout_panel()
        if self.expanded:
            if not self.panel.isVisible():
                self._fade_in(self.panel)
        elif self._toast is not None:
            self.pill.hide()
            if not self.toast_view.isVisible():
                self._fade_in(self.toast_view, 160)
        else:
            self.toast_view.hide()
            self.pill.show()
        self.update()

    def _fade_in(self, widget: QWidget, duration: int = 140) -> None:
        self.fade.stop()
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        widget.show()
        self.fade = QPropertyAnimation(effect, b"opacity", self)
        self.fade.setDuration(duration)
        self.fade.setStartValue(0.0)
        self.fade.setEndValue(1.0)
        self.fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.fade.finished.connect(lambda: widget.setGraphicsEffect(None))
        self.fade.start()

    def _keep_open(self) -> bool:
        if self.panel.pin.isChecked() or self._holds > 0 or self._dragging or self._drag_files:
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
        if not self.expanded:
            return
        if self._keep_open():
            self.collapse_timer.start(max(400, self.ctx.settings.collapse_delay_ms))
            return
        self.collapse()

    def _hold(self, on: bool) -> None:
        self._holds = max(0, self._holds + (1 if on else -1))
        if on:
            self.collapse_timer.stop()
        elif self.expanded:
            self.collapse_timer.start(max(600, self.ctx.settings.collapse_delay_ms))

    # ---------- eventos ----------
    # ---------- zonas da pílula ----------
    def _zone(self) -> str:
        if self.expanded:
            return ""
        return self.pill.zone_at(self.mapFromGlobal(QCursor.pos()))

    def _zone_page(self, zone: str, *, click: bool) -> str | None:
        """Aba que cada parte da pílula abre. No hover, só anéis e clima (intenção clara)."""
        if not click and not self.ctx.settings.zone_open:
            return None
        pages = {"rings": "limits", "weather": "weather"}
        if click:
            pages |= {"date": "agenda", "time": "home", "title": "home"}
        page = pages.get(zone)
        return page if page and (page == "home" or self.ctx.settings.module_enabled(page)) else None

    def enterEvent(self, _event: object) -> None:
        self.collapse_timer.stop()
        self._media_hold = False
        self._hover_zone = self._zone()
        if self.expanded or self._dragging or not self.ctx.settings.hover_open:
            return
        if time.time() - self._collapsed_at < COLLAPSE_COOLDOWN:
            return  # o mouse ainda estava sobre o painel que acabou de fechar
        self._schedule_hover(self._hover_zone)

    def _schedule_hover(self, zone: str) -> None:
        delay = self.ctx.settings.hover_delay_ms
        if zone in ("media", "title") and self.pill.has_media:
            delay = max(delay, MEDIA_HOVER_DELAY)  # dá tempo de usar a roda ou o clique na música
        if delay <= 0:
            self._hover_expand()  # abre na hora
        else:
            self.hover_timer.start(delay)

    def _hover_expand(self) -> None:
        if self.expanded or self._dragging or self._press is not None or self._media_hold:
            return
        if self.geometry().contains(QCursor.pos()):
            self.expand(self._zone_page(self._zone(), click=False))

    def leaveEvent(self, _event: object) -> None:
        self.hover_timer.stop()
        self._media_hold = False
        if self.expanded and not self._dragging:
            self.collapse_timer.start(self.ctx.settings.collapse_delay_ms)

    def wheelEvent(self, event: object) -> None:
        """Roda sobre a pílula com música: volume. Shift ou roda lateral: troca de faixa."""
        if self.expanded or not self.pill.has_media:
            return
        self.hover_timer.stop()
        self._media_hold = True  # mexeu na música: não abre o painel por cima
        delta = event.angleDelta()  # type: ignore[attr-defined]
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)  # type: ignore[attr-defined]
        horizontal = delta.x() if delta.x() else (delta.y() if shift else 0)
        if horizontal:
            (self.ctx.media.next if horizontal < 0 else self.ctx.media.previous)()
        elif delta.y():
            system.volume_step(delta.y() > 0, max(1, abs(delta.y()) // 120))
        event.accept()  # type: ignore[attr-defined]

    # ---------- arrastar e soltar (Atalhos) ----------
    def _accepts_files(self, event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> bool:
        return event.mimeData().hasUrls() and self.ctx.settings.module_enabled("shortcuts")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if not self._accepts_files(event):
            event.ignore()
            return
        event.acceptProposedAction()
        self._drag_files = True
        self.hover_timer.stop()
        self.collapse_timer.stop()
        if not self.expanded:
            self.expand("shortcuts")
        elif self.panel.current != "shortcuts":
            self.panel.select("shortcuts")
        self.panel.drop_hint.show()
        self.panel.drop_hint.raise_()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._accepts_files(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._drag_files = False
        self.panel.drop_hint.hide()
        if self.expanded:
            self.collapse_timer.start(max(800, self.ctx.settings.collapse_delay_ms))

    def dropEvent(self, event: QDropEvent) -> None:
        self._drag_files = False
        self.panel.drop_hint.hide()
        if not self._accepts_files(event):
            event.ignore()
            return
        paths = [u.toLocalFile() or u.toString() for u in event.mimeData().urls()]
        page = self.panel.page("shortcuts")
        if page is not None and paths:
            self.panel.select("shortcuts")
            page.add_paths(paths)  # type: ignore[attr-defined]
            event.acceptProposedAction()
        # Depois de soltar, o Windows nem sempre avisa que o mouse saiu: confere sozinho.
        self.collapse_timer.start(max(1500, self.ctx.settings.collapse_delay_ms))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self.expanded:
            self.collapse_timer.start(250)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.collapse()
            return
        if (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
            and Qt.Key.Key_1 <= event.key() <= Qt.Key.Key_9
        ):
            self.panel.select_index(event.key() - Qt.Key.Key_1)
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and not self.expanded and self.pill.has_media:
            self.hover_timer.stop()
            self._media_hold = True
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.expanded and event.position().y() > HEADER_HEIGHT:
            return
        self._press = event.globalPosition().toPoint()
        self._origin = self.pos()
        self.hover_timer.stop()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press is None:
            if not self.expanded and self.hover_timer.isActive() and not self._media_hold:
                zone = self.pill.zone_at(event.position().toPoint())
                if zone != self._hover_zone:  # saiu da música para outra parte: abre na hora
                    self._hover_zone = zone
                    self.hover_timer.stop()
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
        if event.button() == Qt.MouseButton.MiddleButton and not self.expanded and self.pill.has_media:
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
        elif not self.expanded and self._toast is not None:
            kind = self._toast.kind
            self.expand({"limits": "limits", "agenda": "agenda"}.get(kind, "home"))
        elif not self.expanded:
            zone = self.pill.zone_at(event.position().toPoint())
            if zone == "media":  # capa ou equalizador: tocar/pausar
                self._media_hold = True
                self.ctx.media.play_pause()
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

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(theme.ISLAND_RADIUS, rect.height() / 2)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.fillPath(path, QColor(theme.BG))
        sheen = QLinearGradient(0, 0, 0, min(rect.height(), 140))
        sheen.setColorAt(0, QColor(255, 255, 255, 9))
        sheen.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillPath(path, sheen)
        p.setPen(QPen(QColor(255, 255, 255, 20), 1))
        p.drawPath(path)

    def closeEvent(self, event: QEvent) -> None:
        self.hide()
        event.ignore()

    # ---------- reações ----------
    def _tick(self) -> None:
        if self.expanded:
            self.panel.refresh_clock()
        else:
            self.pill.refresh_clock()

    def _resize_compact(self) -> None:
        if not self.expanded and not self._dragging:
            target = self.target_rect(False)
            if target != self.geometry():
                self._animate(target)

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
    def show_toast(self, toast: Toast) -> None:
        """A ilha cresce um pouco com o aviso e volta a ser pílula sozinha."""
        can_show = (
            self.ctx.settings.island_toasts
            and self.isVisible()
            and not self._auto_hidden
            and not self.expanded
            and not self._dragging
        )
        if not can_show:
            if toast.kind in ("limits", "agenda", "timer"):
                self.ctx.tray_message.emit(toast.title, toast.text)
            return
        if toast.kind == "music":  # só a música mais recente importa
            self._toasts = [t for t in self._toasts if t.kind != "music"]
            if self._toast is not None and self._toast.kind == "music":
                self._toast = toast
                self.toast_view.show_toast(toast)
                self.toast_timer.start(int(toast.seconds * 1000))
                return
        self._toasts.append(toast)
        if self._toast is None:
            self._next_toast()

    def _next_toast(self) -> None:
        if self._toast is not None or not self._toasts or self.expanded:
            return
        self._toast = self._toasts.pop(0)
        self.toast_view.show_toast(self._toast)
        self.toast_timer.start(int(self._toast.seconds * 1000))
        self._animate(self.target_rect(False))

    def _end_toast(self) -> None:
        if self._toast is None:
            return
        self._toast = None
        self.toast_view.hide()
        if not self.expanded:
            self._animate(self.target_rect(False))
        if self._toasts:
            QTimer.singleShot(350, self._next_toast)

    def _cancel_toasts(self) -> None:
        self.toast_timer.stop()
        self._toasts.clear()
        self._toast = None
        self.toast_view.hide()

    def _toast_art(self, data: bytes) -> None:
        if self._toast is not None and self._toast.kind == "music" and data:
            self._toast.art = data
            self.toast_view.show_toast(self._toast)

    def _limit_alert(self, provider: str, title: str, message: str) -> None:
        color = theme.provider_color(provider)
        self.show_toast(Toast(title, message, icon="gauge", color=color, seconds=5, kind="limits"))

    def _reminder(self, event: object, minutes: int) -> None:
        if self.ctx.settings.agenda_reminders:
            self.show_toast(event_toast(event, minutes))  # type: ignore[arg-type]

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
        if not self._dragging and self.anim.state() != QPropertyAnimation.State.Running:
            target = self.target_rect(self.expanded)
            if target.topLeft() != self.geometry().topLeft() or not self.expanded:
                self._animate(target)

    def _check_fullscreen(self) -> None:
        if not self.ctx.settings.hide_fullscreen or self.expanded:
            if self._auto_hidden:
                self._auto_hidden = False
                self.show()
            return
        covered = system.fullscreen_app_over(int(self.winId()))
        if covered and self.isVisible():
            self._auto_hidden = True
            self.hide()
        elif not covered and self._auto_hidden:
            self._auto_hidden = False
            self.show()

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
            self.toggle()
            return True, 0
        return False, 0

    def user_hidden(self, hidden: bool) -> None:
        """Ocultar/mostrar pela bandeja (sem confundir com a tela cheia)."""
        self._auto_hidden = False
        self.setVisible(not hidden)
