"""Ferramentas de tela: conta-gotas (cor com lupa) e seleção de região para OCR.

A tela é congelada numa captura (uma janela por monitor); a escolha acontece por cima dela.
Esc ou o botão direito cancelam.
"""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from ventude.ui import theme

ZOOM_CELLS = 11  # pixels mostrados na lupa (ímpar: o do meio é o escolhido)
ZOOM_SCALE = 11
MAX_OCR_SIDE = 2400


def color_hex(color: QColor) -> str:
    return f"#{color.red():02X}{color.green():02X}{color.blue():02X}"


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def prepare_for_ocr(image: QImage) -> QImage:
    """Regiões pequenas ficam maiores (o OCR erra menos); as enormes, menores (limite do Windows)."""
    longest = max(image.width(), image.height())
    if longest and longest < 900:
        image = image.scaled(
            image.width() * 2,
            image.height() * 2,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    elif longest > MAX_OCR_SIDE:
        image = image.scaled(
            MAX_OCR_SIDE,
            MAX_OCR_SIDE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return image


class ScreenOverlay(QWidget):
    """Captura congelada de um monitor, com a lupa (cor) ou o retângulo (OCR) por cima."""

    picked = Signal(object)  # QColor (cor) ou QImage (OCR)
    cancelled = Signal()

    def __init__(self, screen, shot: QPixmap, mode: str) -> None:  # noqa: ANN001
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool,
        )
        self.mode = mode
        self.shot = shot
        self.image = shot.toImage()
        self.ratio = shot.devicePixelRatio() or 1.0
        self.cursor_pos = QPoint(-100, -100)
        self.start: QPoint | None = None
        self.end: QPoint | None = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setGeometry(screen.geometry())

    # ---------- pixels ----------
    def pixel(self, pos: QPoint) -> QColor:
        x = min(max(0, int(pos.x() * self.ratio)), self.image.width() - 1)
        y = min(max(0, int(pos.y() * self.ratio)), self.image.height() - 1)
        return self.image.pixelColor(x, y)

    def selection(self) -> QRect:
        if self.start is None or self.end is None:
            return QRect()
        return QRect(self.start, self.end).normalized()

    def crop(self) -> QImage:
        rect = self.selection()
        r = self.ratio
        return self.image.copy(
            QRect(int(rect.x() * r), int(rect.y() * r), int(rect.width() * r), int(rect.height() * r))
        )

    # ---------- eventos ----------
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.cursor_pos = event.position().toPoint()
        if self.start is not None:
            self.end = self.cursor_pos
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.mode == "color":
            self.picked.emit(self.pixel(event.position().toPoint()))
        else:
            self.start = self.end = event.position().toPoint()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.mode != "ocr" or self.start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self.end = event.position().toPoint()
        if self.selection().width() < 6 or self.selection().height() < 6:
            self.start = self.end = None
            self.update()
            return
        self.picked.emit(self.crop())

    # ---------- desenho ----------
    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.shot)
        if self.mode == "ocr":
            self._paint_selection(p)
        else:
            self._paint_loupe(p)

    def _hint(self, p: QPainter, text: str) -> None:
        p.setFont(theme.font(13, theme.SEMI))
        width = p.fontMetrics().horizontalAdvance(text) + 32
        box = QRectF((self.width() - width) / 2, 24, width, 36)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color(theme.BG, 0.92))
        p.drawRoundedRect(box, 18, 18)
        p.setPen(theme.color(theme.TEXT))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_selection(self, p: QPainter) -> None:
        rect = self.selection()
        shade = theme.color("#000000", 0.45)
        if rect.isNull():
            p.fillRect(self.rect(), shade)
        else:
            for part in (
                QRect(0, 0, self.width(), rect.top()),
                QRect(0, rect.bottom() + 1, self.width(), self.height() - rect.bottom() - 1),
                QRect(0, rect.top(), rect.left(), rect.height()),
                QRect(rect.right() + 1, rect.top(), self.width() - rect.right() - 1, rect.height()),
            ):
                p.fillRect(part, shade)
            p.setPen(QPen(theme.color(theme.ACCENT), 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect)
        self._hint(p, "Arraste sobre o texto para copiar · Esc cancela")

    def _paint_loupe(self, p: QPainter) -> None:
        pos = self.cursor_pos
        color = self.pixel(pos)
        size = ZOOM_CELLS * ZOOM_SCALE
        x = pos.x() + 24 if pos.x() + 24 + size < self.width() else pos.x() - 24 - size
        y = pos.y() + 24 if pos.y() + 24 + size + 34 < self.height() else pos.y() - 24 - size - 34
        box = QRect(x, y, size, size)
        half = ZOOM_CELLS // 2
        r = self.ratio
        source = QRect(int(pos.x() * r) - half, int(pos.y() * r) - half, ZOOM_CELLS, ZOOM_CELLS)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(box, self.image.copy(source))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(theme.color(theme.TEXT), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(box), 8, 8)
        center = QRectF(x + half * ZOOM_SCALE, y + half * ZOOM_SCALE, ZOOM_SCALE, ZOOM_SCALE)
        p.setPen(QPen(theme.color("#000000"), 1))
        p.drawRect(center.adjusted(-1, -1, 1, 1))
        p.setPen(QPen(theme.color("#FFFFFF"), 1))
        p.drawRect(center)
        label = QRectF(x, y + size + 6, size, 28)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color(theme.BG, 0.94))
        p.drawRoundedRect(label, 14, 14)
        p.setBrush(color)
        p.drawEllipse(QPointF(label.left() + 16, label.center().y()), 7, 7)
        p.setPen(theme.color(theme.TEXT))
        p.setFont(theme.font(12, theme.SEMI))
        p.drawText(label.adjusted(28, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, color_hex(color))
        self._hint(p, "Clique para copiar a cor · Esc cancela")


class CaptureSession(QObject):
    """Abre uma sobreposição por monitor e devolve o resultado do primeiro clique."""

    finished = Signal(str, object)  # modo, QColor | QImage | None (cancelado)

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode
        self.overlays: list[ScreenOverlay] = []

    def start(self) -> bool:
        screens = QGuiApplication.screens()
        if not screens:
            return False
        for screen in screens:
            shot = screen.grabWindow(0)
            if shot.isNull():
                continue
            overlay = ScreenOverlay(screen, shot, self.mode)
            overlay.picked.connect(lambda value: self._finish(value))
            overlay.cancelled.connect(lambda: self._finish(None))
            self.overlays.append(overlay)
        if not self.overlays:
            return False
        under = QGuiApplication.screenAt(QCursor.pos())
        for overlay in self.overlays:
            overlay.show()
            overlay.raise_()
            if under is not None and overlay.geometry() == under.geometry():
                overlay.activateWindow()
                overlay.setFocus()
        return True

    def _finish(self, value: object) -> None:
        overlays, self.overlays = self.overlays, []
        for overlay in overlays:
            overlay.hide()
            overlay.deleteLater()
        if overlays:
            self.finished.emit(self.mode, value)
