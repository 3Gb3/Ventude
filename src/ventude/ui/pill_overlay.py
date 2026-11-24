"""Sobreposição na pílula: barra de volume desenhada no lugar, sem abrir aviso."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ventude.ui import brand, icons, motion, theme


class VolumeOverlay(QWidget):
    """Barra de 140 px que aparece por cima do lado direito da pílula ao usar a roda."""

    WIDTH = 140

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.level = 0.0
        self.muted = False
        self._alpha = 0.0
        self._settings: object | None = None
        self._hiding = False
        self._tween = motion.Tween(self)
        self._tween.progressed.connect(self._on_anim)
        self._tween.done.connect(self._on_done)
        self._hold = QTimer(self, singleShot=True, interval=1200)
        self._hold.timeout.connect(self._fade_out)
        self.hide()

    @property
    def active(self) -> bool:
        return self.isVisible() and not self._hiding

    def place_in(self, parent_rect: QRectF | object) -> None:
        pw = self.parentWidget().width() if self.parentWidget() else self.WIDTH
        ph = self.parentWidget().height() if self.parentWidget() else 38
        w = min(self.WIDTH, max(104, pw - 16))
        h = max(24, ph - 8)
        self.setGeometry(pw - w - 6, (ph - h) // 2, w, h)

    def show_volume(self, level: float, muted: bool, settings: object | None = None) -> None:
        self.level = max(0.0, min(1.0, float(level)))
        self.muted = bool(muted)
        self._settings = settings
        self.place_in(self.rect())
        self._hiding = False
        was_visible = self.isVisible()
        self.show()
        self.raise_()
        if not was_visible or self._alpha < 0.95:
            dur = motion.ms("volume_in", settings)
            self._tween.play(dur, self._alpha, 1.0, motion.named("enfase"))
        else:
            self._alpha = 1.0
            self.update()
        self._hold.start(1200)

    def dismiss_now(self) -> None:
        self._hold.stop()
        self._tween.stop()
        self._hiding = False
        self._alpha = 0.0
        self.hide()

    def _fade_out(self) -> None:
        if not self.isVisible():
            return
        self._hiding = True
        dur = motion.ms("volume_out", self._settings)
        self._tween.play(dur, self._alpha, 0.0, motion.in_cubic)

    def _on_anim(self, value: float) -> None:
        self._alpha = max(0.0, min(1.0, value))
        self.update()

    def _on_done(self) -> None:
        if self._hiding:
            self._hiding = False
            self.hide()

    def paintEvent(self, _event: object) -> None:
        if self._alpha <= 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._alpha)
        rect = QRectF(self.rect())
        brand.paint_ink(p, rect, rect.height() / 2.0, bg=theme.INK_KEY, alpha=0.98, sheen=False)
        glyph = (
            "volume-x"
            if (self.muted or self.level <= 0.001)
            else ("volume-1" if self.level < 0.5 else "volume")
        )
        fg = theme.DANGER if self.muted else theme.ACCENT
        p.drawPixmap(
            int(rect.left() + 8),
            int(rect.center().y() - 7),
            icons.pixmap(glyph, fg if self.muted else theme.TEXT, 14),
        )
        pct_text = "mudo" if self.muted else f"{round(self.level * 100)}%"
        p.setFont(theme.font(11, theme.SEMI, tabular=True))
        pct_w = 34 if self.muted else 30
        track_x = rect.left() + 27
        track_w = max(24.0, rect.width() - 27 - pct_w - 8)
        track = QRectF(track_x, rect.center().y() - 3.0, track_w, 6.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 28))
        p.drawRoundedRect(track, 3.0, 3.0)
        fill_level = 0.0 if self.muted else self.level
        if fill_level > 0.001:
            fw = max(6.0, track.width() * fill_level)
            p.setBrush(theme.color(theme.ACCENT))
            p.drawRoundedRect(QRectF(track.left(), track.top(), fw, 6.0), 3.0, 3.0)
        p.setPen(theme.color(theme.DANGER if self.muted else theme.TEXT))
        p.drawText(
            QRectF(track.right() + 4, rect.top(), pct_w, rect.height()),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            pct_text,
        )
