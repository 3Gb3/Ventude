"""Estante: o que você soltou na ilha (e o que copiou, com o histórico ligado) fica aqui.

Arraste para fora para usar. Itens fixados continuam depois de fechar o Ventude.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileInfo, QMimeData, QPoint, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileIconProvider,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
)

from ventude.services.shelf import ShelfItem
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.pages.base import Page
from ventude.ui.widgets import EmptyState, hbox, label

ITEM_ROLE = Qt.ItemDataRole.UserRole
TILE = QSize(120, 114)
FILTERS = [("all", "Tudo"), ("clip", "Copiados"), ("pinned", "Fixados")]


def item_mime(items: list[ShelfItem]) -> QMimeData:
    """Arquivos viram arquivos de verdade (navegador, e-mail, Discord...); textos, links e cores, texto."""
    mime = QMimeData()
    urls = [QUrl.fromLocalFile(i.value) for i in items if i.is_path]
    urls += [QUrl(i.value) for i in items if i.kind == "link"]
    if urls:
        mime.setUrls(urls)
    texts = [i.value for i in items if i.is_text]
    if texts:
        mime.setText("\n".join(texts))
    return mime


def matches(item: ShelfItem, mode: str) -> bool:
    if mode == "clip":
        return item.source == "clip"
    if mode == "pinned":
        return item.pinned
    return True


def color_swatch(value: str, size: int = 44) -> QIcon:
    pixmap = QPixmap(size * 3, size * 3)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(theme.color(theme.STROKE_2))
    p.setBrush(QColor(value))
    p.drawRoundedRect(QRectF(6, 6, size * 3 - 12, size * 3 - 12), 28, 28)
    p.end()
    pixmap.setDevicePixelRatio(3)
    return QIcon(pixmap)


def with_pin(icon: QIcon, size: int = 44) -> QIcon:
    """Desenha um alfinete no canto do ícone dos itens fixados."""
    base = icon.pixmap(QSize(size, size))
    out = QPixmap(base.size())
    out.setDevicePixelRatio(base.devicePixelRatio())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.drawPixmap(0, 0, base)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(theme.color(theme.BG, 0.9))
    p.drawEllipse(QRectF(size - 17, -1, 18, 18))
    p.drawPixmap(size - 15, 1, icons.pixmap("pin", theme.ACCENT, 14))
    p.end()
    return QIcon(out)


class ShelfGrid(QListWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setGridSize(QSize(TILE.width() + 10, TILE.height() + 10))
        self.setIconSize(QSize(40, 40))
        self.setWordWrap(True)
        self.setUniformItemSizes(True)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setAcceptDrops(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setStyleSheet(
            f"QListWidget::item {{ background: {theme.SURFACE}; border: 1px solid {theme.STROKE};"
            " border-radius: 14px; padding-top: 10px; margin: 0px; }"
            f"QListWidget::item:hover {{ background: {theme.SURFACE_2}; border-color: {theme.STROKE_2}; }}"
            f"QListWidget::item:selected {{ background: {theme.SURFACE_2}; border-color: #3C8C70; }}"
        )

    def mimeData(self, items: list[QListWidgetItem]) -> QMimeData:  # noqa: N802
        return item_mime([i.data(ITEM_ROLE) for i in items])

    def supportedDropActions(self) -> Qt.DropAction:  # noqa: N802
        return Qt.DropAction.CopyAction


class ShelfPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        self.shelf = ctx.shelf
        self.mode = "all"
        row, self.subtitle = self.header("Estante")
        self.filters = QButtonGroup(self)
        self.filters.setExclusive(True)
        self.filter_buttons: dict[str, QPushButton] = {}
        chips = []
        for key, text in FILTERS:
            chip = QPushButton(text)
            chip.setObjectName("Chip")
            chip.setCheckable(True)
            chip.setChecked(key == self.mode)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _c=False, k=key: self.set_mode(k))
            self.filters.addButton(chip)
            self.filter_buttons[key] = chip
            chips.append(chip)
        row.addLayout(hbox(*chips, spacing=6))
        row.addSpacing(8)
        paste = QPushButton(" Colar")
        paste.setIcon(icons.icon("download", theme.TEXT, 15))
        paste.setToolTip("Guardar o que está na área de transferência (texto, imagem ou arquivos)")
        paste.clicked.connect(self.paste)
        clear = QPushButton(" Limpar")
        clear.setIcon(icons.icon("trash", theme.TEXT, 15))
        clear.setToolTip("Tira tudo que não está fixado")
        clear.clicked.connect(self.shelf.clear)
        for button in (paste, clear):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(button)
        self.grid = ShelfGrid()
        self.grid.itemDoubleClicked.connect(self._open)
        self.grid.customContextMenuRequested.connect(self._menu)
        self.empty = EmptyState(
            "inbox",
            "Estante vazia",
            "Arraste arquivos, imagens, links ou textos para a ilha e solte em “Guardar na estante”. "
            "Com o histórico ligado, o que você copia (Ctrl + C) também aparece aqui.",
        )
        self.root.addWidget(self.grid, 1)
        self.root.addWidget(self.empty, 1)
        self.status = label("", "Faint")
        self.root.addWidget(self.status)
        self._icons = QFileIconProvider()
        self.shelf.changed.connect(self.reload)
        self.reload()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.filter_buttons[mode].setChecked(True)
        self.reload()

    def shown_items(self) -> list[ShelfItem]:
        return [i for i in self.shelf.items if matches(i, self.mode)]

    def reload(self) -> None:
        self.grid.clear()
        items = self.shown_items()
        for item in items:
            icon = self._icon(item)
            entry = QListWidgetItem(with_pin(icon) if item.pinned else icon, item.name)
            entry.setSizeHint(TILE)
            entry.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            entry.setData(ITEM_ROLE, item)
            tip = item.value if item.kind != "text" else item.value[:400]
            origin = "Copiado" if item.source == "clip" else "Guardado"
            entry.setToolTip(f"{tip}\n{origin}" + (" · fixado" if item.pinned else ""))
            self.grid.addItem(entry)
        total = len(self.shelf.items)
        self.grid.setVisible(bool(items))
        self.empty.setVisible(not items)
        if self.mode != "all" and not items:
            self.empty.title.setText("Nada copiado ainda" if self.mode == "clip" else "Nada fixado")
        else:
            self.empty.title.setText("Estante vazia")
        history = "o que você copia entra sozinho · " if self.shelf.clipboard_history else ""
        self.subtitle.setText(
            f"{total} ite{'ns' if total != 1 else 'm'} · {history}arraste para fora para usar · "
            "fixe o que quiser guardar"
            if total
            else "Um lugar temporário para o que você está levando de uma janela para outra"
        )

    def _icon(self, item: ShelfItem) -> QIcon:
        if item.kind == "image":
            pixmap = QPixmap(item.value)
            if not pixmap.isNull():
                return QIcon(
                    pixmap.scaled(
                        88, 88, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                    )
                )
        if item.kind == "color":
            return color_swatch(item.value)
        if item.kind == "text":
            return icons.icon("news", theme.TEXT_2, 44)
        if item.kind == "link":
            return icons.icon("globe", theme.ACCENT, 44)
        info = QFileInfo(item.value)
        if info.exists():
            system = self._icons.icon(info)
            if not system.isNull():
                return system
        return icons.icon("folder" if item.kind == "folder" else "file", theme.TEXT_2, 44)

    def paste(self) -> None:
        mime = QGuiApplication.clipboard().mimeData()
        if mime is None:
            return
        if mime.hasUrls() and any(u.isLocalFile() for u in mime.urls()):
            added = self.shelf.add_paths([u.toLocalFile() for u in mime.urls()])
        elif mime.hasImage():
            added = self.shelf.add_image(QGuiApplication.clipboard().image())
        elif mime.hasText():
            added = self.shelf.add_text(mime.text())
        else:
            added = 0
        self.status.setText("Guardado na estante." if added else "Nada novo para guardar.")

    def _open(self, entry: QListWidgetItem) -> None:
        item: ShelfItem = entry.data(ITEM_ROLE)
        if item.kind in ("text", "color"):
            QGuiApplication.clipboard().setText(item.value)
            self.status.setText("Copiado." if item.kind == "text" else f"Cor {item.value} copiada.")
        elif item.kind == "link":
            QDesktopServices.openUrl(QUrl(item.value))
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(item.value))

    def _menu(self, pos: QPoint) -> None:
        entry = self.grid.itemAt(pos)
        if not entry:
            return
        item: ShelfItem = entry.data(ITEM_ROLE)
        menu = QMenu(self)
        menu.addAction(icons.icon("external", theme.TEXT_2, 15), "Abrir", lambda: self._open(entry))
        menu.addAction(icons.icon("copy", theme.TEXT_2, 15), "Copiar", lambda: self._copy(item))
        if item.is_text:
            menu.addAction(icons.icon("qr", theme.TEXT_2, 15), "Mostrar QR Code", lambda: self.show_qr(item))
        if item.is_path:
            menu.addAction(
                icons.icon("folder", theme.TEXT_2, 15),
                "Mostrar na pasta",
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(item.value).parent))),
            )
        menu.addAction(
            icons.icon("pin", theme.ACCENT if item.pinned else theme.TEXT_2, 15),
            "Desafixar" if item.pinned else "Fixar na estante",
            lambda: self._pin(item),
        )
        menu.addSeparator()
        menu.addAction(
            icons.icon("trash", theme.DANGER, 15), "Tirar da estante", lambda: self.shelf.remove(item.id)
        )
        with self.ctx.keep_open():
            menu.exec(self.grid.viewport().mapToGlobal(pos))

    def _pin(self, item: ShelfItem) -> None:
        result = self.shelf.pin(item.id, not item.pinned)
        if result is None:
            self.status.setText("Não deu para fixar este item.")
        else:
            self.status.setText("Fixado: continua aqui depois de fechar." if result.pinned else "Desafixado.")

    def show_qr(self, item: ShelfItem) -> None:
        self.ctx.qr_requested.emit(item.value)

    def _copy(self, item: ShelfItem) -> None:
        QGuiApplication.clipboard().setMimeData(item_mime([item]))
        self.status.setText("Copiado: cole onde quiser.")

    def on_show(self) -> None:
        self.reload()
