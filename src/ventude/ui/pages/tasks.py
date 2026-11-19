"""Tarefas: listas simples, rápidas de marcar, editar e limpar."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ventude.core.store import TaskItem
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.pages.base import Page, scroller
from ventude.ui.widgets import Card, CheckCircle, EmptyState, IconButton, hbox, label

LIST_ROLE = Qt.ItemDataRole.UserRole


class TaskRow(QWidget):
    def __init__(self, page: TasksPage, item: TaskItem) -> None:
        super().__init__()
        self.page, self.item = page, item
        self.check = CheckCircle()
        self.check.setChecked(item.done)
        self.check.toggled.connect(self._toggle)
        self.text = QLineEdit(item.text)
        self.text.setObjectName("Inline")
        self.text.setReadOnly(True)
        self.text.setCursor(Qt.CursorShape.IBeamCursor)
        self.text.setToolTip("Clique duas vezes para editar")
        if item.done:
            font = QFont(self.text.font())
            font.setStrikeOut(True)
            self.text.setFont(font)
            self.text.setStyleSheet(f"color: {theme.MUTED};")
        self.text.editingFinished.connect(self._save)
        self.text.installEventFilter(self)
        delete = IconButton("close", "Excluir tarefa", 26, 14, theme.MUTED)
        delete.clicked.connect(lambda: page.delete_item(item.id))
        self.setLayout(hbox(self.check, self.text, delete, spacing=6))

    def eventFilter(self, obj: object, event: object) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent

        if obj is self.text and event.type() == QEvent.Type.MouseButtonDblClick:
            self.text.setReadOnly(False)
            self.text.selectAll()
            return True
        return False

    def _save(self) -> None:
        if not self.text.isReadOnly():
            self.text.setReadOnly(True)
            if self.text.text().strip() and self.text.text().strip() != self.item.text:
                self.page.ctx.store.edit_item(self.item.id, self.text.text())
                self.page.ctx.data_changed.emit("tasks")

    def _toggle(self, done: bool) -> None:
        self.page.ctx.store.set_done(self.item.id, done)
        QTimer.singleShot(220, lambda: self.page.ctx.data_changed.emit("tasks"))


class TasksPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        self.current: int | None = None

        side = Card(margins=(10, 12, 10, 10), spacing=6)
        side.setFixedWidth(210)
        side.body.addWidget(label("  Listas", "Caption"))
        self.lists = QListWidget()
        self.lists.currentItemChanged.connect(self._pick)
        self.lists.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.lists.customContextMenuRequested.connect(self._list_menu)
        side.body.addWidget(self.lists, 1)
        self.new_list = QLineEdit()
        self.new_list.setPlaceholderText("+ Nova lista")
        self.new_list.returnPressed.connect(self._add_list)
        side.body.addWidget(self.new_list)

        main = QVBoxLayout()
        main.setSpacing(10)
        self.title = QLineEdit()
        self.title.setObjectName("Inline")
        self.title.setStyleSheet("font-size: 19px; font-weight: 600;")
        self.title.editingFinished.connect(self._rename)
        self.counter = label("", "Faint")
        clear = QPushButton("Limpar concluídas")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.clicked.connect(self._clear_done)
        self.clear_btn = clear
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title_box.addWidget(self.title)
        title_box.addWidget(self.counter)
        main.addLayout(hbox(title_box, clear, spacing=8))
        self.items_box = QWidget()
        self.rows = QVBoxLayout(self.items_box)
        self.rows.setContentsMargins(0, 0, 6, 0)
        self.rows.setSpacing(2)
        self.rows.addStretch(1)
        self.items_scroll = scroller(self.items_box)
        self.empty = EmptyState("check-square", "Lista vazia", "Escreva abaixo e tecle Enter.")
        main.addWidget(self.items_scroll, 1)
        main.addWidget(self.empty, 1)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Adicionar tarefa e tecle Enter")
        self.input.returnPressed.connect(self._add_item)
        main.addWidget(self.input)

        body = hbox(side, spacing=16)
        body.addLayout(main, 1)
        self.root.addLayout(body, 1)
        ctx.data_changed.connect(lambda kind: kind == "tasks" and self.refresh())
        self.refresh()

    # ---------- listas ----------
    def refresh(self) -> None:
        lists = self.ctx.store.lists()
        if self.current not in [lst.id for lst in lists]:
            self.current = lists[0].id
        self.lists.blockSignals(True)
        self.lists.clear()
        for lst in lists:
            pending = self.ctx.store.pending_count(lst.id)
            item = QListWidgetItem(f"{lst.name}" + (f"   {pending}" if pending else ""))
            item.setData(LIST_ROLE, lst.id)
            item.setIcon(icons.icon("list", theme.TEXT_2, 15))
            self.lists.addItem(item)
            if lst.id == self.current:
                self.lists.setCurrentItem(item)
        self.lists.blockSignals(False)
        self._show_items()

    def _pick(self, item: QListWidgetItem | None) -> None:
        if item:
            self.current = int(item.data(LIST_ROLE))
            self._show_items()

    def _show_items(self) -> None:
        name = next((lst.name for lst in self.ctx.store.lists() if lst.id == self.current), "")
        if not self.title.hasFocus():
            self.title.setText(name)
        while self.rows.count() > 1:
            child = self.rows.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        items = self.ctx.store.items(self.current or 0)
        for index, item in enumerate(items):
            self.rows.insertWidget(index, TaskRow(self, item))
        pending = sum(not i.done for i in items)
        done = len(items) - pending
        self.counter.setText(
            f"{pending} pendente{'s' if pending != 1 else ''}"
            + (f" · {done} concluída{'s' if done != 1 else ''}" if done else "")
        )
        self.clear_btn.setVisible(done > 0)
        self.items_scroll.setVisible(bool(items))
        self.empty.setVisible(not items)

    def _add_list(self) -> None:
        name = self.new_list.text().strip()
        if name:
            self.current = self.ctx.store.add_list(name).id
            self.new_list.clear()
            self.ctx.data_changed.emit("tasks")
            self.input.setFocus()

    def _rename(self) -> None:
        name = self.title.text().strip()
        if self.current is None or not name:
            return
        if not self.ctx.store.rename_list(self.current, name):
            self.counter.setText("Já existe uma lista com esse nome.")
            return
        self.title.clearFocus()
        self.ctx.data_changed.emit("tasks")

    def _list_menu(self, pos: QPoint) -> None:
        item = self.lists.itemAt(pos)
        if not item:
            return
        list_id = int(item.data(LIST_ROLE))
        menu = QMenu(self)
        menu.addAction(icons.icon("edit", theme.TEXT_2, 15), "Renomear", lambda: self._focus_title(list_id))
        menu.addAction(
            icons.icon("trash", theme.DANGER, 15), "Excluir lista", lambda: self._delete_list(list_id)
        )
        with self.ctx.keep_open():
            menu.exec(self.lists.viewport().mapToGlobal(pos))

    def _focus_title(self, list_id: int) -> None:
        self.current = list_id
        self.refresh()
        self.title.setFocus()
        self.title.selectAll()

    def _delete_list(self, list_id: int) -> None:
        self.ctx.store.remove_list(list_id)
        self.ctx.data_changed.emit("tasks")

    # ---------- itens ----------
    def _add_item(self) -> None:
        text = self.input.text().strip()
        if text and self.current is not None:
            self.ctx.store.add_item(self.current, text)
            self.input.clear()
            self.ctx.data_changed.emit("tasks")

    def delete_item(self, item_id: int) -> None:
        self.ctx.store.remove_item(item_id)
        self.ctx.data_changed.emit("tasks")

    def _clear_done(self) -> None:
        if self.current is not None:
            self.ctx.store.clear_done(self.current)
            self.ctx.data_changed.emit("tasks")

    def on_show(self) -> None:
        self.refresh()
