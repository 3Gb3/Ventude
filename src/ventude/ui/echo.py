"""Eco mínimo nos monitores secundários: apenas o relógio passivo no topo."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from ventude.core.timefmt import clock
from ventude.services import dwm
from ventude.ui import brand, motion, theme
from ventude.ui.shell.common import shell_font

ECHO_HEIGHT = 30
ECHO_RADIUS = 15.0
TOP_GAP = 8


class EchoPill(QWidget):
    """Pílula passiva (WindowTransparentForInput) no topo-centro de monitores secundários."""

    def __init__(self, ctx: object, screen: object) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.ctx = ctx
        self.screen_ = screen
        self.setWindowTitle("Ventude: eco")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._shown = False
        self._opacity = 0.0
        self._time_text = ""
        self._fade = motion.Tween(self)
        self._fade.progressed.connect(self._on_fade)
        self._fade.done.connect(self._on_fade_done)
        self._timer = QTimer(self, interval=1000)
        self._timer.timeout.connect(self.tick)
        self.tick()
        self.place()

    def screen_rect(self) -> QRect:
        return self.screen_.geometry()  # type: ignore[attr-defined]

    def anchor_rect(self) -> QRect:
        area = self.screen_rect()
        font = shell_font(12, QFont.Weight.DemiBold, tabular=True)
        from PySide6.QtGui import QFontMetrics

        text = self._time_text or "00:00"
        tw = QFontMetrics(font).horizontalAdvance(text)
        width = max(68, tw + 24)
        x = area.left() + (area.width() - width) // 2
        y = area.top() + TOP_GAP
        return QRect(x, y, width, ECHO_HEIGHT)

    def place(self) -> None:
        rect = self.anchor_rect()
        self.setGeometry(rect)
        if self.winId():
            dwm.square_corners(int(self.winId()))

    def tick(self) -> None:
        settings = getattr(self.ctx, "settings", None)
        h24 = bool(getattr(settings, "clock_24h", True))
        text = clock(datetime.now(), h24)
        if text != self._time_text:
            self._time_text = text
            self.place()
            self.update()

    def set_shown(self, show: bool) -> None:
        if show == self._shown:
            return
        self._shown = show
        settings = getattr(self.ctx, "settings", None)
        dur = motion.ms("eco", settings)
        if show:
            self.tick()
            self.place()
            self._timer.start()
            if dur <= 0:
                self._opacity = 1.0
                self.setWindowOpacity(1.0)
                self.show()
                return
            self.setWindowOpacity(0.0)
            self.show()
            self._fade.play(dur, 0.0, 1.0, motion.out_cubic)
        else:
            self._timer.stop()
            if dur <= 0:
                self._opacity = 0.0
                self.hide()
                return
            self._fade.play(dur, self._opacity, 0.0, motion.in_cubic)

    def set_covered(self, covered: bool) -> None:
        """Some enquanto um jogo/vídeo está em tela cheia neste monitor."""
        if not self._shown:
            return
        if covered and self.isVisible():
            self.hide()
        elif not covered and not self.isVisible():
            self.setWindowOpacity(max(self._opacity, 1.0))
            self.show()

    def _on_fade(self, value: float) -> None:
        self._opacity = value
        self.setWindowOpacity(value)

    def _on_fade_done(self) -> None:
        if not self._shown:
            self.hide()

    def paintEvent(self, _event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        brand.paint_ink(p, rect, ECHO_RADIUS, outer_rim=True)
        p.setPen(QColor(theme.TEXT))
        p.setFont(shell_font(12, QFont.Weight.DemiBold, tabular=True))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._time_text)
