"""Pergunta se você quer atualizar e mostra o download, no visual da ilha."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ventude import __version__
from ventude.core import updates
from ventude.core.tasks import Tasks
from ventude.ui import theme
from ventude.ui.widgets import hbox, icon_tile, label, vbox

log = logging.getLogger(__name__)


class _Bridge(QObject):
    progress = Signal(int, int)


def _mb(value: int) -> str:
    return f"{value / 1_048_576:.1f}".replace(".", ",")


class UpdateDialog(QDialog):
    def __init__(
        self,
        update: updates.Update,
        tasks: Tasks,
        on_install: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.update_info, self.tasks, self.on_install = update, tasks, on_install
        self._cancel = threading.Event()
        self._busy = False
        self.setWindowTitle("Atualização do Ventude")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(440)
        self.bridge = _Bridge(self)
        self.bridge.progress.connect(self._progress)

        title = label("Nova versão do Ventude", "H2")
        subtitle = label(f"Versão {update.version} · você está na {__version__}", "Muted")
        head = hbox(
            icon_tile("download", theme.ACCENT, 42, 20), vbox(title, subtitle, spacing=1), None, spacing=12
        )

        notes = "\n".join((update.notes or "Melhorias e correções.").splitlines()[:14])
        self.notes = label(notes, "Muted", wrap=True)
        self.notes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box = QWidget()
        box.setLayout(vbox(self.notes, None, spacing=0))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(box)
        self.scroll.setFixedHeight(min(150, max(48, self.notes.sizeHint().height() + 8)))

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.hide()
        self.status = label("", "Faint")
        self.status.hide()

        self.later = QPushButton("Agora não")
        self.later.clicked.connect(self._later)
        self.ok = QPushButton("Atualizar")
        self.ok.setObjectName("Primary")
        self.ok.clicked.connect(self._start)
        for button in (self.later, self.ok):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumWidth(110)
        self.ok.setDefault(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)
        root.addLayout(head)
        root.addWidget(label("Novidades", "Caption"))
        root.addWidget(self.scroll)
        root.addWidget(self.bar)
        root.addWidget(self.status)
        root.addLayout(hbox(None, self.later, self.ok, spacing=8))

    # ---------- aparência ----------
    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 20, 20)
        p.fillPath(path, QColor(theme.BG))
        p.setPen(QPen(QColor(255, 255, 255, 24), 1))
        p.drawPath(path)

    def showEvent(self, event: object) -> None:
        super().showEvent(event)
        screen = self.screen() or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.adjustSize()
        self.move(area.center().x() - self.width() // 2, area.top() + area.height() // 4)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._later()
            return
        super().keyPressEvent(event)

    # ---------- fluxo ----------
    def _later(self) -> None:
        if self._busy:
            self._cancel.set()
            return
        self.reject()

    def _start(self) -> None:
        self._busy = True
        self._cancel.clear()
        self.ok.setEnabled(False)
        self.ok.setText("Baixando…")
        self.later.setText("Cancelar")
        self.bar.setRange(0, 0)
        self.bar.show()
        self.status.setText("Conectando ao GitHub…")
        self.status.show()
        self.adjustSize()
        self.tasks.run(
            updates.download,
            self.update_info,
            self.bridge.progress.emit,
            self._cancel,
            done=self._downloaded,
            fail=self._failed,
            owner=self,
        )

    def _progress(self, done: int, total: int) -> None:
        if total > 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(done / total * 1000))
            self.status.setText(f"Baixando… {_mb(done)} de {_mb(total)} MB")
        else:
            self.status.setText(f"Baixando… {_mb(done)} MB")

    def _failed(self, error: BaseException) -> None:
        self._busy = False
        self.bar.hide()
        self.later.setText("Fechar")
        self.ok.setEnabled(True)
        self.ok.setText("Tentar de novo")
        if isinstance(error, updates.Cancelled):
            self.status.setText("Download cancelado.")
        else:
            log.warning("Falha ao baixar atualização: %s", error)
            self.status.setText(f"Não deu para baixar agora ({error}).")
        self.status.setObjectName("Faint" if isinstance(error, updates.Cancelled) else "Error")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _downloaded(self, path: object) -> None:
        self._busy = False
        self.bar.setRange(0, 1000)
        self.bar.setValue(1000)
        self.status.setText("Instalando… o Ventude fecha e abre de novo sozinho.")
        self.later.setEnabled(False)
        self.ok.setText("Instalando…")
        try:
            updates.launch_installer(path)  # type: ignore[arg-type]
        except OSError as error:
            self._failed(error)
            return
        self.accept()
        if self.on_install:
            self.on_install()


def ask_to_update(update: updates.Update, tasks: Tasks, on_install: Callable[[], None]) -> UpdateDialog:
    dialog = UpdateDialog(update, tasks, on_install)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


__all__ = ["UpdateDialog", "ask_to_update", "QLabel"]
