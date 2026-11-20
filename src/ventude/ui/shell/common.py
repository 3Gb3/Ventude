"""Peças comuns do Ventude Shell: janela no material Tinta, botões com halo, ícones reais e avatar."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import (
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from ventude.services import dwm, shellicons
from ventude.ui import brand, icons, motion, theme

HOVER = theme.HALO
PRESSED = theme.HALO_PRESS
ACTIVE = QColor(255, 255, 255, 24)
BORDER = theme.HAIRLINE


def shell_font(
    size: int,
    weight: QFont.Weight = QFont.Weight.Normal,
    display: bool = False,
    tabular: bool = False,
) -> QFont:
    return theme.font(size, weight, display, tabular=tabular)


def app_pixmap(ctx: object, kind: str, target: str, size: int, fallback: str = "apps") -> QPixmap:
    """Ícone real do Windows (quando já carregado) ou o ícone de traço do Ventude enquanto carrega."""
    name = shellicons.parsing_name(kind, target)
    cache = getattr(ctx, "icons", None)
    if name and cache is not None:
        pixmap = cache.pixmap(name, size)
        if pixmap is not None:
            return pixmap
    glyph = {"url": "globe", "web": "globe", "folder": "folder", "file": "file"}.get(kind, fallback)
    return icons.pixmap(glyph, theme.TEXT_2, size)


def draw_pixmap(p: QPainter, pixmap: QPixmap, rect: QRectF) -> None:
    """Desenha o ícone centralizado e nítido (ícones grandes são reduzidos com suavização)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    ratio = pixmap.devicePixelRatio() or 1.0
    w, h = pixmap.width() / ratio, pixmap.height() / ratio
    scale = min(rect.width() / max(w, 1), rect.height() / max(h, 1))
    tw, th = w * scale, h * scale
    target = QRectF(rect.center().x() - tw / 2, rect.center().y() - th / 2, tw, th)
    p.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
    p.restore()


class ShellWindow(QWidget):
    """Janela sem moldura no material Tinta, com animação suave de abrir e fechar."""

    shown = Signal()
    hidden_ = Signal()

    def __init__(
        self,
        ctx: object,
        *,
        radius: int = theme.R_CARD,
        tool: bool = True,
        focus: bool = True,
        bg: str = theme.INK_RAISED,
        alpha: float = 0.98,
    ) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        flags |= Qt.WindowType.Tool if tool else Qt.WindowType.Window
        if not focus:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        super().__init__(None, flags)
        self.ctx = ctx
        self.radius = radius
        self.bg = bg
        self.alpha = alpha
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if not focus:
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._tween = motion.Tween(self)
        self._tween.progressed.connect(self._on_anim)
        self._tween.done.connect(self._on_done)
        self._offset = QPoint()
        self._target_pos = QPoint()
        self._closing = False

    def apply_ink(self) -> None:
        hwnd = int(self.winId())
        dwm.disable_glass(hwnd)
        dwm.square_corners(hwnd)
        dwm.border_color(hwnd, 0xFFFFFFFE)
        self.update()

    def paint_background(self, p: QPainter) -> None:
        brand.paint_ink(p, QRectF(self.rect()), self.radius, bg=self.bg, alpha=self.alpha)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        self.paint_background(p)

    def showEvent(self, event: object) -> None:
        super().showEvent(event)
        self.apply_ink()

    # ---------- abrir e fechar com animação ----------
    def animation_ms(self) -> int:
        settings = getattr(self.ctx, "settings", None)
        return motion.ms("desdobrar", settings)

    def open_at(self, rect: QRect, offset: QPoint = QPoint(0, 18)) -> None:
        """Mostra deslizando de `offset` até `rect`, com fade."""
        self._closing = False
        self._tween.stop()
        duration = self.animation_ms()
        self.setGeometry(rect)
        self._target_pos = rect.topLeft()
        self._offset = offset
        if duration <= 0:
            self.setWindowOpacity(1.0)
            self.show()
            self.raise_()
            self.shown.emit()
            return
        self.setWindowOpacity(0.0)
        self.move(rect.topLeft() + offset)
        self.show()
        self.raise_()
        self._tween.play(duration, 0.0, 1.0, motion.out_cubic)

    def close_animated(self, offset: QPoint = QPoint(0, 12)) -> None:
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        self._tween.stop()
        settings = getattr(self.ctx, "settings", None)
        duration = motion.ms("desdobrar_close", settings)
        if duration <= 0:
            self._finish_close()
            return
        self._target_pos = self.pos()
        self._offset = offset
        self._tween.play(duration, 1.0, 0.0, motion.in_cubic)

    def _on_anim(self, value: float) -> None:
        self.setWindowOpacity(max(0.0, min(1.0, value)))
        if self._closing:
            self.move(self._target_pos + self._offset * (1.0 - value))
        else:
            self.move(self._target_pos + self._offset * (1.0 - value))

    def _on_done(self) -> None:
        if self._closing:
            self._finish_close()
        else:
            self.setWindowOpacity(1.0)
            self.move(self._target_pos)
            self.shown.emit()

    def _finish_close(self) -> None:
        self.hide()
        self.setWindowOpacity(1.0)
        self._closing = False
        self.hidden_.emit()

    @property
    def closing(self) -> bool:
        return self._closing


