"""Componentes visuais pintados à mão: leves e consistentes."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ventude.ui import icons, theme

Semi = QFont.Weight.DemiBold


def hbox(*items: QWidget | QLayout | int | None, spacing: int = 8, margins: int = 0) -> QHBoxLayout:
    return _fill(QHBoxLayout(), items, spacing, margins)


def vbox(*items: QWidget | QLayout | int | None, spacing: int = 8, margins: int = 0) -> QVBoxLayout:
    return _fill(QVBoxLayout(), items, spacing, margins)


def _fill(layout, items, spacing, margins):  # noqa: ANN001, ANN202
    layout.setSpacing(spacing)
    layout.setContentsMargins(margins, margins, margins, margins)
    for item in items:
        if item is None:
            layout.addStretch(1)
        elif isinstance(item, int):
            layout.addSpacing(item)
        elif isinstance(item, QLayout):
            layout.addLayout(item)
        else:
            layout.addWidget(item)
    return layout


def label(text: str = "", name: str = "", *, wrap: bool = False, parent: QWidget | None = None) -> QLabel:
    widget = QLabel(text, parent)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def repolish(widget: QWidget) -> None:
    """Reaplica a folha de estilo depois de trocar objectName ou propriedades."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    return line


class Card(QFrame):
    clicked = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        clickable: bool = False,
        margins: tuple[int, int, int, int] = (16, 14, 16, 14),
        spacing: int = 8,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setProperty("clickable", clickable)
        self._clickable, self._pressed = clickable, False
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(spacing)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._pressed and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        self._pressed = False
        super().mouseReleaseEvent(event)


class IconButton(QPushButton):
    def __init__(
        self,
        name: str,
        tooltip: str,
        size: int = 32,
        icon_size: int = 18,
        color: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Icon")
        self._icon_size = icon_size
        self.setFixedSize(size, size)
        self.set_icon(name, color)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_icon(self, name: str, color: str | None = None) -> None:
        self.setIcon(icons.icon(name, color, self._icon_size))


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if name:
            self.setObjectName(name)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(12)
        self._full = ""
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text or ""
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        width = max(12, self.width() - 2)
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, width)
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class Badge(QLabel):
    def __init__(self, text: str = "", color: str = theme.ACCENT, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.set_color(color)

    def set_color(self, value: str) -> None:
        c = QColor(value)
        self.setStyleSheet(
            f"background: rgba({c.red()},{c.green()},{c.blue()},0.14); color: {value};"
            "border-radius: 8px; padding: 2px 8px; font-size: 11px; font-weight: 600;"
        )


class Ring(QWidget):
    """Anel de progresso (estilo medidor) com animação suave."""

    def __init__(self, size: int = 56, thickness: float = 6, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._thickness = thickness
        self._target: float | None = None
        self._shown = 0.0
        self._color = theme.ACCENT
        self._base = ""  # cor da IA: trilho e ponto central continuam com ela em alerta
        self._marker = False
        self._text = ""
        self._dim = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(650)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, value: float) -> None:
        self._shown = float(value)
        self.update()

    def set_size(self, size: int, thickness: float) -> None:
        if size != self.width() or thickness != self._thickness:
            self.setFixedSize(size, size)
            self._thickness = thickness
            self.update()

    def set_value(
        self,
        value: float | None,
        color: str,
        text: str = "",
        *,
        dim: bool = False,
        animate: bool = True,
        base: str = "",
        marker: bool = False,
    ) -> None:
        self._color, self._text, self._dim = color, text, dim
        self._base, self._marker = base, marker
        target = float(max(0.0, min(100.0, float(value or 0.0))))
        changed = value != self._target
        self._target = value
        if changed and animate and self.isVisible():
            self._anim.stop()
            self._anim.setStartValue(self._shown)
            self._anim.setEndValue(target)
            self._anim.start()
        elif changed:
            self._shown = target
        self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._thickness
        rect = QRectF(self.rect()).adjusted(t / 2 + 0.5, t / 2 + 0.5, -t / 2 - 0.5, -t / 2 - 0.5)
        if self._target is None:
            track = theme.color(theme.STROKE_2)
        elif self._base and self._base != self._color:
            track = theme.color(self._base, 0.38)  # em alerta, o trilho mostra de qual IA é
        else:
            track = theme.color(self._color, 0.16)
        pen = QPen(track, t)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawEllipse(rect)
        if self._target is not None and self._shown > 0.4:
            pen.setColor(theme.color(self._color, 0.5 if self._dim else 1.0))
            p.setPen(pen)
            p.drawArc(rect, 90 * 16, int(-self._shown / 100 * 360 * 16))
        if self._marker and self._base:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color(self._base, 0.6 if self._dim else 1.0))
            radius = max(1.8, (self.width() - 2 * t) * 0.2)
            p.drawEllipse(QRectF(self.rect()).center(), radius, radius)
        if self._text:
            p.setPen(theme.color(theme.TEXT_2 if self._dim else theme.TEXT))
            p.setFont(theme.font(max(10, int(self.width() * 0.24)), Semi))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)


