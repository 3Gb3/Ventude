"""Prévias ao vivo das janelas ao parar o mouse num app da Doca (DWM Thumbnail).

A janela das prévias é opaca de propósito: o DWM não desenha miniaturas ao vivo dentro de janelas
translúcidas por pixel (WS_EX_LAYERED com UpdateLayeredWindow), que é o que o Qt usa com
WA_TranslucentBackground. Os cantos arredondados e a borda vêm do próprio Windows 11.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QImage, QMouseEvent, QPainter

from ventude.services import dwm, taskbar
from ventude.services.taskbar import WindowInfo
from ventude.ui import icons, motion, theme
from ventude.ui.shell.common import ShellWindow, app_pixmap, draw_pixmap, shell_font
from ventude.ui.shell.items import TaskItem

CARD_W, CARD_H = 248, 180
THUMB_H = 138
PAD = 8
SHADOW_PAD = 0  # opaca: a sombra e os cantos são do Windows
MAX_CARDS = 6


class ThumbnailPopup(ShellWindow):
    """Cartões ao vivo em INK_RAISED; registra os thumbnails DWM após o Desdobrar."""

    def __init__(self, ctx: object, shell: object) -> None:
        super().__init__(ctx, radius=theme.R_CARD, focus=False, bg=theme.INK_RAISED)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.shell = shell
        self.setWindowTitle("Ventude: prévias")
        self.setMouseTracking(True)
        self.item: TaskItem | None = None
        self.windows: list[WindowInfo] = []
        self.handles: list[tuple[int, tuple[int, int, int, int]]] = []
        self.hover = -1
        self.hide_timer = QTimer(self, singleShot=True, interval=280)
        self.hide_timer.timeout.connect(self._maybe_hide)
        self.anchor_rect = QRect()
        self._dock_top = 0
        self._screen_rect = QRect()
        self.preview_images: dict[int, QImage] = {}  # prévias geradas (scripts/preview.py), sem DWM
        self._dwm_fade = motion.Tween(self)
        self._dwm_fade.progressed.connect(self._on_dwm_fade)
        self.shown.connect(self._register)

    def apply_ink(self) -> None:
        hwnd = int(self.winId())
        dwm.disable_glass(hwnd)
        dwm.dark_frame(hwnd)
        dwm.round_corners(hwnd)
        dwm.border_color(hwnd, 0x2A2E36)

    def open_at(self, rect: QRect, offset: QPoint = QPoint(0, 10)) -> None:
        """Aparece no lugar (sem opacidade de janela, que quebraria o DWM); as prévias entram com fade."""
        self._closing = False
        self._tween.stop()
        self.setGeometry(rect)
        self._target_pos = rect.topLeft()
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        self.shown.emit()

    def _panel_rect(self) -> QRectF:
        return QRectF(
            SHADOW_PAD,
            SHADOW_PAD,
            max(1, self.width() - SHADOW_PAD * 2),
            max(1, self.height() - SHADOW_PAD * 2),
        )

    def card_rect(self, index: int) -> QRectF:
        return QRectF(SHADOW_PAD + PAD + index * (CARD_W + PAD), SHADOW_PAD + PAD, CARD_W, CARD_H)

    def thumb_rect(self, index: int) -> QRectF:
        card = self.card_rect(index)
        return QRectF(card.left() + 8, card.top() + 32, card.width() - 16, THUMB_H)

    def close_rect(self, index: int) -> QRectF:
        card = self.card_rect(index)
        return QRectF(card.right() - 30, card.top() + 4, 26, 24)

    def show_for(self, item: TaskItem, anchor_rect: QRect, dock_top: int, screen_rect: QRect) -> None:
        self.anchor_rect = anchor_rect
        self._dock_top = dock_top
        self._screen_rect = screen_rect
        self.item = item
        self.windows = list(item.windows[:MAX_CARDS])
        if not self.windows:
            self.hide_now()
            return
        panel_w = PAD + len(self.windows) * (CARD_W + PAD)
        panel_h = CARD_H + PAD * 2
        center = anchor_rect.center().x()
        px = max(screen_rect.left() + 8, min(center - panel_w // 2, screen_rect.right() - panel_w - 7))
        py = dock_top - panel_h - 14
        rect = QRect(px - SHADOW_PAD, py - SHADOW_PAD, panel_w + SHADOW_PAD * 2, panel_h + SHADOW_PAD * 2)
        self._unregister()
        if self.isVisible() and not self._closing:
            self.setGeometry(rect)
            QTimer.singleShot(0, self._register)
        else:
            self.open_at(rect, QPoint(0, 10))
        self.update()

    def update_item(self, item: TaskItem) -> None:
        if self.isVisible() and self.item is not None and item.key == self.item.key:
            windows = list(item.windows[:MAX_CARDS])
            if [w.hwnd for w in windows] != [w.hwnd for w in self.windows]:
                if not windows:
                    self.hide_now()
                    return
                if not self.anchor_rect.isNull():
                    self.item = item
                    self.windows = windows
                    self.show_for(item, self.anchor_rect, self._dock_top, self._screen_rect)
            else:
                self.item = item
                self.windows = windows
                self.update()

    # ---------- DWM ----------
    def _register(self) -> None:
        self._unregister()
        if not self.isVisible():
            return
        ratio = self.devicePixelRatioF()
        destination = int(self.winId())
        fade_ms = motion.ms("desdobrar_fade", getattr(self.ctx, "settings", None))
        initial_opacity = 0 if fade_ms > 0 else 255
        for index, window in enumerate(self.windows):
            handle = dwm.register_thumbnail(destination, window.hwnd)
            if not handle:
                self.handles.append((0, (0, 0, 0, 0)))
                continue
            box = self.thumb_rect(index)
            w, h = dwm.fit(
                dwm.thumbnail_size(handle),
                (int(box.width() * ratio), int(box.height() * ratio)),
            )
            left = int(box.left() * ratio + (box.width() * ratio - w) / 2)
            top = int(box.top() * ratio + (box.height() * ratio - h) / 2)
            dst = (left, top, left + w, top + h)
            dwm.place_thumbnail(handle, dst, opacity=initial_opacity)
            self.handles.append((handle, dst))
        if fade_ms > 0 and any(h for h, _ in self.handles):
            self._dwm_fade.play(fade_ms, 0.0, 1.0, motion.out_cubic)
        self.update()

    def _on_dwm_fade(self, value: float) -> None:
        alpha = max(0, min(255, round(255 * value)))
        for handle, dst in self.handles:
            if handle:
                dwm.place_thumbnail(handle, dst, opacity=alpha)

    def _unregister(self) -> None:
        self._dwm_fade.stop()
        for handle, _ in self.handles:
            if handle:
                dwm.unregister_thumbnail(handle)
        self.handles = []

    def has_live(self, index: int) -> bool:
        return 0 <= index < len(self.handles) and bool(self.handles[index][0])

    # ---------- desenho ----------
    def paint_background(self, p: QPainter) -> None:
        p.fillRect(self.rect(), QColor(self.bg))
        grad_h = min(self.height(), 120)
        for y in range(0, grad_h, 4):  # brilho de topo do material Tinta, sem transparência
            p.fillRect(0, y, self.width(), 4, QColor(255, 255, 255, round(8 * (1 - y / grad_h))))

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_background(p)
        item = self.item
        for index, window in enumerate(self.windows):
            card = self.card_rect(index)
            if index == self.hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.HALO))
                p.drawRoundedRect(card, 12, 12)
            if item is not None:
                pixmap = app_pixmap(self.ctx, item.icon_kind, item.icon_target, 16, "window")
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
                    p.drawRoundedRect(close, theme.R_ITEM, theme.R_ITEM)
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
                p.drawRoundedRect(box, theme.R_ITEM, theme.R_ITEM)
                if item is not None:
                    big = app_pixmap(self.ctx, item.icon_kind, item.icon_target, 48, "window")
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
            elif self.item is not None and not self.anchor_rect.isNull():
                self.show_for(self.item, self.anchor_rect, self._dock_top, self._screen_rect)
            if hasattr(self.shell, "refresh_windows"):
                QTimer.singleShot(350, self.shell.refresh_windows)  # type: ignore[attr-defined]
            return
        if event.button() == Qt.MouseButton.LeftButton:
            taskbar.activate(window.hwnd)
            self.hide_now()
            if hasattr(self.shell, "refresh_windows"):
                QTimer.singleShot(150, self.shell.refresh_windows)  # type: ignore[attr-defined]

    def schedule_hide(self) -> None:
        self.hide_timer.start()

    def _maybe_hide(self) -> None:
        pos = QCursor.pos()
        if self.geometry().contains(pos) or self.anchor_rect.contains(pos):
            return
        self.hide_now()

    def hide_now(self) -> None:
        self.hide_timer.stop()
        self._unregister()
        self._tween.stop()
        self.hide()
        self.item = None
        self.anchor_rect = QRect()
        self.windows = []

    def hideEvent(self, event: object) -> None:
        self._unregister()
        super().hideEvent(event)
