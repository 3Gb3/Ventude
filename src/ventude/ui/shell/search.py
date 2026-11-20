"""Pesquisa do Ventude Shell (tecla Windows): "Pesquisas recentes" quando vazia e, ao digitar, resultados por
seção (Melhor correspondência, Apps, Configurações, Pastas, Arquivos, Ventude, Web) com um painel de ações
para o resultado escolhido: Abrir, Executar como administrador, Abrir local, Fixar no Iniciar/na barra,
Terminal e editor (pastas). Os mesmos resultados aparecem ao digitar no Menu Iniciar.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ventude.services import apps
from ventude.ui import icons, theme
from ventude.ui.command import KIND_LABELS, SECTION_ORDER, Command, CommandEngine
from ventude.ui.shell.common import (
    HOVER,
    Avatar,
    HoverButton,
    ShellWindow,
    app_pixmap,
    draw_pixmap,
    shell_font,
)

SEARCH_SIZE = QSize(760, 700)
RESULT_LIMIT = 16
TIPS = (
    ("calculator", "Calculadora", "1450 * 5,85"),
    ("check-square", "Nova tarefa", "t: revisar o PR"),
    ("calendar", "Compromisso", "a: dentista amanhã 9h"),
    ("clock", "Timer", "timer 15m"),
    ("folder", "Só pastas", "/p projetos"),
    ("settings", "Configurações do Windows", "wi-fi"),
    ("qr", "QR Code", "qr https://"),
    ("coffee", "Cafeína", "cafeina 1h"),
)


def search_field(placeholder: str) -> QLineEdit:
    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    field.setFixedHeight(40)
    field.addAction(icons.icon("search", theme.ACCENT, 16), QLineEdit.ActionPosition.LeadingPosition)
    field.setStyleSheet(
        f"QLineEdit {{ background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.08);"
        f" border-radius: 20px; padding: 0 14px; font-size: 13px; color: {theme.TEXT}; }}"
        "QLineEdit:focus { border: 1px solid rgba(110,231,183,0.55); background: rgba(255,255,255,0.08); }"
    )
    return field


def describe(command: Command) -> str:
    if command.kind in KIND_LABELS:
        return KIND_LABELS[command.kind]
    return command.subtitle or "Ventude"


@dataclass
class Row:
    kind: str  # header | best | item
    text: str = ""
    command: Command | None = None


def group_rows(commands: list[Command]) -> list[Row]:
    """Primeiro a melhor correspondência, depois as seções na ordem do Windows."""
    if not commands:
        return []
    rows = [Row("header", "Melhor correspondência"), Row("best", command=commands[0])]
    sections: dict[str, list[Command]] = {}
    for command in commands[1:]:
        sections.setdefault(command.section or "Ventude", []).append(command)
    order = list(SECTION_ORDER) + [s for s in sections if s not in SECTION_ORDER]
    for section in order:
        if section in sections:
            rows.append(Row("header", section))
            rows += [Row("item", command=c) for c in sections[section]]
    return rows


class ResultList(QWidget):
    """Lista desenhada à mão com cabeçalhos de seção; a seleção acompanha as setas do teclado."""

    activated = Signal(object)  # Command
    selected = Signal(object)  # Command
    context = Signal(object, QPoint)

    HEADER, BEST, ITEM = 30, 60, 40

    def __init__(self, ctx: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.rows: list[Row] = []
        self.current = -1
        self.hover = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        cache = getattr(ctx, "icons", None)
        if cache is not None:
            cache.changed.connect(lambda _n: self.update())

    def height_of(self, row: Row) -> int:
        return {"header": self.HEADER, "best": self.BEST}.get(row.kind, self.ITEM)

    def set_rows(self, rows: list[Row]) -> None:
        self.rows = rows
        self.current = next((i for i, r in enumerate(rows) if r.command is not None), -1)
        self.setFixedHeight(sum(self.height_of(r) for r in rows) + 4)
        self.update()
        command = self.current_command()
        if command is not None:
            self.selected.emit(command)

    def rects(self) -> list[QRectF]:
        out, y = [], 0.0
        for row in self.rows:
            h = self.height_of(row)
            out.append(QRectF(0, y, self.width(), h))
            y += h
        return out

    def current_command(self) -> Command | None:
        if 0 <= self.current < len(self.rows):
            return self.rows[self.current].command
        return None

    def step(self, step: int) -> None:
        selectable = [i for i, r in enumerate(self.rows) if r.command is not None]
        if not selectable:
            return
        position = selectable.index(self.current) if self.current in selectable else 0
        self.current = selectable[(position + step) % len(selectable)]
        self.update()
        command = self.current_command()
        if command is not None:
            self.selected.emit(command)

    def current_rect(self) -> QRect:
        rects = self.rects()
        return rects[self.current].toRect() if 0 <= self.current < len(rects) else QRect()

    def index_at(self, pos: QPoint) -> int:
        for index, rect in enumerate(self.rects()):
            if rect.contains(pos.toPointF()) and self.rows[index].command is not None:
                return index
        return -1

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for index, (row, rect) in enumerate(zip(self.rows, self.rects(), strict=True)):
            if row.kind == "header":
                p.setPen(QColor(theme.TEXT))
                p.setFont(shell_font(13, QFont.Weight.DemiBold))
                p.drawText(rect.adjusted(12, 6, 0, 0), Qt.AlignmentFlag.AlignVCenter, row.text)
                continue
            command = row.command
            assert command is not None
            box = rect.adjusted(2, 1, -2, -1)
            if index == self.current:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 22))
                p.drawRoundedRect(box, 6, 6)
                p.setBrush(QColor(theme.ACCENT))
                p.drawRoundedRect(QRectF(box.left(), box.center().y() - 8, 3, 16), 1.5, 1.5)
            elif index == self.hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(HOVER)
                p.drawRoundedRect(box, 6, 6)
            size = 32 if row.kind == "best" else 22
            pixmap = app_pixmap(self.ctx, command.kind, command.target or command.path, size, command.icon)
            if command.kind not in ("app", "uwp", "folder", "file") or not (command.target or command.path):
                pixmap = icons.pixmap(
                    command.icon, theme.ACCENT if command.kind == "calc" else theme.TEXT_2, size
                )
            draw_pixmap(p, pixmap, QRectF(box.left() + 12, box.center().y() - size / 2, size, size))
            x = box.left() + 12 + size + 12
            width = box.right() - x - 10
            title_font = shell_font(
                14 if row.kind == "best" else 13,
                QFont.Weight.DemiBold if row.kind == "best" else QFont.Weight.Normal,
            )
            p.setFont(title_font)
            p.setPen(QColor(theme.TEXT))
            title = QFontMetrics(title_font).elidedText(
                command.title, Qt.TextElideMode.ElideRight, int(width)
            )
            if row.kind == "best":
                p.drawText(QRectF(x, box.center().y() - 19, width, 19), Qt.AlignmentFlag.AlignBottom, title)
                p.setFont(shell_font(12))
                p.setPen(QColor(theme.TEXT_2))
                p.drawText(
                    QRectF(x, box.center().y() + 2, width, 17), Qt.AlignmentFlag.AlignTop, describe(command)
                )
            else:
                p.drawText(QRectF(x, box.top(), width, box.height()), Qt.AlignmentFlag.AlignVCenter, title)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        index = self.index_at(event.position().toPoint())
        if index != self.hover:
            self.hover = index
            self.update()

    def leaveEvent(self, _event: object) -> None:
        self.hover = -1
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        index = self.index_at(event.position().toPoint())
        if index < 0:
            return
        command = self.rows[index].command
        if event.button() == Qt.MouseButton.LeftButton:
            self.current = index
            self.update()
            self.activated.emit(command)
        elif event.button() == Qt.MouseButton.RightButton:
            self.current = index
            self.update()
            self.selected.emit(command)
            self.context.emit(command, event.globalPosition().toPoint())


class DetailPane(QWidget):
    """Painel da direita: ícone grande, nome, tipo e as ações do resultado escolhido."""

    def __init__(
        self, ctx: object, run: Callable[[Callable[[], str]], None], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.ctx, self._run = ctx, run
        self.command: Command | None = None
        self.setObjectName("Detail")
        self.setStyleSheet("QWidget#Detail { background: rgba(255,255,255,0.035); border-radius: 8px; }")
        self.icon = QLabel()
        self.icon.setFixedSize(64, 64)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.kind = QLabel()
        self.kind.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.kind.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 12px;")
        self.path = QLabel()
        self.path.setWordWrap(True)
        self.path.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.path.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        self.actions = QVBoxLayout()
        self.actions.setSpacing(2)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 22, 14, 14)
        root.setSpacing(6)
        root.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addSpacing(4)
        root.addWidget(self.title)
        root.addWidget(self.kind)
        root.addWidget(self.path)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(255,255,255,0.08);")
        root.addSpacing(8)
        root.addWidget(line)
        root.addSpacing(6)
        root.addLayout(self.actions)
        root.addStretch(1)
        cache = getattr(ctx, "icons", None)
        if cache is not None:
            cache.changed.connect(lambda _n: self.command and self._paint_icon(self.command))
        self._buttons: list[QWidget] = []

    def _paint_icon(self, command: Command) -> None:
        if command.kind in ("app", "uwp", "folder", "file") and (command.target or command.path):
            pixmap = app_pixmap(self.ctx, command.kind, command.target or command.path, 56, command.icon)
            box = 56
        else:
            pixmap = icons.pixmap(command.icon, theme.ACCENT, 44)
            box = 44
        scale = 3
        canvas = QPixmap(64 * scale, 64 * scale)
        canvas.fill(Qt.GlobalColor.transparent)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        offset = (64 - box) / 2 * scale
        draw_pixmap(p, pixmap, QRectF(offset, offset, box * scale, box * scale))
        p.end()
        canvas.setDevicePixelRatio(scale)
        self.icon.setPixmap(canvas)

    def show_command(
        self, command: Command | None, actions: list[tuple[str, str, Callable[[], str]]]
    ) -> None:
        self.command = command
        for button in self._buttons:
            self.actions.removeWidget(button)
            button.deleteLater()
        self._buttons = []
        if command is None:
            self.title.setText("")
            self.kind.setText("")
            self.path.setText("")
            self.icon.clear()
            return
        self._paint_icon(command)
        self.title.setText(command.title)
        self.kind.setText(describe(command))
        path = command.path if command.kind in ("folder", "file") else ""
        self.path.setText(path)
        self.path.setVisible(bool(path))
        for icon_name, text, action in actions:
            button = HoverButton(icon_name, text, "", QSize(10, 36), 16, 6)
            button.setMinimumWidth(10)
            button.setMaximumWidth(16777215)
            button.setFixedHeight(36)
            button.clicked.connect(lambda a=action: self._run(a))
            self.actions.addWidget(button)
            self._buttons.append(button)


class SearchResults(QWidget):
    """Lista de resultados + painel de ações. Usado pela Pesquisa e pelo Iniciar."""

    finished = Signal()  # abriu algo: quem mostra pode fechar

    def __init__(
        self, ctx: object, shell: object | None, detail: bool = True, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.ctx, self.shell = ctx, shell
        self.engine = CommandEngine(ctx, self)  # type: ignore[arg-type]
        self.engine.dismiss.connect(self.finished.emit)
        self.engine.navigate.connect(lambda key: ctx.navigate_requested.emit(key))  # type: ignore[attr-defined]
        self.engine.tool.connect(self._tool)
        self.list = ResultList(ctx)
        self.list.activated.connect(lambda c: self.run(c))
        self.list.selected.connect(self._selected)
        self.list.context.connect(self._context)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.list)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll = scroll
        self.detail = DetailPane(ctx, self._run_action)
        self.detail.setFixedWidth(250)
        self.detail.setVisible(detail)
        self.status = QLabel("")
        self.status.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 12px;")
        self.empty = QLabel("")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 13px;")
        self.empty.hide()
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(scroll, 1)
        left.addWidget(self.empty, 1)
        left.addWidget(self.status)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addLayout(left, 1)
        row.addWidget(self.detail)
        self.text = ""
        self._armed = ""
        self.commands: list[Command] = []
        ctx.indexer.ready.connect(lambda _n: self.isVisible() and self.text and self.set_query(self.text))  # type: ignore[attr-defined]

    # ---------- consulta ----------
    def set_query(self, text: str) -> None:
        self.text = text
        self._armed = ""
        self.status.setText("")
        self.commands = self.engine.commands(text, limit=RESULT_LIMIT, shell=True) if text.strip() else []
        rows = group_rows(self.commands)
        self.list.set_rows(rows)
        self.scroll.verticalScrollBar().setValue(0)
        nothing = bool(text.strip()) and not rows
        self.empty.setVisible(nothing)
        self.scroll.setVisible(not nothing)
        if nothing:
            self.empty.setText(
                f"Nada encontrado para “{text.strip()}”.\nTente o nome de um app, /p pasta ou 2^10."
            )
            self.detail.show_command(None, [])

    def _selected(self, command: Command) -> None:
        self._armed = ""
        self.status.setText(
            "Enter abre · Ctrl + Enter: Terminal · Shift + Enter: Cursor/VS Code"
            if command.ctrl or command.shift
            else ""
        )
        self.detail.show_command(command, self.actions_for(command))
        rect = self.list.current_rect()
        if not rect.isNull():
            self.scroll.ensureVisible(rect.center().x(), rect.center().y(), 0, rect.height())

    def actions_for(self, command: Command) -> list[tuple[str, str, Callable[[], str]]]:
        primary = {
            "calc": ("copy", "Copiar resultado"),
            "web": ("globe", "Abrir no navegador"),
            "power": (command.icon, command.title),
        }.get(command.kind, ("external", "Abrir"))
        actions: list[tuple[str, str, Callable[[], str]]] = [(primary[0], primary[1], command.run)]
        target = command.target or command.path
        if command.kind == "app" and target.lower().endswith((".exe", ".lnk")):
            actions.append(
                ("admin", "Executar como administrador", lambda t=target: self._message(apps.run_as_admin(t)))
            )
        if command.kind in ("folder",):
            actions += [(icon, text.split(" (")[0], action) for icon, text, action in command.actions]
        if command.kind in ("app", "file", "folder") and target:
            actions.append(
                (
                    "folder-open",
                    "Abrir local do arquivo",
                    lambda t=target: self._message(apps.open_location(t)),
                )
            )
        if command.pinnable and self.shell is not None:
            store = self.ctx.store  # type: ignore[attr-defined]
            for place, label in (("start", "Iniciar"), ("taskbar", "barra de tarefas")):
                pinned = store.pinned(place, command.target)
                text = (
                    f"Desafixar {'do' if place == 'start' else 'da'} {label}"
                    if pinned
                    else f"Fixar {'no' if place == 'start' else 'na'} {label}"
                )
                actions.append(
                    (
                        "pin",
                        text,
                        lambda p=place, c=command: self._toggle_pin(p, c),
                    )
                )
        if command.kind == "file" and target:
            actions.append(("copy", "Copiar caminho", lambda t=target: self.engine._copy(t)))
        return actions

    def _toggle_pin(self, place: str, command: Command) -> str:
        message = self.shell.toggle_pin(place, command.kind, command.title, command.target)  # type: ignore[attr-defined]
        self.detail.show_command(command, self.actions_for(command))
        self.status.setText(message)
        return ""

    def _message(self, result: tuple[bool, str]) -> str:
        ok, error = result
        if ok:
            self.finished.emit()
            return ""
        self.status.setText(f"Não deu: {error}")
        return ""

    def _tool(self, name: str) -> None:
        self.finished.emit()
        tool = getattr(self.ctx, "tool_requested", None)
        if tool is not None:
            tool.emit(name)

    # ---------- executar ----------
    def run(self, command: Command | None = None, variant: str = "") -> None:
        command = command or self.list.current_command()
        if command is None:
            return
        if command.confirm and self._armed != command.title:
            self._armed = command.title
            self.status.setText(f"⚠ {command.title}: Enter de novo para confirmar")
            return
        action = {"ctrl": command.ctrl, "shift": command.shift}.get(variant) or command.run
        self._run_action(action)

    def _run_action(self, action: Callable[[], str]) -> None:
        self._armed = ""
        message = action()
        if not message:
            return  # a ação já cuidou de tudo (abriu uma aba, a cortina de energia...)
        if message.startswith("Não deu"):
            self.status.setText(message)
        elif message.startswith(("Abrindo", "Terminal aberto")):
            self.finished.emit()
        else:  # "Tarefa criada", "8482,5 copiado"...: mostra e fecha
            self.status.setText(f"✓ {message}")
            QTimer.singleShot(900, self.finished.emit)

    def handle_key(self, event: QKeyEvent) -> bool:
        key = event.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self.list.step(1 if key == Qt.Key.Key_Down else -1)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            mods = event.modifiers()
            variant = (
                "ctrl"
                if mods & Qt.KeyboardModifier.ControlModifier
                else "shift"
                if mods & Qt.KeyboardModifier.ShiftModifier
                else ""
            )
            self.run(variant=variant)
            return True
        return False

    def _context(self, command: Command, pos: QPoint) -> None:
        from ventude.ui.shell.taskbar import styled_menu

        menu = styled_menu(self)
        for icon_name, text, action in self.actions_for(command):
            menu.addAction(
                icons.icon(icon_name, theme.TEXT_2, 15), text, lambda a=action: self._run_action(a)
            )
        menu.exec(pos)


class RecentRow(QWidget):
    """Uma pesquisa recente: ícone num quadrado, nome, tipo e quando (o X aparece no hover)."""

    def __init__(
        self, ctx: object, launch: object, open_: Callable[[object], None], forget: Callable[[object], None]
    ) -> None:
        super().__init__()
        self.ctx, self.launch, self._open, self._forget = ctx, launch, open_, forget
        self.setFixedHeight(58)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        cache = getattr(ctx, "icons", None)
        if cache is not None:
            cache.changed.connect(lambda _n: self.update())

    def close_rect(self) -> QRectF:
        return QRectF(self.width() - 40, (self.height() - 28) / 2, 28, 28)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        launch = self.launch
        hover = self.underMouse()
        if hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 14))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 1, -2, -1), 6, 6)
        tile = QRectF(12, (self.height() - 40) / 2, 40, 40)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 14))
        p.drawRoundedRect(tile, 6, 6)
        kind, target = launch.kind, launch.target  # type: ignore[attr-defined]
        if kind == "system":
            item = next((s for s in apps.SYSTEM_ITEMS if s.target == target), None)
            pixmap = icons.pixmap(item.icon if item else "settings", theme.TEXT, 22)
        else:
            pixmap = app_pixmap(self.ctx, kind, target, 24)
        draw_pixmap(p, pixmap, tile.adjusted(8, 8, -8, -8))
        x = tile.right() + 14
        p.setPen(QColor(theme.TEXT))
        p.setFont(shell_font(13, QFont.Weight.DemiBold))
        p.drawText(QRectF(x, 9, self.width() - x - 150, 20), Qt.AlignmentFlag.AlignBottom, launch.label)  # type: ignore[attr-defined]
        p.setPen(QColor(theme.TEXT_2))
        p.setFont(shell_font(12))
        p.drawText(
            QRectF(x, 30, self.width() - x - 150, 18),
            Qt.AlignmentFlag.AlignTop,
            KIND_LABELS.get(kind, "Item"),
        )
        if hover:
            close = self.close_rect()
            inside = close.contains(self.mapFromGlobal(QCursor.pos()).toPointF())
            if inside:
                p.setBrush(QColor(255, 255, 255, 20))
                p.drawRoundedRect(close, 5, 5)
            draw_pixmap(p, icons.pixmap("close", theme.TEXT, 14), close.adjusted(7, 7, -7, -7))
        else:
            p.drawText(
                QRectF(0, 0, self.width() - 16, self.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                apps.relative_time(launch.ts),  # type: ignore[attr-defined]
            )

    def enterEvent(self, _event: object) -> None:
        self.update()

    def leaveEvent(self, _event: object) -> None:
        self.update()

    def mouseMoveEvent(self, _event: QMouseEvent) -> None:
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.close_rect().contains(event.position()):
            self._forget(self.launch)
        elif self.rect().contains(event.position().toPoint()):
            self._open(self.launch)


class RecentPanel(QWidget):
    """Busca vazia: "Pesquisas recentes" (ou sugestões do que dá para digitar)."""

    tip_chosen = Signal(str)

    def __init__(self, ctx: object, shell: object | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx, self.shell = ctx, shell
        header = QHBoxLayout()
        self.heading = QLabel("Pesquisas recentes")
        self.heading.setStyleSheet("font-size: 15px; font-weight: 600;")
        header.addWidget(self.heading)
        header.addStretch(1)
        name = apps.display_name() if shell is not None else "Você"
        self.avatar = Avatar(name, "", 26)
        self.avatar.setToolTip(name)
        self.clear_btn = HoverButton("trash", "", "Limpar o histórico", QSize(32, 32), 15, 6)
        self.clear_btn.clicked.connect(self._clear)
        self.settings_btn = HoverButton("settings", "", "Ajustes do Ventude Shell", QSize(32, 32), 15, 6)
        self.settings_btn.clicked.connect(lambda: shell and shell.open_settings())  # type: ignore[attr-defined]
        header.addWidget(self.avatar)
        header.addSpacing(6)
        header.addWidget(self.clear_btn)
        header.addWidget(self.settings_btn)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(2)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 4, 0)
        body_layout.addLayout(self.rows)
        body_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        root.addLayout(header)
        root.addWidget(scroll, 1)
        self._widgets: list[QWidget] = []

    def refresh(self) -> None:
        for widget in self._widgets:
            self.rows.removeWidget(widget)
            widget.deleteLater()
        self._widgets = []
        launches = self.ctx.store.launches(12)  # type: ignore[attr-defined]
        self.heading.setText("Pesquisas recentes" if launches else "Experimente digitar")
        self.clear_btn.setVisible(bool(launches))
        if launches:
            for launch in launches:
                row = RecentRow(self.ctx, launch, self._open, self._forget)
                self.rows.addWidget(row)
                self._widgets.append(row)
        else:
            for icon_name, title, example in TIPS:
                row = HoverButton(icon_name, f"{title}  ·  {example}", "", QSize(10, 40), 16, 6)
                row.setMinimumWidth(10)
                row.setMaximumWidth(16777215)
                row.icon_color = theme.ACCENT
                row.clicked.connect(lambda e=example: self.tip_chosen.emit(e))
                self.rows.addWidget(row)
                self._widgets.append(row)

    def _open(self, launch: object) -> None:
        if self.shell is not None:
            self.shell.launch(launch.kind, launch.target, launch.label)  # type: ignore[attr-defined]

    def _forget(self, launch: object) -> None:
        self.ctx.store.forget_launch(launch.target)  # type: ignore[attr-defined]
        self.refresh()

    def _clear(self) -> None:
        self.ctx.store.clear_launches()  # type: ignore[attr-defined]
        self.refresh()


class SearchScreen(ShellWindow):
    """A tela de Pesquisa (tecla Windows / lupa da barra)."""

    def __init__(self, ctx: object, shell: object | None) -> None:
        super().__init__(ctx)
        self.shell = shell
        self.setWindowTitle("Ventude: Pesquisa")
        self.input = search_field("Pesquisar aplicativos, configurações e documentos")
        self.input.textChanged.connect(self._changed)
        self.input.installEventFilter(self)
        self.stack = QStackedWidget()
        self.recent = RecentPanel(ctx, shell)
        self.recent.tip_chosen.connect(self.input.setText)
        self.results = SearchResults(ctx, shell, detail=True)
        self.results.finished.connect(self.dismiss)
        self.stack.addWidget(self.recent)
        self.stack.addWidget(self.results)
        hint = QLabel("↑↓ escolhem · Enter abre · Ctrl + Enter Terminal · Shift + Enter editor · Esc fecha")
        hint.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 14)
        root.setSpacing(16)
        root.addWidget(self.input)
        root.addWidget(self.stack, 1)
        root.addWidget(hint)
        self.opened_at = 0.0

    def open_search(self, rect: QRect, text: str = "") -> None:
        self.opened_at = time.monotonic()
        self.input.setText(text)
        self._changed(text)
        self.open_at(rect)
        self.activateWindow()
        self.raise_()
        try:
            from ventude.services.winutil import force_foreground

            force_foreground(int(self.winId()))
        except Exception:  # noqa: BLE001
            pass
        self.input.setFocus()
        self.input.end(False)

    def _changed(self, text: str) -> None:
        if text.strip():
            self.results.set_query(text)
            self.stack.setCurrentWidget(self.results)
        else:
            self.recent.refresh()
            self.stack.setCurrentWidget(self.recent)

    def dismiss(self) -> None:
        self.close_animated()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.input and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() == Qt.Key.Key_Escape:
                self.dismiss()
                return True
            if self.input.text().strip() and self.results.handle_key(event):
                return True
        return super().eventFilter(watched, event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self.isVisible():
            if time.monotonic() - self.opened_at > 0.35:  # clicou fora: fecha (como no Windows)
                QTimer.singleShot(60, self._close_if_inactive)

    def _close_if_inactive(self) -> None:
        if not self.isActiveWindow() and self.isVisible() and not self.closing:
            from PySide6.QtWidgets import QApplication

            popup = QApplication.activePopupWidget()
            if popup is None:
                self.dismiss()
