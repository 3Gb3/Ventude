"""Efeito "buraco negro" da ilha (tecla Windows 3 vezes).

Sumir: a ilha vira uma foto, fatiada em tiras verticais. As tiras das pontas são puxadas primeiro e mais
forte para o meio (e ficam mais baixas, como se esticassem para dentro), tudo gira um pouco, um disco
escuro com anel de luz cresce no centro e, no fim, colapsa num ponto com um brilho rápido.
Voltar: o mesmo ao contrário (a ilha é "cuspida" do ponto para fora).
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

from ventude.ui import motion, theme

STRIPS = 36
PAD = 70  # espaço em volta da ilha para o anel e as faíscas
DURATION_MS = 720


def strip_progress(t: float, u: float) -> float:
    """Quanto a tira na posição |u| (0 = meio, 1 = ponta) já foi sugada no instante t (0..1).

    As pontas começam antes e terminam junto com o meio: dá o efeito de "bordas puxadas para dentro".
    """
    u = min(1.0, abs(u))
    delay = (1.0 - u) * 0.38
    local = (t - delay) / max(0.05, 1.0 - delay)
    local = max(0.0, min(1.0, local))
    return local * local * (3 - 2 * local)  # smoothstep


class BlackHole(QWidget):
    """Janela transparente por cima de onde a ilha estava, só enquanto a animação roda."""

    def __init__(self, pixmap: QPixmap, island_rect: QRect, *, inward: bool, settings: object = None) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Ventude: buraco negro")
        self.pixmap = pixmap
        self.inward = inward
        self.island_size = island_rect.size()
        self.setGeometry(island_rect.adjusted(-PAD, -PAD, PAD, PAD))
        self.t = 0.0
        self.on_done: Callable[[], None] | None = None
        self._tween = motion.Tween(self)
        self._tween.progressed.connect(self._step)
        self._tween.done.connect(self._finish)
        reduced = motion.reduced_motion(settings)
        self.duration = 0 if reduced else DURATION_MS

    def play(self, on_done: Callable[[], None] | None = None) -> None:
        self.on_done = on_done
        self.show()
        self.raise_()
        if self.duration <= 0:
            self._finish()
            return
        self._tween.play(self.duration, 0.0, 1.0, motion.linear)

    def _step(self, value: float) -> None:
        self.t = value
        self.update()

    def _finish(self) -> None:
        callback, self.on_done = self.on_done, None
        self.hide()
        self.close()
        if callback is not None:
            callback()

    # ---------- desenho ----------
    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # sumindo: t vai 0→1; voltando: o mesmo filme de trás para frente
        t = self.t if self.inward else 1.0 - self.t
        cx, cy = self.width() / 2.0, self.height() / 2.0
        w, h = float(self.island_size.width()), float(self.island_size.height())

        self._paint_hole(p, cx, cy, h, t)
        self._paint_strips(p, cx, cy, w, h, t)
        self._paint_sparks(p, cx, cy, w, t)
        self._paint_flash(p, cx, cy, t)

    def _paint_strips(self, p: QPainter, cx: float, cy: float, w: float, h: float, t: float) -> None:
        if self.pixmap.isNull() or t >= 0.995:
            return
        ratio = self.pixmap.devicePixelRatio() or 1.0
        src_w = self.pixmap.width() / ratio
        src_h = self.pixmap.height() / ratio
        strip_w = w / STRIPS
        p.save()
        p.translate(cx, cy)
        p.rotate(-38.0 * t * t)  # um giro, como se entrasse num redemoinho
        p.setOpacity(max(0.0, 1.0 - t**3))
        for i in range(STRIPS):
            u = ((i + 0.5) / STRIPS) * 2 - 1  # -1 .. 1
            k = strip_progress(t, u)
            x0 = -w / 2 + i * strip_w
            mid = x0 + strip_w / 2
            new_mid = mid * (1 - k)
            scale_x = max(0.02, 1 - k * 0.9)
            scale_y = max(0.02, 1 - k * (0.55 + 0.45 * abs(u)))
            bend = math.sin(k * math.pi) * (h * 0.18) * (u * u)  # as pontas "escorrem" ao entrar
            dst = QRectF(
                new_mid - strip_w * scale_x / 2,
                -h * scale_y / 2 + bend * (1 if u > 0 else -1) * 0.4,
                strip_w * scale_x + 0.6,
                h * scale_y,
            )
            src = QRectF(i * src_w / STRIPS, 0, src_w / STRIPS, src_h)
            p.drawPixmap(dst, self.pixmap, QRectF(src.x() * ratio, 0, src.width() * ratio, src_h * ratio))
        p.restore()

    def _paint_hole(self, p: QPainter, cx: float, cy: float, h: float, t: float) -> None:
        # o disco cresce até ~70% e encolhe a nada no fim
        grow = math.sin(min(1.0, t) * math.pi) ** 0.8
        radius = h * 0.62 * grow
        if radius < 0.5:
            return
        glow = QRadialGradient(QPointF(cx, cy), radius * 2.6)
        accent = theme.color(theme.ACCENT)
        glow.setColorAt(0.0, QColor(0, 0, 0, 0))
        glow.setColorAt(0.36, QColor(accent.red(), accent.green(), accent.blue(), round(120 * grow)))
        glow.setColorAt(0.5, QColor(140, 120, 255, round(70 * grow)))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), radius * 2.6, radius * 2.6)
        core = QRadialGradient(QPointF(cx, cy), radius)
        core.setColorAt(0.0, QColor(0, 0, 0, 255))
        core.setColorAt(0.82, QColor(0, 0, 0, 250))
        core.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(core)
        p.drawEllipse(QPointF(cx, cy), radius, radius)

    def _paint_sparks(self, p: QPainter, cx: float, cy: float, w: float, t: float) -> None:
        if t <= 0.02 or t >= 0.98:
            return
        accent = theme.color(theme.ACCENT)
        p.setPen(Qt.PenStyle.NoPen)
        for n in range(18):
            phase = (n * 0.618) % 1.0
            life = (t * 1.4 + phase) % 1.0  # cada faísca nasce longe e espirala para o centro
            dist = (1 - life) * (w * 0.55 + PAD * 0.5)
            angle = n * 2.4 + life * 5.5
            x = cx + math.cos(angle) * dist
            y = cy + math.sin(angle) * dist * 0.42
            alpha = round(200 * life * (1 - life) * 4 * min(1.0, t * 3) * (1 - t))
            p.setBrush(QColor(accent.red(), accent.green(), accent.blue(), max(0, min(255, alpha))))
            size = 1.2 + 1.8 * (1 - life)
            p.drawEllipse(QPointF(x, y), size, size)

    def _paint_flash(self, p: QPainter, cx: float, cy: float, t: float) -> None:
        if t < 0.86:
            return
        k = (t - 0.86) / 0.14  # 0..1 no finalzinho
        alpha = round(255 * math.sin(k * math.pi))
        radius = 3 + 10 * math.sin(k * math.pi)
        flash = QRadialGradient(QPointF(cx, cy), radius * 2)
        flash.setColorAt(0.0, QColor(255, 255, 255, alpha))
        flash.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(flash)
        p.drawEllipse(QPointF(cx, cy), radius * 2, radius * 2)


__all__ = ["BlackHole", "strip_progress"]
