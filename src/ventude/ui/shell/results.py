"""Lista unificada de resultados do Lançador (sem DetailPane; verbos inline e dot deslizante)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QMimeData, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from ventude.limits.model import PROVIDERS
from ventude.services import apps
from ventude.ui import icons, motion, theme
from ventude.ui.command import KIND_LABELS, SECTION_ORDER, Command, CommandEngine
from ventude.ui.shell.common import app_pixmap, draw_pixmap, shell_font
from ventude.ui.shell.dock import MIME_VENTUDE_ITEM
from ventude.ui.shell.items import find_matching_pin, provider_for_app

RESULT_LIMIT = 16
ROW_HEADER = 28
ROW_NORMAL = 44
ROW_SELECTED = 80
CHIP_H = 24


def describe(command: Command) -> str:
    if command.kind == "window":
        return command.monitor_tag or "Janela aberta"
    if command.kind == "project":
        return f"Projeto · {command.branch}" if command.branch else "Projeto"
    if command.kind == "file":
        from ventude.services.indexer import category_of
        from ventude.ui.command import FILE_LOOK

        return FILE_LOOK.get(category_of(command.target or command.path), FILE_LOOK["file"])[2]
    if command.kind in KIND_LABELS:
        return KIND_LABELS[command.kind]
    return command.subtitle or "Ventude"


def ai_limit_hint(ctx: object, command: Command | None) -> str:
    """Resumo compacto do limite de uso se o comando for um app de IA monitorado."""
    if command is None:
        return ""
    provider = provider_for_app(command.title, command.target or command.path)
    if not provider:
        return ""
    pid = {"gemini": "antigravity"}.get(provider, provider)
    limits = getattr(ctx, "limits", None)
    if limits is None or pid not in limits.providers():
        return ""
    report = limits.report(pid)
    if report.state not in ("ok", "stale"):
        return ""
    head = limits.headline(report)
    if head is None:
        return ""
    used = head.used_at()
    if used is None:
        return ""
    return f"{PROVIDERS.get(pid, pid.title())}: {round(used)}% usado · {head.label}"


@dataclass
class Row:
    kind: str  # header | best | item
    text: str = ""
    command: Command | None = None


def group_rows(commands: list[Command]) -> list[Row]:
    """Melhor correspondência no topo e seções em caixa baixa."""
    if not commands:
        return []
    rows = [Row("header", "melhor correspondência"), Row("best", command=commands[0])]
    sections: dict[str, list[Command]] = {}
    for command in commands[1:]:
        sections.setdefault(command.section or "Ventude", []).append(command)
    order = list(SECTION_ORDER) + [s for s in sections if s not in SECTION_ORDER]
    for section in order:
        if section in sections:
            rows.append(Row("header", section.lower()))
            rows += [Row("item", command=c) for c in sections[section]]
    return rows


def verbs_for(
    ctx: object,
    command: Command,
    *,
    message_cb: Callable[[tuple[bool, str]], str] | None = None,
    pin_cb: Callable[[str, Command], str] | None = None,
) -> list[tuple[str, str, Callable[[], str]]]:
    """Verbos inline disponíveis para o comando selecionado."""
    msg = message_cb or (lambda res: "" if res[0] else f"Não deu: {res[1]}")
    primary = {
        "calc": ("copy", "Copiar"),
        "web": ("globe", "Navegador"),
        "power": (command.icon, command.title),
        "window": ("apps", "Focar"),
    }.get(command.kind, ("external", "Abrir"))
    actions: list[tuple[str, str, Callable[[], str]]] = [(primary[0], primary[1], command.run)]
    target = command.target or command.path
    if command.kind in ("folder", "project"):
        if command.ctrl is not None:
            actions.append(("terminal", "Terminal", command.ctrl))
        if command.shift is not None:
            actions.append(("code", "Editor", command.shift))
        if command.agent is not None:
            actions.append(("spark", "Agente aqui", command.agent))
    if command.kind == "app" and target.lower().endswith((".exe", ".lnk")):
        actions.append(("admin", "Admin", lambda t=target: msg(apps.run_as_admin(t))))
    if command.kind in ("app", "file", "folder", "project") and target:
        actions.append(("folder-open", "Local", lambda t=target: msg(apps.open_location(t))))
    if command.pinnable and pin_cb is not None:
        shell = getattr(ctx, "shell", None)
        store = getattr(ctx, "store", None)
        if shell is not None and hasattr(shell, "find_pin"):
            dock_pinned = shell.find_pin("taskbar", command.kind, command.title, command.target) is not None
        elif store is not None:
            dock_pinned = (
                find_matching_pin(store.pins("taskbar"), command.kind, command.title, command.target)
                is not None
            )
        else:
            dock_pinned = False
        actions.append(
            (
                "pin",
                "Desafixar da doca" if dock_pinned else "Fixar na doca",
                lambda c=command: pin_cb("taskbar", c),
            )
        )
    if command.kind == "file" and target:
        actions.append(("copy", "Caminho", lambda t=target: t))
    return actions[:5]


class ResultList(QWidget):
    """Lista única desenhada à mão: linha ativa 44->80 px com chips de verbo + ponto mint."""

    activated = Signal(object, object)  # (Command, action callable | None)
    selected = Signal(object)
    context = Signal(object, QPoint)

    def __init__(self, ctx: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.rows: list[Row] = []
        self.current = -1
        self.hover = -1
        self.active_chip = 0
        self._verbs: dict[int, list[tuple[str, str, Callable[[], str]]]] = {}
        self._chip_rects: list[tuple[QRectF, int]] = []
        self._hover_chip = -1
        self._dot_y = 0.0
        self._dot_target_y = 0.0
        self._stagger_start = 0.0
        self._drag_start: QPoint | None = None
        self._drag_cmd: Command | None = None
        self._verb_provider: Callable[[Command], list[tuple[str, str, Callable[[], str]]]] | None = None

        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tick = QTimer(self, interval=16)
        self._tick.timeout.connect(self._animate_step)
        cache = getattr(ctx, "icons", None)
        if cache is not None:
            cache.changed.connect(lambda _n: self.update())

    def set_verb_provider(
        self, fn: Callable[[Command], list[tuple[str, str, Callable[[], str]]]]
    ) -> None:
        self._verb_provider = fn

    def height_of(self, index: int, row: Row) -> int:
        if row.kind == "header":
            return ROW_HEADER
        return ROW_SELECTED if index == self.current else ROW_NORMAL

    def set_rows(self, rows: list[Row]) -> None:
        self.rows = rows
        self._verbs.clear()
        self._chip_rects.clear()
        self.active_chip = 0
        self.hover = -1
        self._hover_chip = -1
        self.current = next((i for i, r in enumerate(rows) if r.command is not None), -1)
        self._sync_height()
        rects = self.rects()
        if 0 <= self.current < len(rects):
            self._dot_target_y = float(rects[self.current].top() + ROW_NORMAL / 2.0)
            self._dot_y = self._dot_target_y
        settings = getattr(self.ctx, "settings", None)
        self._stagger_start = time.monotonic() if motion.ms("escada", settings) > 0 else 0.0
        if self._stagger_start > 0.0 and rows:
            self._tick.start()
        self.update()
        command = self.current_command()
        if command is not None:
            self.selected.emit(command)

    def _sync_height(self) -> None:
        total = sum(self.height_of(i, r) for i, r in enumerate(self.rows))
        self.setFixedHeight(total + 4)

    def _chips_for_row(self, index: int, cmd: Command) -> list[tuple[str, str, Callable[[], str]]]:
        if index not in self._verbs:
            if self._verb_provider is not None:
                self._verbs[index] = self._verb_provider(cmd)
            else:
                self._verbs[index] = verbs_for(self.ctx, cmd)
        return self._verbs[index]

    def invalidate_verbs(self) -> None:
        self._verbs.clear()
        self.update()

    def rects(self) -> list[QRectF]:
        out, y = [], 0.0
        for i, row in enumerate(self.rows):
            h = self.height_of(i, row)
            out.append(QRectF(0, y, float(max(1, self.width())), float(h)))
            y += h
        return out

    def current_command(self) -> Command | None:
        if 0 <= self.current < len(self.rows):
            return self.rows[self.current].command
        return None

    def current_action(self) -> Callable[[], str] | None:
        cmd = self.current_command()
        if cmd is None:
            return None
        chips = self._chips_for_row(self.current, cmd)
        if 0 <= self.active_chip < len(chips):
            return chips[self.active_chip][2]
        return cmd.run

    def cycle_chip(self, delta: int) -> bool:
        cmd = self.current_command()
        if cmd is None:
            return False
        chips = self._chips_for_row(self.current, cmd)
        if len(chips) <= 1:
            return False
        self.active_chip = (self.active_chip + delta) % len(chips)
        self.update()
        return True

    def step(self, delta: int) -> None:
        selectable = [i for i, r in enumerate(self.rows) if r.command is not None]
        if not selectable:
            return
        pos = selectable.index(self.current) if self.current in selectable else 0
        self.current = selectable[(pos + delta) % len(selectable)]
        self.active_chip = 0
        self._sync_height()
        rects = self.rects()
        if 0 <= self.current < len(rects):
            self._dot_target_y = float(rects[self.current].top() + ROW_NORMAL / 2.0)
            if motion.reduced_motion(getattr(self.ctx, "settings", None)):
                self._dot_y = self._dot_target_y
            else:
                self._tick.start()
        self.update()
        command = self.current_command()
        if command is not None:
            self.selected.emit(command)

    def current_rect(self) -> QRect:
        rects = self.rects()
        return rects[self.current].toRect() if 0 <= self.current < len(rects) else QRect()

    def index_at(self, pos: QPoint) -> int:
        pt = pos.toPointF()
        for index, rect in enumerate(self.rects()):
            if rect.contains(pt) and self.rows[index].command is not None:
                return index
        return -1

    def _animate_step(self) -> None:
        dirty = False
        if abs(self._dot_y - self._dot_target_y) > 0.5:
            self._dot_y += (self._dot_target_y - self._dot_y) * 0.35
            dirty = True
        else:
            self._dot_y = self._dot_target_y
        if self._stagger_start > 0.0:
            elapsed = (time.monotonic() - self._stagger_start) * 1000.0
            if elapsed >= 10 * 14 + 160:
                self._stagger_start = 0.0
            dirty = True
        if not dirty:
            self._tick.stop()
        self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._chip_rects.clear()
        now = time.monotonic()
        elapsed_ms = (now - self._stagger_start) * 1000.0 if self._stagger_start > 0.0 else 9999.0
        item_ordinal = 0
        rects = self.rects()

        for index, (row, rect) in enumerate(zip(self.rows, rects, strict=True)):
            if row.kind == "header":
                p.setPen(QColor(theme.TEXT_2))
                p.setFont(shell_font(11, QFont.Weight.DemiBold))
                p.drawText(rect.adjusted(14, 4, 0, 0), Qt.AlignmentFlag.AlignVCenter, row.text)
                continue
            command = row.command
            assert command is not None
            alpha = 1.0
            y_off = 0.0
            if self._stagger_start > 0.0 and item_ordinal < 10:
                local_t = max(0.0, min(1.0, (elapsed_ms - item_ordinal * 14.0) / 140.0))
                alpha = local_t
                y_off = (1.0 - motion.out_cubic(local_t)) * 8.0
            item_ordinal += 1
            if alpha <= 0.01:
                continue

            p.save()
            p.setOpacity(alpha)
            box = rect.adjusted(4, 2, -4, -2).translated(0, y_off)
            is_cur = index == self.current
            if is_cur:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.HALO_PRESS))
                p.drawRoundedRect(box, theme.R_ITEM, theme.R_ITEM)
            elif index == self.hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.HALO))
                p.drawRoundedRect(box, theme.R_ITEM, theme.R_ITEM)

            top_h = float(ROW_NORMAL - 4)
            top_rect = QRectF(box.left(), box.top(), box.width(), top_h)
            size = 24 if is_cur else 20
            if command.kind not in ("app", "uwp", "folder", "file", "project") or not (
                command.target or command.path
            ):
                pixmap = icons.pixmap(
                    command.icon,
                    theme.ACCENT if command.kind in ("calc", "window") else theme.TEXT_2,
                    size,
                )
            else:
                pixmap = app_pixmap(
                    self.ctx,
                    "folder" if command.kind == "project" else command.kind,
                    command.target or command.path,
                    size,
                    command.icon,
                )
            draw_pixmap(
                p,
                pixmap,
                QRectF(top_rect.left() + 16, top_rect.center().y() - size / 2.0, size, size),
            )
            tx = top_rect.left() + 16 + size + 12
            desc = describe(command)
            desc_font = shell_font(11)
            desc_w = min(200, QFontMetrics(desc_font).horizontalAdvance(desc) + 6)
            title_w = max(40.0, top_rect.right() - tx - desc_w - 14)

            title_font = shell_font(13, QFont.Weight.DemiBold if is_cur else QFont.Weight.Normal)
            p.setFont(title_font)
            p.setPen(QColor(theme.TEXT))
            title = QFontMetrics(title_font).elidedText(
                command.title, Qt.TextElideMode.ElideRight, int(title_w)
            )
            p.drawText(
                QRectF(tx, top_rect.top(), title_w, top_rect.height()),
                Qt.AlignmentFlag.AlignVCenter,
                title,
            )

            p.setFont(desc_font)
            p.setPen(QColor(theme.TEXT_2))
            desc_txt = QFontMetrics(desc_font).elidedText(desc, Qt.TextElideMode.ElideRight, desc_w)
            p.drawText(
                QRectF(top_rect.right() - desc_w - 10, top_rect.top(), desc_w, top_rect.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                desc_txt,
            )

            if is_cur:
                chips = self._chips_for_row(index, command)
                cx = tx
                cy = box.top() + top_h + 2
                chip_font = shell_font(11)
                p.setFont(chip_font)
                fm = QFontMetrics(chip_font)
                for c_idx, (icon_name, chip_label, _fn) in enumerate(chips):
                    cw = min(148.0, float(fm.horizontalAdvance(chip_label) + 34))
                    if cx + cw > box.right() - 8:
                        break
                    crect = QRectF(cx, cy, cw, float(CHIP_H))
                    self._chip_rects.append((crect, c_idx))
                    selected_chip = c_idx == self.active_chip
                    hovered_chip = c_idx == self._hover_chip
                    if selected_chip:
                        p.setPen(QColor(theme.ACCENT))
                        p.setBrush(QColor(110, 231, 183, 28))
                    elif hovered_chip:
                        p.setPen(QColor(theme.HAIRLINE))
                        p.setBrush(QColor(theme.HALO))
                    else:
                        p.setPen(QColor(theme.HAIRLINE))
                        p.setBrush(QColor(255, 255, 255, 10))
                    p.drawRoundedRect(crect, 12.0, 12.0)
                    fg = theme.ACCENT if selected_chip else theme.TEXT_2
                    draw_pixmap(
                        p,
                        icons.pixmap(icon_name, fg, 12),
                        QRectF(crect.left() + 8, crect.center().y() - 6, 12, 12),
                    )
                    p.setPen(QColor(theme.TEXT if selected_chip else theme.TEXT_2))
                    p.drawText(
                        crect.adjusted(24, 0, -8, 0),
                        Qt.AlignmentFlag.AlignVCenter,
                        chip_label,
                    )
                    cx += cw + 6
            p.restore()

        if 0 <= self.current < len(rects):
            dot_y = self._dot_y or float(rects[self.current].top() + ROW_NORMAL / 2.0)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT))
            p.drawEllipse(QRectF(10.0, dot_y - 3.0, 6.0, 6.0))

    # ---------- mouse e drag ----------
    def _chip_at(self, pos: QPoint) -> int:
        pt = pos.toPointF()
        for rect, c_idx in self._chip_rects:
            if rect.contains(pt):
                return c_idx
        return -1

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        if (
            self._drag_start is not None
            and self._drag_cmd is not None
            and (event.buttons() & Qt.MouseButton.LeftButton)
            and (pos - self._drag_start).manhattanLength() >= 8
        ):
            cmd = self._drag_cmd
            self._drag_start = None
            self._drag_cmd = None
            if cmd.pinnable:
                drag = QDrag(self)
                mime = QMimeData()
                kind = "folder" if cmd.kind == "project" else cmd.kind
                payload = json.dumps({"kind": kind, "label": cmd.title, "target": cmd.target})
                mime.setData(MIME_VENTUDE_ITEM, payload.encode("utf-8"))
                drag.setMimeData(mime)
                drag.setPixmap(app_pixmap(self.ctx, kind, cmd.target or cmd.path, 28, cmd.icon))
                drag.exec(Qt.DropAction.CopyAction)
                return
        index = self.index_at(pos)
        chip = self._chip_at(pos)
        if index != self.hover or chip != self._hover_chip:
            self.hover = index
            self._hover_chip = chip
            self.update()

    def leaveEvent(self, _event: object) -> None:
        self.hover = -1
        self._hover_chip = -1
        self._drag_start = None
        self._drag_cmd = None
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if self._chip_at(pos) >= 0:
                return
            idx = self.index_at(pos)
            if idx >= 0:
                cmd = self.rows[idx].command
                if cmd is not None and cmd.pinnable:
                    self._drag_start = pos
                    self._drag_cmd = cmd

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_start = None
        self._drag_cmd = None
        pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton:
            chip_idx = self._chip_at(pos)
            cmd = self.current_command()
            if chip_idx >= 0 and cmd is not None:
                self.active_chip = chip_idx
                chips = self._chips_for_row(self.current, cmd)
                if 0 <= chip_idx < len(chips):
                    self.activated.emit(cmd, chips[chip_idx][2])
                    return
        index = self.index_at(pos)
        if index < 0:
            return
        command = self.rows[index].command
        assert command is not None
        if event.button() == Qt.MouseButton.LeftButton:
            if index != self.current:
                self.current = index
                self.active_chip = 0
                self._sync_height()
                rects = self.rects()
                self._dot_target_y = float(rects[index].top() + ROW_NORMAL / 2.0)
                self._dot_y = self._dot_target_y
                self.update()
                self.selected.emit(command)
            else:
                self.activated.emit(command, None)
        elif event.button() == Qt.MouseButton.RightButton:
            self.current = index
            self.active_chip = 0
            self._sync_height()
            self.update()
            self.selected.emit(command)
            self.context.emit(command, event.globalPosition().toPoint())


class SearchResults(QWidget):
    """Lista única rolável de resultados + dica de AI/atalhos no rodapé."""

    finished = Signal()

    def __init__(
        self, ctx: object, shell: object | None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.ctx, self.shell = ctx, shell
        self.engine = CommandEngine(ctx, self)  # type: ignore[arg-type]
        self.engine.dismiss.connect(self.finished.emit)
        self.engine.navigate.connect(lambda key: ctx.navigate_requested.emit(key))  # type: ignore[attr-defined]
        self.engine.tool.connect(self._tool)

        self.list = ResultList(ctx)
        self.list.set_verb_provider(self._verbs_for)
        self.list.activated.connect(self._on_row_activated)
        self.list.selected.connect(self._selected)
        self.list.context.connect(self._context)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.list)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll = scroll

        self.empty = QLabel("")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 13px;")
        self.empty.hide()

        self.status = QLabel("")
        self.status.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px;")
        self.ai_hint = QLabel("")
        self.ai_hint.setStyleSheet(f"color: {theme.ACCENT}; font-size: 11px;")
        self.ai_hint.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        root.addWidget(scroll, 1)
        root.addWidget(self.empty, 1)
        root.addWidget(self.ai_hint)
        root.addWidget(self.status)

        self.text = ""
        self._armed = ""
        self.commands: list[Command] = []
        indexer = getattr(ctx, "indexer", None)
        if indexer is not None:
            indexer.ready.connect(lambda _n: self.isVisible() and self.text and self.set_query(self.text))

    def _verbs_for(self, command: Command) -> list[tuple[str, str, Callable[[], str]]]:
        return verbs_for(
            self.ctx,
            command,
            message_cb=self._message,
            pin_cb=self._toggle_pin if self.shell is not None else None,
        )

    def _on_row_activated(self, cmd: object, fn: object) -> None:
        if isinstance(cmd, Command) and (fn is None or fn is cmd.run):
            self.run(cmd)
        elif fn is not None and callable(fn):
            self._run_action(fn)  # type: ignore[arg-type]

    def set_query(self, text: str) -> None:
        self.text = text
        self._armed = ""
        self.status.setText("")
        self.ai_hint.hide()
        self.commands = self.engine.commands(text, limit=RESULT_LIMIT, shell=True) if text.strip() else []
        rows = group_rows(self.commands)
        self.list.set_rows(rows)
        self.scroll.verticalScrollBar().setValue(0)
        nothing = bool(text.strip()) and not rows
        self.empty.setVisible(nothing)
        self.scroll.setVisible(not nothing)
        if nothing:
            self.empty.setText(
                f"Nada encontrado para “{text.strip()}”.\n"
                "Tente um app, / pasta, > comando, @ janela ou = 2^10."
            )

    def _selected(self, command: Command) -> None:
        self._armed = ""
        hint = ai_limit_hint(self.ctx, command)
        if hint:
            self.ai_hint.setText(hint)
            self.ai_hint.show()
        else:
            self.ai_hint.hide()
        if command.agent is not None:
            self.status.setText("Enter abre · Ctrl+Enter Terminal · Shift+Enter Editor · Tab verbos")
        elif command.ctrl or command.shift:
            self.status.setText("Enter abre · Ctrl+Enter Terminal · Shift+Enter Editor")
        else:
            self.status.setText("Tab alterna verbos" if len(self._verbs_for(command)) > 1 else "")
        rect = self.list.current_rect()
        if not rect.isNull():
            self.scroll.ensureVisible(rect.center().x(), rect.center().y(), 0, rect.height())

    def _toggle_pin(self, place: str, command: Command) -> str:
        if self.shell is None:
            return ""
        message = self.shell.toggle_pin(place, command.kind, command.title, command.target)  # type: ignore[attr-defined]
        self.list.invalidate_verbs()
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

    def run(self, command: Command | None = None, variant: str = "") -> None:
        command = command or self.list.current_command()
        if command is None:
            return
        needs_two_step = command.confirm and (
            command.kind != "power"
            or not getattr(getattr(self.ctx, "settings", None), "power_animation", True)
        )
        if needs_two_step and self._armed != command.title:
            self._armed = command.title
            self.status.setText(f"⚠ {command.title}: Enter de novo para confirmar")
            return
        if not variant:
            action = self.list.current_action() or command.run
        else:
            action = {"ctrl": command.ctrl, "shift": command.shift}.get(variant) or command.run
        self._run_action(action)

    def _run_action(self, action: Callable[[], str]) -> None:
        message = action()
        if message == "":
            self.finished.emit()
        elif "Não deu" in message or "não encontrado" in message:
            self.status.setText(message)
        else:
            self.finished.emit()
            toaster = getattr(self.ctx, "toast_requested", None)
            if toaster is not None:
                from ventude.ui.toast import Toast

                toaster.emit(Toast(message, icon="check"))

    def _context(self, command: Command, pos: QPoint) -> None:
        from ventude.ui import brand

        menu = brand.ink_menu(self)
        for icon_name, label_text, action in self._verbs_for(command):
            act = menu.addAction(icons.icon(icon_name, theme.TEXT_2, 15), label_text)
            act.triggered.connect(lambda _c=False, a=action: self._run_action(a))
        menu.exec(pos)

    def handle_key(self, event: QKeyEvent) -> bool:
        key = event.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self.list.step(1 if key == Qt.Key.Key_Down else -1)
            return True
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            delta = (
                -1
                if (key == Qt.Key.Key_Backtab or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
                else 1
            )
            return self.list.cycle_chip(delta)
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
