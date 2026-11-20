"""Atalhos: apps, pastas, arquivos e sites a um clique. Aceita arrastar e soltar."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileInfo, QPoint, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFileDialog,
    QFileIconProvider,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QWidget,
)

from ventude.core.store import Shortcut
from ventude.services import launcher
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.pages.base import Page
from ventude.ui.widgets import EmptyState, IconButton, hbox, label

ID_ROLE = Qt.ItemDataRole.UserRole
TILE = QSize(108, 92)


class ShortcutGrid(QListWidget):
    """Grade de atalhos. Arquivos soltos aqui sobem para a ilha, que trata todos os casos."""

    def __init__(self) -> None:
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setGridSize(QSize(TILE.width() + 10, TILE.height() + 10))
        self.setIconSize(QSize(34, 34))
        self.setSpacing(0)
        self.setStyleSheet(
            f"QListWidget::item {{ background: {theme.SURFACE}; border: 1px solid {theme.STROKE};"
            " border-radius: 14px; padding-top: 12px; margin: 0px; }"
            f"QListWidget::item:hover {{ background: {theme.SURFACE_2}; border-color: {theme.STROKE_2}; }}"
        )
        self.setWordWrap(True)
        self.setUniformItemSizes(True)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.setEditTriggers(QListWidget.EditTrigger.NoEditTriggers)
        self.setDragDropMode(QListWidget.DragDropMode.NoDragDrop)
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)


class ShortcutsPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        row, self.subtitle = self.header("Atalhos")
        for text, icon_name, slot in (
            (" App", "apps", self._add_app),
            (" Pasta", "folder", self._add_folder),
            (" Site", "globe", self._toggle_site_form),
        ):
            button = QPushButton(text)
            button.setIcon(icons.icon(icon_name, theme.TEXT, 15))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(slot)
            row.addWidget(button)

        self.url = QLineEdit()
        self.url.setPlaceholderText("Endereço, ex.: github.com")
        self.url_name = QLineEdit()
        self.url_name.setPlaceholderText("Nome (opcional)")
        add = QPushButton("Adicionar")
        add.setObjectName("Primary")
        add.clicked.connect(self._add_site)
        close = IconButton("close", "Cancelar", 30, 15)
        close.clicked.connect(self._toggle_site_form)
        self.url.returnPressed.connect(self._add_site)
        self.url_name.returnPressed.connect(self._add_site)
        self.site_form = QWidget()
        self.site_form.setLayout(hbox(self.url, self.url_name, add, close, spacing=8))
        self.url.setMinimumWidth(260)
        self.site_form.hide()
        self.root.addWidget(self.site_form)

        self.grid = ShortcutGrid()
        self.grid.itemClicked.connect(self._open)
        self.grid.itemChanged.connect(self._renamed)
        self.grid.customContextMenuRequested.connect(self._menu)
        self.empty = EmptyState(
            "apps",
            "Seus atalhos ficam aqui",
            "Arraste apps, pastas, arquivos ou links para a ilha (mesmo fechada), ou use os botões acima.",
        )
        self.root.addWidget(self.grid, 1)
        self.root.addWidget(self.empty, 1)
        self.status = label("", "Faint")
        self.root.addWidget(self.status)
        self._icons = QFileIconProvider()
        self._loading = False
        ctx.data_changed.connect(lambda kind: kind == "shortcuts" and self.reload())
        self.reload()

    # ---------- dados ----------
    def reload(self) -> None:
        self._loading = True
        self.grid.clear()
        items = self.ctx.store.shortcuts()
        for shortcut in items:
            self.grid.addItem(self._item(shortcut))
        self.grid.setVisible(bool(items))
        self.empty.setVisible(not items)
        self.subtitle.setText(
            f"{len(items)} atalho{'s' if len(items) != 1 else ''} · clique para abrir, "
            "botão direito para editar, arraste para a ilha para adicionar"
            if items
            else "Abra o que você mais usa com um clique"
        )
        self._loading = False

    def _item(self, shortcut: Shortcut) -> QListWidgetItem:
        item = QListWidgetItem(shortcut.label)
        item.setSizeHint(TILE)
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        item.setData(ID_ROLE, shortcut)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        item.setToolTip(shortcut.target)
        item.setIcon(self._icon_for(shortcut))
        if not launcher.is_valid(shortcut.kind, shortcut.target):
            item.setForeground(theme.color(theme.MUTED))
            item.setToolTip(f"Não encontrado: {shortcut.target}")
        return item

    def _icon_for(self, shortcut: Shortcut) -> QIcon:
        if shortcut.kind == "url":
            return icons.icon("globe", theme.ACCENT, 34)
        if shortcut.kind == "folder" and not Path(shortcut.target).exists():
            return icons.icon("folder", theme.MUTED, 34)
        info = QFileInfo(shortcut.target)
        if info.exists():
            system = self._icons.icon(info)
            if not system.isNull():
                return system
        return icons.icon("folder" if shortcut.kind == "folder" else "file", theme.TEXT_2, 34)

    def _add(self, kind: str, target: str, name: str = "") -> bool:
        if self.ctx.store.add_shortcut(kind, name or launcher.default_label(target, kind), target):
            self.status.setText("")
            self.ctx.data_changed.emit("shortcuts")
            return True
        self.status.setText("Esse atalho já existe.")
        return False

    def add_paths(self, paths: list[str]) -> int:
        """Arquivos, pastas, apps e links soltos na ilha. Devolve quantos entraram."""
        added = repeated = ignored = 0
        for raw in paths:
            raw = (raw or "").strip()
            if raw.startswith(("http://", "https://")):
                ok = self._add("url", raw)
            elif raw and Path(raw).exists():
                ok = self._add(launcher.kind_for(raw), str(Path(raw)))
            else:
                ignored += 1
                continue
            added += ok
            repeated += not ok
        parts = []
        if added:
            parts.append(f"{added} atalho{'s' if added != 1 else ''} adicionado{'s' if added != 1 else ''}")
        if repeated:
            parts.append(f"{repeated} já existia{'m' if repeated != 1 else ''}")
        if ignored:
            parts.append(f"{ignored} item{'s' if ignored != 1 else ''} do Windows sem caminho (ignorado)")
        self.status.setText(" · ".join(parts))
        return added

    _add_paths = add_paths

    def _add_app(self) -> None:
        with self.ctx.keep_open():
            path, _ = QFileDialog.getOpenFileName(
                self.window(),
                "Escolher aplicativo",
                "",
                "Aplicativos e atalhos (*.exe *.lnk *.url *.bat *.cmd *.appref-ms);;Todos os arquivos (*)",
            )
        if path:
            self._add(launcher.kind_for(path), str(Path(path)))

    def _add_folder(self) -> None:
        with self.ctx.keep_open():
            path = QFileDialog.getExistingDirectory(self.window(), "Escolher pasta")
        if path:
            self._add("folder", str(Path(path)))

    def _toggle_site_form(self) -> None:
        self.site_form.setVisible(not self.site_form.isVisible())
        if self.site_form.isVisible():
            self.url.setFocus()

    def _add_site(self) -> None:
        url = launcher.normalize_url(self.url.text())
        if not url:
            self.status.setText("Endereço inválido. Ex.: github.com ou https://exemplo.com")
            return
        self._add("url", url, self.url_name.text().strip())
        self.url.clear()
        self.url_name.clear()
        self.site_form.hide()

    # ---------- interação ----------
    def _open(self, item: QListWidgetItem) -> None:
        shortcut: Shortcut = item.data(ID_ROLE)
        ok, message = launcher.open_target(shortcut.kind, shortcut.target)
        self.status.setText("" if ok else f"Não foi possível abrir: {message}")

    def _renamed(self, item: QListWidgetItem) -> None:
        if self._loading:
            return
        shortcut: Shortcut = item.data(ID_ROLE)
        if shortcut and item.text().strip() and item.text() != shortcut.label:
            self.ctx.store.rename_shortcut(shortcut.id, item.text())
            self.ctx.data_changed.emit("shortcuts")

    def _menu(self, pos: QPoint) -> None:
        item = self.grid.itemAt(pos)
        if not item:
            return
        shortcut: Shortcut = item.data(ID_ROLE)
        ids = [s.id for s in self.ctx.store.shortcuts()]
        index = ids.index(shortcut.id) if shortcut.id in ids else 0
        menu = QMenu(self)
        menu.addAction(icons.icon("external", theme.TEXT_2, 15), "Abrir", lambda: self._open(item))
        menu.addAction(icons.icon("edit", theme.TEXT_2, 15), "Renomear", lambda: self.grid.editItem(item))
        if index > 0:
            menu.addAction(
                icons.icon("chevron-left", theme.TEXT_2, 15), "Mover para trás", lambda: self._move(index, -1)
            )
        if index < len(ids) - 1:
            menu.addAction(
                icons.icon("chevron-right", theme.TEXT_2, 15),
                "Mover para frente",
                lambda: self._move(index, 1),
            )
        menu.addSeparator()
        menu.addAction(icons.icon("trash", theme.DANGER, 15), "Remover", lambda: self._remove(shortcut))
        with self.ctx.keep_open():
            menu.exec(self.grid.viewport().mapToGlobal(pos))

    def _move(self, index: int, delta: int) -> None:
        ids = [s.id for s in self.ctx.store.shortcuts()]
        ids.insert(index + delta, ids.pop(index))
        self.ctx.store.reorder_shortcuts(ids)
        self.ctx.data_changed.emit("shortcuts")

    def _remove(self, shortcut: Shortcut) -> None:
        self.ctx.store.remove_shortcut(shortcut.id)
        self.ctx.data_changed.emit("shortcuts")

    def on_show(self) -> None:
        pass
