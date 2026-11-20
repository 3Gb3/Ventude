"""Barra de comandos: abrir atalhos, criar tarefas e compromissos, iniciar timers e trocar de aba.

Abre pelo atalho global (Ctrl + Alt + Espaço), por Ctrl + K ou ao começar a digitar no painel.
Exemplos: "github", "t: revisar PR", "a: call 15h", "a: dentista amanhã 9h-10h",
"timer 15m", "pomodoro", "clima", "cor", "ocr".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from ventude.core.config import MODULES
from ventude.services import launcher
from ventude.services.timer import countdown, parse_duration
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.pages.agenda import parse_times
from ventude.ui.widgets import label

ACTION_ROLE = Qt.ItemDataRole.UserRole
TIME_AT_END = re.compile(
    r"\s+(\d{1,2}(?:[:h]\d{0,2})?(?:\s*-\s*\d{1,2}(?:[:h]\d{0,2})?)?)\s*$", re.IGNORECASE
)
TAB_WORDS = {
    "home": ("inicio", "início", "home"),
    "limits": ("ias", "ia", "limites", "claude", "gemini", "grok"),
    "shortcuts": ("atalhos", "apps"),
    "agenda": ("agenda", "calendario", "calendário"),
    "tasks": ("tarefas", "todo"),
    "weather": ("clima", "tempo", "previsao", "previsão"),
    "market": ("mercado", "ações", "acoes", "bolsa", "dolar", "dólar"),
    "news": ("notícias", "noticias", "news"),
    "shelf": ("estante", "shelf"),
    "settings": ("ajustes", "configurações", "configuracoes", "settings"),
}


@dataclass
class Command:
    icon: str
    title: str
    subtitle: str
    run: Callable[[], str]  # devolve a mensagem de confirmação ("" fecha sem mensagem)


def parse_event(text: str, today: date | None = None) -> tuple[str, date, object, object]:
    """'call 15h' / 'dentista amanhã 9h-10h' -> (título, dia, início, fim)."""
    today = today or date.today()
    day = today
    words = text.split()
    for word in ("amanhã", "amanha"):
        if word in [w.lower() for w in words]:
            day = today + timedelta(days=1)
            words = [w for w in words if w.lower() != word]
    for word in ("hoje",):
        words = [w for w in words if w.lower() != word]
    text = " ".join(words)
    start = end = None
    if match := TIME_AT_END.search(text):
        try:
            start, end = parse_times(match.group(1))
            text = text[: match.start()].strip()
        except ValueError:
            start = end = None
    return text.strip(), day, start, end


class CommandBar(QFrame):
    """Campo flutuante no topo do painel, com resultados que mudam enquanto você digita."""

    navigate = Signal(str)
    tool = Signal(str)  # "color" | "ocr"
    closed = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("CommandBar")
        self.setStyleSheet(
            f"QFrame#CommandBar {{ background: {theme.SURFACE_2}; border: 1px solid {theme.STROKE_2};"
            " border-radius: 16px; }"
            f"QListWidget::item {{ padding: 7px 8px; }}"
            f"QListWidget::item:selected {{ background: {theme.SURFACE_3}; }}"
        )
        self.input = QLineEdit()
        self.input.setPlaceholderText("Abrir atalho, t: tarefa, a: compromisso 15h, timer 15m, pomodoro…")
        self.input.addAction(icons.icon("search", theme.TEXT_2, 15), QLineEdit.ActionPosition.LeadingPosition)
        self.input.textChanged.connect(self._refresh)
        self.input.installEventFilter(self)
        self.results = QListWidget()
        self.results.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.results.itemClicked.connect(lambda item: self._run(item))
        self.status = label("", "Faint")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(6)
        layout.addWidget(self.input)
        layout.addWidget(self.results)
        layout.addWidget(self.status)
        self._close_timer = QTimer(self, singleShot=True, interval=1100)
        self._close_timer.timeout.connect(self.close_bar)
        self.hide()

    # ---------- abrir e fechar ----------
    def open_bar(self, text: str = "") -> None:
        self._close_timer.stop()
        parent = self.parentWidget()
        width = min(560, parent.width() - 40)
        self.setGeometry((parent.width() - width) // 2, 50, width, 0)
        self.show()
        self.raise_()
        self.input.setText(text)
        self.input.setFocus()
        self._refresh()

    def close_bar(self) -> None:
        if self.isVisible():
            self.hide()
            self.input.clear()
            self.closed.emit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.input and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.close_bar()
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.results.count():
                step = 1 if key == Qt.Key.Key_Down else -1
                row = (self.results.currentRow() + step) % self.results.count()
                self.results.setCurrentRow(row)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.results.currentItem() or self.results.item(0)
                if item:
                    self._run(item)
                return True
        return super().eventFilter(watched, event)

    # ---------- resultados ----------
    def commands(self, text: str) -> list[Command]:
        text = text.strip()
        lowered = text.lower()
        out: list[Command] = []
        if match := re.match(r"^(t:|tarefa\s)(.+)$", text, re.IGNORECASE):
            body = match.group(2).strip()
            if body:
                out.append(
                    Command("check-square", f"Criar tarefa: {body}", "Tarefas", lambda b=body: self._task(b))
                )
        if match := re.match(r"^(a:|agenda\s)(.+)$", text, re.IGNORECASE):
            title, day, start, end = parse_event(match.group(2))
            if title:
                when = "hoje" if day == date.today() else "amanhã"
                if start:
                    when += f" · {start:%H:%M}" + (f"–{end:%H:%M}" if end else "")
                out.append(
                    Command(
                        "calendar",
                        f"Agendar: {title}",
                        when,
                        lambda t=title, d=day, s=start, e=end: self._event(t, d, s, e),
                    )
                )
        if match := re.match(r"^(timer|t)\s+(.+)$", text, re.IGNORECASE):
            seconds = parse_duration(match.group(2))
            if seconds:
                out.append(
                    Command(
                        "clock",
                        f"Timer de {countdown(seconds)}",
                        "Aparece na ilha",
                        lambda secs=seconds: self._timer(secs),
                    )
                )
        if lowered in ("pomodoro", "foco"):
            out.append(Command("clock", "Pomodoro: 25 min de foco + 5 de pausa", "Timer", self._pomodoro))
        if lowered in ("parar", "parar timer", "stop") and self.ctx.timer.state:
            out.append(Command("close", "Parar o timer", "Timer", self._stop_timer))
        if lowered in ("cor", "conta-gotas", "contagotas", "color"):
            out.append(
                Command(
                    "droplet",
                    "Conta-gotas",
                    "Copia a cor de qualquer ponto da tela",
                    lambda: self._tool("color"),
                )
            )
        if lowered in ("ocr", "texto", "capturar texto"):
            out.append(
                Command(
                    "search",
                    "Capturar texto da tela (OCR)",
                    "Copia o texto da região escolhida",
                    lambda: self._tool("ocr"),
                )
            )
        if lowered:
            enabled = {k for k, _, _ in MODULES if self.ctx.settings.module_enabled(k)}
            for key, tab_title, icon_name in MODULES:
                words = TAB_WORDS.get(key, ())
                if key in enabled and any(w.startswith(lowered) or lowered.startswith(w) for w in words):
                    out.append(Command(icon_name, f"Abrir {tab_title}", "Aba", lambda k=key: self._tab(k)))
            for shortcut in self.ctx.store.shortcuts():
                if lowered in shortcut.label.lower() or lowered in shortcut.target.lower():
                    out.append(
                        Command(
                            "globe" if shortcut.kind == "url" else "external",
                            shortcut.label,
                            shortcut.target,
                            lambda s=shortcut: self._shortcut(s.kind, s.target, s.label),
                        )
                    )
        return out[:7]

    def _refresh(self) -> None:
        self.results.clear()
        self.status.setText("")
        commands = self.commands(self.input.text())
        for command in commands:
            item = QListWidgetItem(
                icons.icon(command.icon, theme.TEXT_2, 16), f"{command.title}   ·   {command.subtitle}"
            )
            item.setData(ACTION_ROLE, command)
            self.results.addItem(item)
        if commands:
            self.results.setCurrentRow(0)
        elif self.input.text().strip():
            self.status.setText(
                "Nada encontrado. Tente t: tarefa, a: compromisso 15h, timer 10m ou o nome de um atalho."
            )
        else:
            self.status.setText("Enter abre o primeiro resultado · ↑↓ escolhem · Esc fecha")
        rows = min(self.results.count(), 7)
        self.results.setFixedHeight(rows * 34 + (4 if rows else 0))
        self.results.setVisible(bool(rows))
        self.adjustSize()
        self.resize(self.width(), self.sizeHint().height())

    def _run(self, item: QListWidgetItem) -> None:
        command: Command = item.data(ACTION_ROLE)
        message = command.run()
        if message:
            self.results.clear()
            self.results.hide()
            self.status.setText(f"✓ {message}")
            self.adjustSize()
            self._close_timer.start()
        else:
            self.close_bar()

    # ---------- ações ----------
    def _task(self, text: str) -> str:
        lists = self.ctx.store.lists()
        list_id = lists[0].id if lists else self.ctx.store.add_list("Geral").id
        self.ctx.store.add_item(list_id, text)
        self.ctx.data_changed.emit("tasks")
        return f"Tarefa criada: {text}"

    def _event(self, title: str, day: date, start: object, end: object) -> str:
        self.ctx.store.add_event(title, day, start, end)  # type: ignore[arg-type]
        self.ctx.data_changed.emit("events")
        return f"Agendado: {title}"

    def _timer(self, seconds: float) -> str:
        self.ctx.timer.start(seconds)
        return f"Timer de {countdown(seconds)} iniciado"

    def _pomodoro(self) -> str:
        self.ctx.timer.start_pomodoro()
        return "Pomodoro iniciado: 25 min de foco"

    def _stop_timer(self) -> str:
        self.ctx.timer.stop()
        return "Timer parado"

    def _tool(self, name: str) -> str:
        self.close_bar()
        self.tool.emit(name)
        return ""

    def _tab(self, key: str) -> str:
        self.close_bar()
        self.navigate.emit(key)
        return ""

    def _shortcut(self, kind: str, target: str, name: str) -> str:
        ok, error = launcher.open_target(kind, target)
        return f"Abrindo {name}" if ok else f"Não deu para abrir: {error}"
