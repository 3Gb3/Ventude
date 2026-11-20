"""Estante: o que você soltou na ilha fica aqui até fechar o Ventude. Arraste para fora para usar."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileInfo, QMimeData, QPoint, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import QFileIconProvider, QListView, QListWidget, QListWidgetItem, QMenu, QPushButton

from ventude.services.shelf import ShelfItem
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.pages.base import Page
from ventude.ui.widgets import EmptyState, label

ITEM_ROLE = Qt.ItemDataRole.UserRole
TILE = QSize(116, 100)


def item_mime(items: list[ShelfItem]) -> QMimeData:
    """Arquivos viram arquivos de verdade (navegador, e-mail, Discord...); textos e links, texto."""
    mime = QMimeData()
    urls = [QUrl.fromLocalFile(i.value) for i in items if i.is_path]
    urls += [QUrl(i.value) for i in items if i.kind == "link"]
    if urls:
        mime.setUrls(urls)
    texts = [i.value for i in items if i.kind in ("text", "link")]
    if texts:
        mime.setText("\n".join(texts))
    return mime


class ShelfGrid(QListWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setGridSize(QSize(TILE.width() + 10, TILE.height() + 10))
        self.setIconSize(QSize(44, 44))
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
        row, self.subtitle = self.header("Estante")
        paste = QPushButton(" Colar")
        paste.setIcon(icons.icon("download", theme.TEXT, 15))
        paste.setToolTip("Guardar o que está na área de transferência (texto, imagem ou arquivos)")
        paste.clicked.connect(self.paste)
        clear = QPushButton(" Limpar")
        clear.setIcon(icons.icon("trash", theme.TEXT, 15))
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
            "Depois é só arrastar daqui para o navegador, o e-mail ou o Discord.",
        )
        self.root.addWidget(self.grid, 1)
        self.root.addWidget(self.empty, 1)
        self.status = label("", "Faint")
        self.root.addWidget(self.status)
        self._icons = QFileIconProvider()
        self.shelf.changed.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        self.grid.clear()
        for item in self.shelf.items:
            entry = QListWidgetItem(self._icon(item), item.name)
            entry.setSizeHint(TILE)
            entry.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            entry.setData(ITEM_ROLE, item)
            entry.setToolTip(item.value if item.kind != "text" else item.value[:400])
            self.grid.addItem(entry)
        count = len(self.shelf.items)
        self.grid.setVisible(bool(count))
        self.empty.setVisible(not count)
        self.subtitle.setText(
            f"{count} ite{'ns' if count != 1 else 'm'} · arraste para fora para usar · "
            "somem ao fechar o Ventude"
            if count
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
        if item.kind == "text":
            QGuiApplication.clipboard().setText(item.value)
            self.status.setText("Texto copiado.")
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
        menu.addAction(icons.icon("edit", theme.TEXT_2, 15), "Copiar", lambda: self._copy(item))
        if item.is_path:
            menu.addAction(
                icons.icon("folder", theme.TEXT_2, 15),
                "Mostrar na pasta",
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(item.value).parent))),
            )
        menu.addSeparator()
        menu.addAction(
            icons.icon("trash", theme.DANGER, 15), "Tirar da estante", lambda: self.shelf.remove(item.id)
        )
        with self.ctx.keep_open():
            menu.exec(self.grid.viewport().mapToGlobal(pos))

    def _copy(self, item: ShelfItem) -> None:
        QGuiApplication.clipboard().setMimeData(item_mime([item]))
        self.status.setText("Copiado: cole onde quiser.")

    def on_show(self) -> None:
        pass