class UsageBar(QWidget):
    """Rótulo, porcentagem, barra e texto de renovação, numa única pintura."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(50)
        self._label, self._sub, self._color = "", "", theme.ACCENT
        self._note = ""  # aviso em destaque depois do subtexto (ex.: ritmo alto)
        self._used: float | None = None
        self._dim = False

    def set_data(
        self,
        label_text: str,
        used: float | None,
        color: str,
        sub: str = "",
        tip: str = "",
        dim: bool = False,
        note: str = "",
    ) -> None:
        self._label, self._used, self._color, self._sub, self._dim = label_text, used, color, sub, dim
        self._note = note
        self.setToolTip(tip)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(220, 50)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        pct = "—" if self._used is None else f"{self._used:.0f}%"
        p.setFont(theme.font(13, Semi))
        fm = p.fontMetrics()
        pct_w = fm.horizontalAdvance(pct)
        p.setPen(theme.color(theme.TEXT))
        p.drawText(QRectF(w - pct_w, 0, pct_w, 18), Qt.AlignmentFlag.AlignVCenter, pct)
        p.setFont(theme.font(12, Semi))
        p.setPen(theme.color(theme.TEXT if not self._dim else theme.TEXT_2))
        text = p.fontMetrics().elidedText(self._label, Qt.TextElideMode.ElideRight, w - pct_w - 10)
        p.drawText(QRectF(0, 0, w - pct_w - 8, 18), Qt.AlignmentFlag.AlignVCenter, text)
        bar = QRectF(0, 23, w, 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color(theme.SURFACE_3))
        p.drawRoundedRect(bar, 3, 3)
        if self._used is not None and self._used > 0:
            fill = QRectF(bar.x(), bar.y(), max(6.0, bar.width() * min(self._used, 100) / 100), 6)
            p.setBrush(theme.color(self._color, 0.55 if self._dim else 1.0))
            p.drawRoundedRect(fill, 3, 3)
        if self._sub or self._note:
            p.setFont(theme.font(11))
            p.setPen(theme.color(theme.MUTED))
            fm = p.fontMetrics()
            note = (f" · {self._note}" if self._sub else self._note) if self._note else ""
            note_w = min(fm.horizontalAdvance(note), int(w * 0.62)) if note else 0
            sub = fm.elidedText(self._sub, Qt.TextElideMode.ElideRight, w - note_w)
            p.drawText(QRectF(0, 34, w, 16), Qt.AlignmentFlag.AlignVCenter, sub)
            if note:
                p.setPen(theme.color(theme.WARN))
                x = fm.horizontalAdvance(sub)
                text = fm.elidedText(note, Qt.TextElideMode.ElideRight, int(w - x))
                p.drawText(QRectF(x, 34, w - x, 16), Qt.AlignmentFlag.AlignVCenter, text)


class Sparkline(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(96, 30)
        self._values: list[float] = []
        self._color = theme.UP
        self._bounds: tuple[float, float] | None = None

    def set_values(self, values: list[float], color: str, bounds: tuple[float, float] | None = None) -> None:
        """bounds fixa a escala (ex.: 0–100 para porcentagem); sem ela, usa o mínimo e o máximo."""
        self._values, self._color, self._bounds = list(values), color, bounds
        self.update()

    def paintEvent(self, _event: object) -> None:
        if len(self._values) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lo, hi = self._bounds or (min(self._values), max(self._values))
        span = (hi - lo) or 1.0
        r = QRectF(self.rect()).adjusted(1.5, 3, -1.5, -3)
        step = r.width() / (len(self._values) - 1)
        points = [
            QPointF(r.left() + i * step, r.bottom() - (v - lo) / span * r.height())
            for i, v in enumerate(self._values)
        ]
        line = QPainterPath(points[0])
        for point in points[1:]:
            line.lineTo(point)
        area = QPainterPath(line)
        area.lineTo(points[-1].x(), r.bottom() + 3)
        area.lineTo(points[0].x(), r.bottom() + 3)
        area.closeSubpath()
        gradient = QLinearGradient(0, r.top(), 0, r.bottom() + 3)
        gradient.setColorAt(0, theme.color(self._color, 0.28))
        gradient.setColorAt(1, theme.color(self._color, 0.0))
        p.fillPath(area, QBrush(gradient))
        pen = QPen(theme.color(self._color), 1.6)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPath(line)


class Switch(QAbstractButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda _v: self.update())
        self.toggled.connect(self._animate)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(0.0 if checked else 1.0)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, 22)

    def paintEvent(self, _event: object) -> None:
        running = self._anim.state() == QVariantAnimation.State.Running
        pos = float(self._anim.currentValue()) if running else (1.0 if self.isChecked() else 0.0)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off, on = QColor(theme.STROKE_2), QColor(theme.ACCENT)
        track = QColor(
            int(off.red() + (on.red() - off.red()) * pos),
            int(off.green() + (on.green() - off.green()) * pos),
            int(off.blue() + (on.blue() - off.blue()) * pos),
        )
        if not self.isEnabled():
            track.setAlphaF(0.4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 40, 22), 11, 11)
        p.setBrush(QColor("#FFFFFF") if pos > 0.5 else QColor(theme.TEXT))
        p.drawEllipse(QRectF(3 + pos * 18, 3, 16, 16))


class CheckCircle(QAbstractButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(2, 2, 18, 18)
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color(theme.ACCENT))
            p.drawEllipse(r)
            pen = QPen(theme.color(theme.ACCENT_TEXT), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            path = QPainterPath(QPointF(7, 11.2))
            path.lineTo(9.8, 14)
            path.lineTo(15, 8.4)
            p.drawPath(path)
        else:
            hover = self.underMouse()
            p.setPen(QPen(theme.color(theme.TEXT_2 if hover else theme.MUTED), 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r)


class Equalizer(QWidget):
    """Três barrinhas que dançam enquanto a música toca."""

    def __init__(self, color: str = theme.ACCENT, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._color = color
        self._playing = False
        self._dim = False
        self._timer = QTimer(self)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self.update)

    def set_dim(self, dim: bool) -> None:
        """Pausado: barrinhas paradas e apagadas."""
        self._dim = dim
        self.update()

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        if playing and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def showEvent(self, event: object) -> None:
        super().showEvent(event)
        self.set_playing(self._playing)

    def hideEvent(self, event: object) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color(theme.TEXT_2 if self._dim else self._color, 0.7 if self._dim else 1.0))
        t = time.monotonic()
        for i in range(3):
            if self._playing:
                level = 0.35 + 0.65 * abs(math.sin(t * (3.1 + i * 1.3) + i * 1.7))
            else:
                level = 0.3
            h = max(3.0, 13 * level)
            p.drawRoundedRect(QRectF(1 + i * 4.6, 14 - h, 3, h), 1.5, 1.5)


class TempRange(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(6)
        self.setMinimumWidth(80)
        self._lo = self._hi = 0.0
        self._min = 0.0
        self._max = 1.0

    def set_range(self, lo: float, hi: float, week_min: float, week_max: float) -> None:
        self._lo, self._hi, self._min, self._max = lo, hi, week_min, max(week_max, week_min + 1)
        self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        w = self.width()
        p.setBrush(theme.color(theme.SURFACE_3))
        p.drawRoundedRect(QRectF(0, 0, w, 6), 3, 3)
        span = self._max - self._min
        x1 = (self._lo - self._min) / span * w
        x2 = (self._hi - self._min) / span * w
        gradient = QLinearGradient(0, 0, w, 0)
        gradient.setColorAt(0, theme.color(theme.SKY))
        gradient.setColorAt(1, theme.color(theme.SUN))
        p.setBrush(QBrush(gradient))
        p.drawRoundedRect(QRectF(x1, 0, max(6.0, x2 - x1), 6), 3, 3)


class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.badge = QLabel()
        self.badge.setFixedSize(52, 52)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setStyleSheet(f"background: {theme.SURFACE_2}; border-radius: 26px;")
        self.badge.setPixmap(icons.pixmap(icon_name, theme.TEXT_2, 24))
        self.title = label(title, "H2")
        self.text = label(text, "Muted", wrap=True)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.actions.addStretch(1)
        layout = vbox(None, spacing=10)
        layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignHCenter)
        for widget in (self.title, self.text):
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(widget)
        layout.addLayout(self.actions)
        layout.addStretch(1)
        self.text.setVisible(bool(text))
        self.setLayout(layout)
        self._ended = False

    def add_action(self, button: QPushButton) -> None:
        self.actions.addWidget(button)

    def finish(self) -> None:
        if not self._ended:
            self.actions.addStretch(1)
            self._ended = True


def rounded_pixmap(data: bytes, size: int, radius: float) -> QPixmap | None:
    source = QPixmap()
    if not data or not source.loadFromData(data):
        return None
    scale = 3
    side = size * scale
    source = source.scaled(
        side,
        side,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    out = QPixmap(side, side)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, side, side), radius * scale, radius * scale)
    p.setClipPath(path)
    p.drawPixmap((side - source.width()) // 2, (side - source.height()) // 2, source)
    p.end()
    out.setDevicePixelRatio(scale)
    return out


def icon_tile(name: str, color: str, size: int = 36, icon_size: int = 18) -> QLabel:
    tile = QLabel()
    tile.setFixedSize(size, size)
    tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
    c = QColor(color)
    tile.setStyleSheet(
        f"background: rgba({c.red()},{c.green()},{c.blue()},0.13); border-radius: {size // 3}px;"
    )
    tile.setPixmap(icons.pixmap(name, color, icon_size))
    return tile