class HoverButton(QWidget):
    """Botão desenhado à mão: halo em cápsula no hover/pressionado, ícone e texto opcional."""

    clicked = Signal()
    right_clicked = Signal(QPoint)
    middle_clicked = Signal()

    def __init__(
        self,
        icon: str = "",
        text: str = "",
        tip: str = "",
        size: QSize = QSize(40, 40),
        icon_size: int = 18,
        radius: int = theme.R_ITEM,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.icon_name, self.text, self.icon_size, self.radius = icon, text, icon_size, radius
        self.icon_color = theme.TEXT
        self.pixmap: QPixmap | None = None
        self.checked = False
        self._pressed = False
        self.setFixedSize(size)
        self.setToolTip(tip)
        if tip or text:
            self.setAccessibleName(text or tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def set_icon(self, name: str, color: str | None = None) -> None:
        self.icon_name = name
        if color:
            self.icon_color = color
        self.update()

    def set_checked(self, on: bool) -> None:
        self.checked = on
        self.update()

    def background(self) -> QColor | None:
        if self._pressed:
            return PRESSED
        if self.underMouse():
            return HOVER
        if self.checked:
            return ACTIVE
        return None

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = self.background()
        if bg is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), self.radius, self.radius)
        pixmap = self.pixmap or (
            icons.pixmap(self.icon_name, self.icon_color, self.icon_size) if self.icon_name else None
        )
        size = self.icon_size * (0.9 if self._pressed else 1.0)
        if self.text:
            x = 12.0
            if pixmap is not None:
                draw_pixmap(p, pixmap, QRectF(x, (self.height() - size) / 2, size, size))
                x += self.icon_size + 10
            p.setPen(QColor(theme.TEXT))
            p.setFont(shell_font(13))
            p.drawText(
                QRectF(x, 0, self.width() - x - 8, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text
            )
        elif pixmap is not None:
            draw_pixmap(
                p,
                pixmap,
                QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size),
            )

    def enterEvent(self, _event: object) -> None:
        self.update()

    def leaveEvent(self, _event: object) -> None:
        self._pressed = False
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        inside = self.rect().contains(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton:
            was = self._pressed
            self._pressed = False
            self.update()
            if inside and was:
                self.clicked.emit()
        elif event.button() == Qt.MouseButton.RightButton and inside:
            self.right_clicked.emit(event.globalPosition().toPoint())
        elif event.button() == Qt.MouseButton.MiddleButton and inside:
            self.middle_clicked.emit()


class Avatar(QLabel):
    """Foto da conta em círculo (ou a inicial do nome)."""

    def __init__(self, name: str, picture: str = "", size: int = 32, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size = size
        self.set_person(name, picture)

    def set_person(self, name: str, picture: str = "") -> None:
        size = self._size
        scale = 3
        canvas = QPixmap(size * scale, size * scale)
        canvas.fill(Qt.GlobalColor.transparent)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addEllipse(QRectF(0, 0, size * scale, size * scale))
        p.setClipPath(path)
        photo = QPixmap(picture) if picture else QPixmap()
        if not photo.isNull():
            photo = photo.scaled(
                size * scale,
                size * scale,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            p.drawPixmap(0, 0, photo)
        else:
            p.fillRect(canvas.rect(), QColor(theme.ACCENT_SOFT))
            p.setPen(QColor(theme.ACCENT))
            font = shell_font(int(size * scale * 0.45), QFont.Weight.DemiBold)
            p.setFont(font)
            p.drawText(canvas.rect(), Qt.AlignmentFlag.AlignCenter, (name[:1] or "?").upper())
        p.end()
        canvas.setDevicePixelRatio(scale)
        self.setPixmap(canvas)


def screen_at_cursor():  # noqa: ANN201
    from PySide6.QtGui import QCursor, QGuiApplication

    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


def later(ms: int, fn: Callable[[], object]) -> None:
    QTimer.singleShot(ms, fn)


def process_events() -> None:
    QApplication.processEvents()
