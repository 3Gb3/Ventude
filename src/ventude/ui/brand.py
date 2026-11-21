"""Identidade visual compartilhada do Ventude: Tinta, Interruptor do logo, sombras e menus."""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QWidget

from ventude.services import dwm
from ventude.ui import theme


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    r = max(0.0, min(float(radius), rect.width() / 2.0, rect.height() / 2.0))
    path.addRoundedRect(rect, r, r)
    return path


def paint_ink(
    p: QPainter,
    rect: QRectF,
    radius: float,
    *,
    bg: str = theme.INK,
    alpha: float = 1.0,
    sheen: bool = True,
    hairline: bool = True,
    outer_rim: bool = False,
) -> QPainterPath:
    """Pinta uma superfície no material Tinta: fundo sólido, brilho de topo e fio branco 8%."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    inner = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    r = max(0.0, min(float(radius), inner.width() / 2.0, inner.height() / 2.0))
    if outer_rim and inner.width() > 4 and inner.height() > 4:
        rim_path = rounded_path(inner.adjusted(-0.75, -0.75, 0.75, 0.75), r + 0.75)
        p.setPen(QPen(QColor(0, 0, 0, int(90 * alpha)), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(rim_path)
    path = rounded_path(inner, r)
    p.fillPath(path, theme.color(bg, alpha))
    if sheen and inner.height() > 4:
        grad = QLinearGradient(0, inner.top(), 0, inner.top() + min(inner.height(), 140.0))
        grad.setColorAt(0.0, QColor(255, 255, 255, int(9 * alpha)))
        grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillPath(path, grad)
    if hairline:
        p.setPen(QPen(QColor(255, 255, 255, int(20 * alpha)), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    p.restore()
    return path


def _mix_color(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def paint_switch(
    p: QPainter,
    rect: QRectF,
    on: float = 1.0,
    outline: float = 0.0,
    dim: float = 1.0,
    *,
    accent: str = theme.ACCENT,
    outline_color: str | None = None,
    crt: float = 0.0,
    sink_y: float = 0.0,
    border_color: str = "#5A616D",
    breath: float = 1.0,
) -> None:
    """Desenha o Interruptor do logo em qualquer tamanho (Doca, Cortina, Lançador, bandeja)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    on = max(0.0, min(1.0, float(on)))
    dim = max(0.0, min(1.0, float(dim)))
    h = max(4.0, rect.height())
    w = max(8.0, rect.width())
    r = h / 2.0
    stroke = max(1.2, h * 0.11)
    body = QRectF(rect)
    path = rounded_path(body, r)
    p.setPen(QPen(theme.color(border_color, dim), stroke))
    p.setBrush(theme.color(theme.BG, dim))
    p.drawPath(path)

    # Traço discreto do lado esquerdo quando ligado (como na marca original)
    if on > 0.25 and crt <= 0.01:
        dash_h = max(1.8, h * 0.16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color(theme.TEXT_2, dim * on * 0.7))
        p.drawRoundedRect(
            QRectF(body.left() + h * 0.30, body.center().y() - dash_h / 2.0, w * 0.26, dash_h),
            dash_h / 2.0,
            dash_h / 2.0,
        )

    # Ponto (desliza da esquerda para a direita)
    dot_d = h * 0.52
    left_x = body.left() + h * 0.24
    right_x = body.right() - dot_d - h * 0.24
    dot_x = left_x + (right_x - left_x) * on
    dot_cy = body.center().y() + sink_y
    dot_color = _mix_color(QColor(theme.TEXT_2), QColor(accent), on)
    sink_fade = max(0.0, 1.0 - abs(sink_y) / max(12.0, h * 0.45)) if sink_y else 1.0
    dot_color.setAlphaF(dim * breath * sink_fade)

    if crt > 0.0:
        t = max(0.0, min(1.0, crt))
        if t < 0.55:
            k = t / 0.55
            dw = dot_d * (1.0 + 0.5 * k)
            dh = max(2.0, dot_d * (1.0 - k))
            alpha = 1.0
        else:
            k = (t - 0.55) / 0.45
            dw = max(0.0, dot_d * 1.5 * (1.0 - k))
            dh = 2.0
            alpha = 1.0 - k
        if dw > 0.5 and alpha > 0.01:
            dot_color.setAlphaF(dim * alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dot_color)
            cx = dot_x + dot_d / 2.0
            p.drawRoundedRect(QRectF(cx - dw / 2.0, dot_cy - dh / 2.0, dw, dh), dh / 2.0, dh / 2.0)
    elif dot_color.alphaF() > 0.01:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot_color)
        p.drawEllipse(QPointF(dot_x + dot_d / 2.0, dot_cy), dot_d / 2.0, dot_d / 2.0)

    # Contorno de contagem regressiva (Cortina)
    if outline > 0.001:
        progress = max(0.0, min(1.0, float(outline)))
        pen_w = max(2.0, h * 0.07)
        pen = QPen(theme.color(outline_color or accent, dim), pen_w)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        length = max(1.0, path.length())
        dash_units = (progress * length) / pen_w
        gap_units = (length / pen_w) + 4.0
        pen.setDashPattern([max(0.05, dash_units), gap_units])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    p.restore()


