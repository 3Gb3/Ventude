"""Componentes visuais pintados à mão: leves e consistentes."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
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

from ventude.ui import icons, motion, theme

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


def set_qss(widget: QWidget, qss: str) -> None:
    """setStyleSheet só quando muda: cada troca faz o Qt recalcular o estilo do widget e dos filhos."""
    if widget.styleSheet() != qss:
        widget.setStyleSheet(qss)


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


class BadgeButton(IconButton):
    """Botão de ícone com um pontinho de aviso no canto (ex.: atualização do Windows pendente)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._badge = ""

    def set_badge(self, color: str = "") -> None:
        if color != self._badge:
            self._badge = color
            self.update()

    def paintEvent(self, event: object) -> None:
        super().paintEvent(event)  # type: ignore[arg-type]
        if not self._badge:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(theme.color(theme.BG), 1.6))
        p.setBrush(theme.color(self._badge))
        p.drawEllipse(QPointF(self.width() - 8.5, 8.5), 3.6, 3.6)


class PinButton(IconButton):
    """Alfinete que fixa algo na pílula (um limite das IAs, uma métrica do Sistema)."""

    def __init__(self, pinned_tip: str, free_tip: str, size: int = 24, icon_size: int = 13) -> None:
        super().__init__("pin", free_tip, size, icon_size)
        self._tips = (pinned_tip, free_tip)
        self.pinned = False
        self.set_pinned(False)

    def set_pinned(self, pinned: bool) -> None:
        self.pinned = pinned
        self.set_icon("pin", theme.ACCENT if pinned else theme.STROKE_2)
        self.setToolTip(self._tips[0] if pinned else self._tips[1])


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
        self._anim.setDuration(motion.DURATIONS["anel"])
        self._anim.setEasingCurve(motion.easing("enfase"))
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
            span = int(-self._shown / 100 * 360 * 16)
            if self.width() >= 28 and not self._dim:
                # anel grande: um gradiente suave da cor, mais clara na ponta
                grad = QConicalGradient(rect.center(), 90)
                grad.setColorAt(0.0, theme.mix(self._color, "#FFFFFF", 0.18))
                grad.setColorAt(1.0, theme.color(self._color, 0.8))
                pen.setBrush(QBrush(grad))
            else:
                pen.setColor(theme.color(self._color, 0.5 if self._dim else 1.0))
            p.setPen(pen)
            p.drawArc(rect, 90 * 16, span)
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
        self._fill = 0.0  # o que está desenhado (anima até self._used)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(motion.DURATIONS["barra"])
        self._anim.setEasingCurve(motion.easing("enfase"))
        self._anim.valueChanged.connect(self._on_fill)

    def _on_fill(self, value: object) -> None:
        self._fill = float(value)  # type: ignore[arg-type]
        self.update()

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
        target = float(min(max(used or 0.0, 0.0), 100.0))
        if used != self._used:
            self._anim.stop()
            if self.isVisible() and abs(target - self._fill) > 0.5:
                self._anim.setStartValue(self._fill)
                self._anim.setEndValue(target)
                self._anim.start()
            else:
                self._fill = target
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
        if self._used is not None and self._fill > 0.05:
            fill = QRectF(bar.x(), bar.y(), max(6.0, bar.width() * self._fill / 100), 6)
            if self._dim:
                p.setBrush(theme.color(self._color, 0.55))
            else:
                grad = QLinearGradient(fill.left(), 0, fill.right(), 0)
                grad.setColorAt(0.0, theme.color(self._color, 0.85))
                grad.setColorAt(1.0, theme.mix(self._color, "#FFFFFF", 0.15))
                p.setBrush(QBrush(grad))
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

    def expand(self, height: int = 30) -> Sparkline:
        """Ocupa a largura toda do card (o padrão é um mini-gráfico de 96 px)."""
        self.setFixedHeight(height)
        self.setMinimumWidth(60)
        self.setMaximumWidth(16_777_215)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return self

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
        self.setFixedSize(42, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(motion.DURATIONS["interruptor"])
        self._anim.setEasingCurve(motion.easing("mola_leve"))
        self._anim.valueChanged.connect(lambda _v: self.update())
        self.toggled.connect(self._animate)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        if not self.isVisible():
            self.update()
            return
        self._anim.setStartValue(0.0 if checked else 1.0)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(42, 24)

    def paintEvent(self, _event: object) -> None:
        running = self._anim.state() == QVariantAnimation.State.Running
        pos = float(self._anim.currentValue()) if running else (1.0 if self.isChecked() else 0.0)
        on = max(0.0, min(1.0, pos))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(0.5, 0.5, 41, 23)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color(theme.SURFACE_3 if self.underMouse() else theme.STROKE_2))
        p.drawRoundedRect(track, 11.5, 11.5)
        if on > 0.01:
            p.setOpacity(on if self.isEnabled() else on * 0.4)
            p.setBrush(theme.accent_gradient(0, 0, 42, 0))
            p.drawRoundedRect(track, 11.5, 11.5)
            p.setOpacity(1.0)
        # o botão estica um pouco no meio do caminho (dá sensação de mola)
        stretch = 5.0 * math.sin(math.pi * on) if running else 0.0
        width = 18.0 + stretch
        left = max(3.0, min(39.0 - width, 12.0 + pos * 18.0 - width / 2.0))
        knob = QRectF(left, 3, width, 18)
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawRoundedRect(knob.translated(0, 1), 9, 9)
        p.setBrush(QColor("#FFFFFF") if on > 0.5 else theme.color(theme.TEXT, 0.92))
        if not self.isEnabled():
            p.setOpacity(0.5)
        p.drawRoundedRect(knob, 9, 9)


class CheckCircle(QAbstractButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._pop = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(motion.DURATIONS["interruptor"])
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(motion.easing("mola"))
        self._anim.valueChanged.connect(self._on_pop)
        self.toggled.connect(self._toggled)

    def _on_pop(self, value: object) -> None:
        self._pop = float(value)  # type: ignore[arg-type]
        self.update()

    def _toggled(self, checked: bool) -> None:
        if checked and self.isVisible():
            self._anim.stop()
            self._anim.start()
        else:
            self._pop = 1.0

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(2, 2, 18, 18)
        if self.isChecked():
            k = 0.6 + 0.4 * self._pop
            c = r.center()
            r = QRectF(c.x() - 9 * k, c.y() - 9 * k, 18 * k, 18 * k)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.accent_gradient(r.left(), r.top(), r.right(), r.bottom()))
            p.drawEllipse(r)
            pen = QPen(theme.color(theme.ACCENT_TEXT), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            path = QPainterPath(QPointF(7, 11.2))
            path.lineTo(9.8, 14)
            path.lineTo(15, 8.4)
            if self._pop < 1.0:  # o risco do check se desenha junto com o pop
                length = path.length() * min(1.0, self._pop * 1.4)
                pen.setDashPattern([max(0.01, length / 2), 100.0])
                p.setPen(pen)
            p.drawPath(path)
        else:
            hover = self.underMouse()
            p.setPen(QPen(theme.color(theme.ACCENT if hover else theme.MUTED, 0.9 if hover else 1.0), 1.6))
            p.setBrush(theme.color(theme.ACCENT, 0.08) if hover else Qt.BrushStyle.NoBrush)
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
