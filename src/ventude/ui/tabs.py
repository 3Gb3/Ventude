"""Abas da ilha: um indicador que desliza entre elas e abas que agrupam páginas irmãs.

Cada aba é um grupo (ex.: Agenda + Tarefas). Fechada, mostra só o ícone; ativa, cresce com o nome das
páginas do grupo lado a lado, e o nome em destaque é a página aberta. Clicar num nome apagado troca de
página sem sair do grupo. O indicador (a pílula atrás da aba ativa) desliza até a aba nova enquanto as
larguras se acomodam, tudo na mesma curva.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QFontMetricsF, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QSizePolicy, QWidget

from ventude.ui import icons, motion, theme

TAB_HEIGHT = 34
ICON = 17
PAD = 10
GAP = 7  # entre o ícone e o primeiro nome
SEG_GAP = 12  # entre os nomes do grupo


class TabButton(QAbstractButton):
    """Uma aba (grupo de páginas). `members` = [(chave, título)] das páginas ligadas do grupo."""

    picked = Signal(str)

    def __init__(self, icon_name: str, members: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon_name = icon_name
        self.members = list(members)
        self.current = self.members[0][0]
        self._open = 0.0  # 0 = só o ícone, 1 = com os nomes
        self._hover_seg = ""
        self._widths: list[tuple[str, str, float]] | None = None
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(TAB_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip(" · ".join(title for _k, title in self.members))
        self.setAccessibleName(self.toolTip())
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(motion.easing("enfase"))
        self._anim.valueChanged.connect(self._on_open)
        self.clicked.connect(lambda: self.picked.emit(self.current))  # teclado (Espaço); o mouse vai abaixo

    # ---------- estado ----------
    def set_current(self, key: str, active: bool, settings: object | None = None) -> None:
        if any(k == key for k, _ in self.members):
            self.current = key
        was = self.isChecked()
        self.setChecked(active)
        target = 1.0 if active else 0.0
        if active != was or (self._anim.state() != QVariantAnimation.State.Running and self._open != target):
            self._anim.stop()
            duration = motion.ms("indicador", settings)
            if duration <= 0 or not self.isVisible():
                self._on_open(target)
            else:
                self._anim.setDuration(duration)
                self._anim.setStartValue(self._open)
                self._anim.setEndValue(target)
                self._anim.start()
        self.update()

    def _on_open(self, value: object) -> None:
        self._open = float(value)  # type: ignore[arg-type]
        self.updateGeometry()
        self.update()
        parent = self.parentWidget()
        if parent is not None:
            parent.update()

    # ---------- medidas ----------
    def _segments(self) -> list[tuple[str, str, float]]:
        if self._widths is None:
            metrics = QFontMetricsF(theme.font(12, theme.SEMI))
            self._widths = [(k, title, metrics.horizontalAdvance(title)) for k, title in self.members]
        return self._widths

    def _text_width(self) -> float:
        segs = self._segments()
        return GAP + sum(w for _k, _t, w in segs) + SEG_GAP * (len(segs) - 1)

    def sizeHint(self) -> QSize:  # noqa: N802
        base = PAD * 2 + ICON
        return QSize(round(base + self._text_width() * self._open), TAB_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def _segment_rects(self) -> list[tuple[str, str, QRectF]]:
        x = PAD + ICON + GAP
        out = []
        for key, title, width in self._segments():
            out.append((key, title, QRectF(x - SEG_GAP / 2, 0, width + SEG_GAP, self.height())))
            x += width + SEG_GAP
        return out

    def segment_at(self, x: float) -> str:
        if self._open < 0.99 or len(self.members) < 2:
            return ""
        for key, _title, rect in self._segment_rects():
            if rect.left() <= x <= rect.right():
                return key
        return ""

    # ---------- pintura ----------
    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self.isChecked()
        hover = self.underMouse()
        if hover and not active:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("#FFFFFF", 0.045))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 11, 11)
        icon_color = theme.TEXT if (active or hover) else theme.TEXT_2
        filled = f"{self.icon_name}-fill"
        name = filled if active and filled in icons.PATHS else self.icon_name
        top = (self.height() - ICON) / 2.0
        p.drawPixmap(QRectF(PAD, top, ICON, ICON).toRect(), icons.pixmap(name, icon_color, ICON))
        if self._open <= 0.02:
            return
        p.save()
        p.setClipRect(QRectF(PAD + ICON, 0, self.width() - PAD - ICON - PAD / 2, self.height()))
        p.setFont(theme.font(12, theme.SEMI))
        many = len(self.members) > 1
        for key, title, rect in self._segment_rects():
            is_current = key == self.current
            if is_current:
                color = theme.color(theme.TEXT, self._open)
            elif key == self._hover_seg:
                color = theme.color(theme.TEXT_2, self._open)
            else:
                color = theme.color(theme.MUTED, self._open)
            p.setPen(color)
            text_rect = rect.adjusted(SEG_GAP / 2, 0, -SEG_GAP / 2, 0)
            p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
            if many and is_current:  # tracinho com o gradiente da marca sob a página aberta
                y = self.height() - 7.0
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.accent_gradient(text_rect.left(), 0, text_rect.right(), 0, self._open))
                p.drawRoundedRect(QRectF(text_rect.left(), y, text_rect.width(), 2.0), 1.0, 1.0)
        p.restore()

    # ---------- mouse ----------
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        seg = self.segment_at(event.position().x())
        if seg != self._hover_seg:
            self._hover_seg = seg
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hover_seg = ""
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            seg = self.segment_at(event.position().x())
            self.setDown(False)
            self.picked.emit(seg or self.current)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def nextCheckState(self) -> None:  # noqa: N802
        pass  # quem marca é a TabStrip


class TabStrip(QWidget):
    """Fileira de abas com o indicador deslizante pintado atrás da aba ativa."""

    picked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(2)
        self.row.addStretch(1)
        self.buttons: list[TabButton] = []
        self.active: TabButton | None = None
        self._from: QRectF | None = None
        self._t = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_step)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(TAB_HEIGHT)

    def clear(self) -> None:
        for button in self.buttons:
            self.row.removeWidget(button)
            button.deleteLater()
        self.buttons = []
        self.active = None
        self._from = None

    def add(self, button: TabButton) -> None:
        button.setParent(self)
        button.picked.connect(self.picked.emit)
        button.installEventFilter(self)
        self.row.insertWidget(len(self.buttons), button)
        self.buttons.append(button)

    def activate(self, button: TabButton | None, member: str, settings: object | None = None) -> None:
        previous = self.active
        start = self.indicator_rect()
        self.active = button
        for b in self.buttons:
            b.set_current(member if b is button else b.current, b is button, settings)
        duration = motion.ms("indicador", settings)
        if previous is None or previous is button or duration <= 0 or not self.isVisible() or start is None:
            self._anim.stop()
            self._from, self._t = None, 1.0
        else:
            self._from, self._t = start, 0.0
            self._anim.stop()
            self._anim.setDuration(duration)
            self._anim.setEasingCurve(motion.easing("enfase"))
            self._anim.start()
        self.updateGeometry()
        self.update()

    def _on_step(self, value: object) -> None:
        self._t = float(value)  # type: ignore[arg-type]
        self.update()

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        if watched is self.active and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self.update()
        return False

    def indicator_rect(self) -> QRectF | None:
        if self.active is None:
            return None
        target = QRectF(self.active.geometry())
        if self._from is None or self._t >= 1.0:
            return target
        t = self._t
        f = self._from
        return QRectF(
            f.x() + (target.x() - f.x()) * t,
            f.y() + (target.y() - f.y()) * t,
            f.width() + (target.width() - f.width()) * t,
            f.height() + (target.height() - f.height()) * t,
        )

    def paintEvent(self, _event: object) -> None:
        rect = self.indicator_rect()
        if rect is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme.color("#FFFFFF", 0.07), 1.0))
        p.setBrush(theme.color(theme.SURFACE_3, 0.85))
        p.drawRoundedRect(r, 11, 11)
        # brilho de topo: a pílula parece um botão de vidro
        p.setPen(QPen(theme.color("#FFFFFF", 0.06), 1.0))
        p.drawLine(QPointF(r.left() + 10, r.top() + 1), QPointF(r.right() - 10, r.top() + 1))