class ShadowCache:
    """Sombra de contato 9-slice pré-renderizada (sem QGraphicsDropShadowEffect)."""

    PAD = 24
    OFFSET_Y = 8

    def __init__(self, max_items: int = 16) -> None:
        self._cache: OrderedDict[tuple[int, int, int], QPixmap] = OrderedDict()
        self._max = max_items

    def _get(self, width: int, height: int, radius: int) -> QPixmap:
        key = (width, height, radius)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        pad = self.PAD
        pm = QPixmap(width + pad * 2, height + pad * 2)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        base = QRectF(pad, pad + self.OFFSET_Y, width, height)
        steps = 14
        for step in range(steps, 0, -1):
            frac = 1.0 - (step - 1) / steps
            alpha = int(16 * (frac**1.6))
            spread = step * 1.4
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawRoundedRect(
                base.adjusted(-spread, -spread * 0.6, spread, spread),
                radius + spread,
                radius + spread,
            )
        p.end()
        self._cache[key] = pm
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)
        return pm

    def paint(self, p: QPainter, rect: QRectF, radius: float, opacity: float = 1.0) -> None:
        w, h = round(rect.width()), round(rect.height())
        if w <= 0 or h <= 0 or opacity <= 0.01:
            return
        pm = self._get(w, h, round(radius))
        p.save()
        p.setOpacity(max(0.0, min(1.0, opacity)))
        p.drawPixmap(QPointF(rect.left() - self.PAD, rect.top() - self.PAD), pm)
        p.restore()


class _InkMenu(QMenu):
    def showEvent(self, event: object) -> None:
        super().showEvent(event)  # type: ignore[arg-type]
        hwnd = int(self.winId())
        dwm.square_corners(hwnd)
        dwm.border_color(hwnd, 0xFFFFFFFE)


def ink_menu(parent: QWidget | None = None) -> QMenu:
    """Menu flutuante no material Tinta (raio 14, itens 34 px com raio 10)."""
    menu = _InkMenu(parent)
    menu.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
    # A ilha, a Doca e o Lançador são "sempre no topo": sem isto o menu abria por baixo deles.
    menu.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    menu.setStyleSheet(
        f"QMenu {{ background: {theme.INK_RAISED}; border: 1px solid {theme.STROKE_2}; "
        f"border-radius: {theme.R_MENU}px; padding: 6px; }}"
        f"QMenu::item {{ padding: 7px 26px 7px 12px; min-height: 20px; border-radius: {theme.R_ITEM}px; }}"
        f"QMenu::item:selected {{ background: rgba(255,255,255,0.08); }}"
        f"QMenu::item:disabled {{ color: {theme.MUTED}; }}"
        f"QMenu::separator {{ height: 1px; background: rgba(255,255,255,0.08); margin: 5px 8px; }}"
    )
    return menu
