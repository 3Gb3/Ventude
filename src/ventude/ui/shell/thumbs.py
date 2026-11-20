"""Prévias ao vivo das janelas ao parar o mouse num app da barra (as mesmas miniaturas do Windows, via DWM).

Cada cartão mostra o ícone, o título e a janela ao vivo. Clique foca a janela, o X (ou o clique do meio)
fecha. Se o Windows não der a prévia, o cartão mostra o ícone grande do app.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QWidget

from ventude.services import dwm, taskbar
from ventude.services.taskbar import WindowInfo
from ventude.ui import icons, theme
from ventude.ui.shell.common import HOVER, ShellWindow, app_pixmap, draw_pixmap, shell_font

CARD_W, CARD_H = 232, 168
THUMB_H = 128
PAD = 8
MAX_CARDS = 6


class ThumbnailPopup(ShellWindow):
    def __init__(self, ctx: object, shell: object) -> None:
        super().__init__(ctx, focus=False)
        self.shell = shell
        self.setWindowTitle("Ventude: prévias")
        self.setMouseTracking(True)
        self.item: object | None = None
        self.windows: list[WindowInfo] = []
        self.handles: list[int] = []
        self.hover = -1
        self.hide_timer = QTimer(self, singleShot=True, interval=280)
        self.hide_timer.timeout.connect(self._maybe_hide)
        self.anchor: QWidget | None = None
        self.preview_images: dict[int, QImage] = {}  # prévias geradas (scripts/preview.py), sem DWM

    def card_rect(self, index: int) -> QRectF:
        return QRectF(PAD + index * (CARD_W + PAD), PAD, CARD_W, CARD_H)

    def thumb_rect(self, index: int) -> QRectF:
        card = self.card_rect(index)
        return QRectF(card.left() + 8, card.top() + 32, card.width() - 16, THUMB_H)

    def close_rect(self, index: int) -> QRectF:
        card = self.card_rect(index)
        return QRectF(card.right() - 30, card.top() + 4, 26, 24)

    def show_for(self, button: QWidget, taskbar_top: int, screen_rect: QRect) -> None:
        item = button.item  # type: ignore[attr-defined]
        self.anchor = button
        self.item = item
        self.windows = list(item.windows[:MAX_CARDS])
        if not self.windows:
            self.hide_now()
            return
        width = PAD + len(self.windows) * (CARD_W + PAD)
        height = CARD_H + PAD * 2
        center = button.mapToGlobal(QPoint(button.width() // 2, 0)).x()
        x = max(screen_rect.left() + 8, min(center - width // 2, screen_rect.right() - width - 7))
        rect = QRect(x, taskbar_top - height - 8, width, height)
        self._unregister()
        if self.isVisible():
            self.setGeometry(rect)
        else:
            self.open_at(rect, QPoint(0, 10))
        QTimer.singleShot(0, self._register)
        self.update()

    def update_item(self, item: object) -> None:
        if (
            self.isVisible()
            and self.item is not None
            and getattr(item, "key", None) == getattr(self.item, "key", None)
        ):
            windows = list(item.windows[:MAX_CARDS])  # type: ignore[attr-defined]
            if [w.hwnd for w in windows] != [w.hwnd for w in self.windows]:
                if not windows:
                    self.hide_now()
                    return
                if self.anchor is not None:
                    self.windows = windows
                    self.show_for(self.anchor, self.y() + self.height() + 8, self.screen().geometry())
            else:
                self.windows = windows
                self.update()

    # ---------- DWM ----------
    def _register(self) -> None:
        self._unregister()
        if not self.isVisible():
            return
        ratio = self.devicePixelRatioF()
        destination = int(self.winId())
        for index, window in enumerate(self.windows):
            handle = dwm.register_thumbnail(destination, window.hwnd)
            self.handles.append(handle)
            if not handle:
                continue
            box = self.thumb_rect(index)
            w, h = dwm.fit(dwm.thumbnail_size(handle), (int(box.width() * ratio), int(box.height() * ratio)))
            left = int(box.left() * ratio + (box.width() * ratio - w) / 2)
            top = int(box.top() * ratio + (box.height() * ratio - h) / 2)
            dwm.place_thumbnail(handle, (left, top, left + w, top + h))
        self.update()

    def _unregister(self) -> None:
        for handle in self.handles:
            dwm.unregister_thumbnail(handle)
        self.handles = []

    def has_live(self, index: int) -> bool:
        return 0 <= index < len(self.handles) and bool(self.handles[index])

    # ---------- desenho ----------
    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        self.paint_background(p)
        item = self.item
        for index, window in enumerate(self.windows):
            card = self.card_rect(index)
            if index == self.hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(HOVER)
                p.drawRoundedRect(card, 8, 8)
            if item is not None:
                pixmap = app_pixmap(self.ctx, item.icon_kind, item.icon_target, 16, "window")  # type: ignore[attr-defined]
                draw_pixmap(p, pixmap, QRectF(card.left() + 10, card.top() + 8, 16, 16))
            p.setPen(QColor(theme.TEXT))
            font = shell_font(12, QFont.Weight.DemiBold if window.foreground else QFont.Weight.Normal)
            p.setFont(font)
            title = p.fontMetrics().elidedText(
                window.title, Qt.TextElideMode.ElideRight, int(card.width() - 72)
            )
            p.drawText(
                QRectF(card.left() + 34, card.top() + 6, card.width() - 70, 20),
                Qt.AlignmentFlag.AlignVCenter,
                title,
            )
            if index == self.hover:
                close = self.close_rect(index)
                inside = close.contains(self.mapFromGlobal(QCursor.pos()).toPointF())
                if inside:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(theme.DANGER))
                    p.drawRoundedRect(close, 5, 5)
                draw_pixmap(p, icons.pixmap("close", theme.TEXT, 14), close.adjusted(6, 5, -6, -5))
            if not self.has_live(index):
                box = self.thumb_rect(index)
                image = self.preview_images.get(window.hwnd)
                if image is not None:
                    scaled = image.scaled(
                        int(box.width()),
                        int(box.height()),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    p.drawImage(
                        QPoint(
                            int(box.center().x() - scaled.width() / 2),
                            int(box.center().y() - scaled.height() / 2),
                        ),
                        scaled,
                    )
                    continue
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 10))
                p.drawRoundedRect(box, 6, 6)
                if item is not None:
                    big = app_pixmap(self.ctx, item.icon_kind, item.icon_target, 48, "window")  # type: ignore[attr-defined]
                    draw_pixmap(p, big, QRectF(box.center().x() - 24, box.center().y() - 24, 48, 48))

    # ---------- mouse ----------
    def index_at(self, pos: QPoint) -> int:
        for index in range(len(self.windows)):
            if self.card_rect(index).contains(pos.toPointF()):
                return index
        return -1

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        index = self.index_at(event.position().toPoint())
        if index != self.hover:
            self.hover = index
        self.update()

    def enterEvent(self, _event: object) -> None:
        self.hide_timer.stop()

    def leaveEvent(self, _event: object) -> None:
        self.hover = -1
        self.update()
        self.hide_timer.start()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        index = self.index_at(event.position().toPoint())
        if index < 0:
            return
        window = self.windows[index]
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton and self.close_rect(index).contains(event.position())
        ):
            taskbar.close(window.hwnd)
            self.windows.pop(index)
            if not self.windows:
                self.hide_now()
            elif self.anchor is not None:
                self.show_for(self.anchor, self.y() + self.height() + 8, self.screen().geometry())
            QTimer.singleShot(350, self.shell.refresh_windows)  # type: ignore[attr-defined]
            return
        if event.button() == Qt.MouseButton.LeftButton:
            taskbar.activate(window.hwnd)
            self.hide_now()
            QTimer.singleShot(150, self.shell.refresh_windows)  # type: ignore[attr-defined]

    def schedule_hide(self) -> None:
        self.hide_timer.start()

    def _maybe_hide(self) -> None:
        if self.geometry().contains(QCursor.pos()):
            return
        if self.anchor is not None and self.anchor.isVisible():
            local = self.anchor.mapFromGlobal(QCursor.pos())
            if self.anchor.rect().contains(local):
                return
        self.hide_now()

    def hide_now(self) -> None:
        self.hide_timer.stop()
        self._unregister()
        self.hide()
        self.item = None
        self.anchor = None
        self.windows = []

    def hideEvent(self, event: object) -> None:
        self._unregister()
        super().hideEvent(event)
