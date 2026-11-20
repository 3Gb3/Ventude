"""QR Code dentro da ilha: um cartão por cima do painel para escanear com a câmera do celular."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import QFrame, QPushButton, QSizePolicy, QWidget

from ventude.services import qr
from ventude.ui import icons, theme
from ventude.ui.widgets import ElidedLabel, IconButton, hbox, label, vbox

QUIET = 4  # margem clara mínima da norma, em módulos


class QrView(QWidget):
    """Desenha a matriz com fundo branco (os leitores precisam de contraste e da margem clara)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.matrix: list[list[bool]] = []
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_text(self, text: str) -> None:
        self.matrix = qr.encode(text)
        self.update()

    def paintEvent(self, _event: object) -> None:
        if not self.matrix:
            return
        p = QPainter(self)
        side = min(self.width(), self.height())
        count = len(self.matrix) + QUIET * 2
        cell = max(1, side // count)
        full = cell * count
        left, top = (self.width() - full) // 2, (self.height() - full) // 2
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRectF(left, top, full, full), 12, 12)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)  # módulos com bordas nítidas
        p.setBrush(QColor("#000000"))
        for y, row in enumerate(self.matrix):
            for x, dark in enumerate(row):
                if dark:
                    p.drawRect(left + (x + QUIET) * cell, top + (y + QUIET) * cell, cell, cell)


class QrCard(QFrame):
    """Cobre o painel com um fundo escuro e mostra o QR no centro. Esc ou clique fora fecha."""

    closed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("QrBackdrop")
        self.setStyleSheet("QFrame#QrBackdrop { background: rgba(8, 9, 12, 0.82); border-radius: 20px; }")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.text = ""
        self.card = QFrame(self)
        self.card.setObjectName("QrCard")
        self.card.setStyleSheet(
            f"QFrame#QrCard {{ background: {theme.SURFACE}; border: 1px solid {theme.STROKE_2};"
            " border-radius: 18px; }"
        )
        self.view = QrView()
        self.title = label("QR Code", "H2")
        self.preview = ElidedLabel("", "Faint")
        self.error = label("", "Error", wrap=True)
        close = IconButton("close", "Fechar (Esc)", 28, 15)
        close.clicked.connect(self.close_card)
        copy = QPushButton(" Copiar texto")
        copy.setIcon(icons.icon("copy", theme.TEXT, 14))
        copy.setCursor(Qt.CursorShape.PointingHandCursor)
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.text))
        hint = label("Aponte a câmera do celular", "Faint")
        self.card.setLayout(
            vbox(
                hbox(icons_label(), self.title, None, close, spacing=8),
                self.view,
                self.error,
                self.preview,
                hbox(hint, None, copy, spacing=8),
                spacing=8,
                margins=16,
            )
        )
        self.hide()

    def show_text(self, text: str) -> None:
        self.text = text
        self.preview.setText(" ".join(text.split())[:300])
        try:
            self.view.set_text(text)
            self.view.show()
            self.error.hide()
        except qr.QrError as exc:
            self.view.hide()
            self.error.setText(str(exc))
            self.error.show()
        parent = self.parentWidget()
        self.setGeometry(parent.rect().adjusted(6, 6, -6, -6))
        self.show()
        self.raise_()
        self.setFocus()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        side = min(380, self.height() - 24)
        width = min(self.width() - 40, max(side, 320))
        self.card.setGeometry((self.width() - width) // 2, (self.height() - side) // 2, width, side)

    def close_card(self) -> None:
        if self.isVisible():
            self.hide()
            self.closed.emit()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self.card.geometry().contains(event.position().toPoint()):
            self.close_card()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_card()
            return
        super().keyPressEvent(event)


def icons_label() -> QWidget:
    glyph = label()
    glyph.setPixmap(icons.pixmap("qr", theme.ACCENT, 18))
    return glyph
