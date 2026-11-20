"""Ícone voador: arco Bézier do Lançador até a Doca ao fixar um app (Voo)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import QWidget

from ventude.ui import motion
from ventude.ui.shell.common import draw_pixmap

START_SIZE = 40.0
END_SIZE = 28.0
PAD = 36


class FlyingIcon(QWidget):
    """Sobrepõe um ícone em arco Bézier até a doca (360 ms) e se remove ao pousar."""

    def __init__(
        self,
        pixmap: QPixmap,
        start_global: QPoint,
        end_global: QPoint,
        duration_ms: int,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        mid_x = (start_global.x() + end_global.x()) / 2.0
        arc_lift = max(48.0, min(140.0, abs(end_global.x() - start_global.x()) * 0.25 + 44.0))
        ctrl_y = min(start_global.y(), end_global.y()) - arc_lift
        ctrl_global = QPointF(mid_x, ctrl_y)

        xs = (start_global.x(), end_global.x(), round(ctrl_global.x()))
        ys = (start_global.y(), end_global.y(), round(ctrl_global.y()))
        bounds = QRect(
            min(xs) - PAD,
            min(ys) - PAD,
            (max(xs) - min(xs)) + PAD * 2,
            (max(ys) - min(ys)) + PAD * 2,
        )
        self.setGeometry(bounds)

        origin = bounds.topLeft()
        self._p0 = QPointF(start_global - origin)
        self._p1 = QPointF(ctrl_global.x() - origin.x(), ctrl_global.y() - origin.y())
        self._p2 = QPointF(end_global - origin)
        self._pixmap = pixmap
        self._progress = 0.0
        self._on_done = on_done

        self._tween = motion.Tween(self)
        self._tween.progressed.connect(self._step)
        self._tween.done.connect(self._finish)
        self.show()
        self.raise_()
        self._tween.play(duration_ms, 0.0, 1.0, motion.in_out_cubic)

    @classmethod
    def fly(
        cls,
        pixmap: QPixmap,
        start_global: QPoint,
        end_global: QPoint,
        settings: object | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> FlyingIcon | None:
        dur = motion.ms("voo", settings)
        if dur <= 0:
            if on_done is not None:
                on_done()
            return None
        return cls(pixmap, start_global, end_global, dur, on_done=on_done)

    def _step(self, value: float) -> None:
        self._progress = value
        self.update()

    def _finish(self) -> None:
        if self._on_done is not None:
            cb, self._on_done = self._on_done, None
            cb()
        self.close()
        self.deleteLater()

    def paintEvent(self, _event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._progress
        u = 1.0 - t
        pos = self._p0 * (u * u) + self._p1 * (2.0 * u * t) + self._p2 * (t * t)
        size = START_SIZE + (END_SIZE - START_SIZE) * t
        rect = QRectF(pos.x() - size / 2.0, pos.y() - size / 2.0, size, size)
        draw_pixmap(p, self._pixmap, rect)
