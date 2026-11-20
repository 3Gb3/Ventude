"""Barra de comandos / Menu Iniciar do Ventude: busca universal e ações rápidas.

Abre pelo atalho global (Ctrl + Alt + Espaço), pela tecla Windows (se ligado), por Ctrl + K ou ao
começar a digitar no painel. Exemplos: "github", "code" (apps), "/p projetos" (só pastas),
"1450 * 5,85" (calculadora), "t: revisar PR", "a: call 15h", "timer 15m", "pomodoro",
"qr https://...", "cafeina 2h", "reiniciar", "cor", "ocr".

Pastas: Enter abre no Explorer, Ctrl + Enter abre o Terminal nela e Shift + Enter, o Cursor/VS Code.
Reiniciar, desligar e sair da sessão pedem Enter duas vezes.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from PySide6.QtCore import QEvent, QFileInfo, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon, QKeyEvent
from PySide6.QtWidgets import (
    QFileIconProvider,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ventude.core.config import MODULES
from ventude.services import calc, indexer, launcher, power
from ventude.services.timer import countdown, parse_duration
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.pages.agenda import parse_times
from ventude.ui.widgets import ElidedLabel, IconButton, hbox, label

ACTION_ROLE = Qt.ItemDataRole.UserRole
MAX_RESULTS = 8
ROW_HEIGHT = 40
TIME_AT_END = re.compile(
    r"\s+(\d{1,2}(?:[:h]\d{0,2})?(?:\s*-\s*\d{1,2}(?:[:h]\d{0,2})?)?)\s*$", re.IGNORECASE
)
TAB_WORDS = {
    "home": ("inicio", "início", "home"),
    "limits": ("ias", "ia", "limites", "claude", "gemini", "grok"),
    "hardware": ("sistema", "cpu", "gpu", "ram", "memória", "memoria", "rede", "hardware", "ping"),
    "audio": ("áudio", "audio", "som", "volume", "microfone", "mixer", "fones"),
    "shortcuts": ("atalhos", "apps"),
    "agenda": ("agenda", "calendario", "calendário"),
    "tasks": ("tarefas", "todo"),
    "weather": ("clima", "tempo", "previsao", "previsão"),
    "market": ("mercado", "ações", "acoes", "bolsa", "dolar", "dólar"),
    "news": ("notícias", "noticias", "news"),
    "shelf": ("estante", "shelf", "copiados"),
    "settings": ("ajustes", "configurações", "configuracoes", "settings"),
}
CAFFEINE_WORDS = ("cafeina", "cafeína", "caffeine", "acordado")


@dataclass
class Command:
    icon: str
    title: str
    subtitle: str
    run: Callable[[], str]  # devolve a mensagem de confirmação ("" fecha sem mensagem)
    ctrl: Callable[[], str] | None = None  # Ctrl + Enter
    shift: Callable[[], str] | None = None  # Shift + Enter
    actions: list[tuple[str, str, Callable[[], str]]] = field(default_factory=list)  # botões na linha
    confirm: bool = False  # destrutivo: pede Enter de novo
    path: str = ""  # para o ícone real do app/pasta/arquivo


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


def parse_caffeine(text: str) -> tuple[bool, float | None] | None:
    """'cafeina' -> (ligar, sem fim); 'cafeina 30m' -> (ligar, 1800 s); 'cafeina off' -> (desligar, None)."""
    lowered = text.strip().lower()
    for word in CAFFEINE_WORDS:
        if lowered == word or lowered.startswith(word + " "):
            rest = lowered[len(word) :].strip()
            if rest in ("off", "parar", "desligar", "não", "nao"):
                return False, None
            if not rest or rest in ("on", "ligar"):
                return True, None
            seconds = parse_duration(rest)
            return (True, float(seconds)) if seconds else None
    if lowered in ("parar cafeina", "parar cafeína", "desligar cafeina", "desligar cafeína"):
        return False, None
    return None


class ResultRow(QWidget):
    """Uma linha de resultado: ícone, título, detalhe e botões de ação (Terminal, editor...)."""

    def __init__(self, command: Command, icon: QIcon, run: Callable[[Callable[[], str]], None]) -> None:
        super().__init__()
        glyph = QLabel()
        glyph.setFixedSize(20, 20)
        glyph.setPixmap(icon.pixmap(QSize(18, 18)))
        title = ElidedLabel(command.title, "")
        title.setStyleSheet("font-size: 13px;")
        sub = ElidedLabel(command.subtitle, "Faint")
        sub.ensurePolished()
        sub.setFixedWidth(min(260, sub.fontMetrics().horizontalAdvance(command.subtitle) + 6))
        layout = hbox(glyph, title, sub, spacing=10, margins=0)
        layout.setStretch(1, 1)
        for icon_name, tip, action in command.actions:
            button = IconButton(icon_name, tip, 26, 14)
            button.clicked.connect(lambda _c=False, a=action: run(a))
            layout.addWidget(button)
        layout.setContentsMargins(4, 0, 4, 0)
        self.setLayout(layout)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


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
            f"QListWidget::item {{ padding: 0px; border-radius: 10px; }}"
            f"QListWidget::item:selected {{ background: {theme.SURFACE_3}; }}"
        )
        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "Buscar apps, pastas e arquivos · t: tarefa · 2^10 · qr link · cafeina 1h…"
        )
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
        self._armed = ""
        self.start_mode = False
        self._file_icons: QFileIconProvider | None = None
        self._icon_cache: dict[str, QIcon] = {}
        ctx.indexer.ready.connect(lambda _n: self.isVisible() and self._refresh())
        self.hide()

    # ---------- abrir e fechar ----------
    def open_bar(self, text: str = "", start: bool = False) -> None:
        self._close_timer.stop()
        self.start_mode = start
        parent = self.parentWidget()
        self._width = min(620, parent.width() - 40)
        self.setGeometry((parent.width() - self._width) // 2, 50, self._width, 0)
        self.show()
        self.raise_()
        self.input.setText(text)
        self.input.setFocus()
        self._refresh()

    def close_bar(self) -> None:
        if self.isVisible():
            self.hide()
            self.input.clear()
            self._armed = ""
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
                    mods = event.modifiers()
                    variant = (
                        "ctrl"
                        if mods & Qt.KeyboardModifier.ControlModifier
                        else "shift"
                        if mods & Qt.KeyboardModifier.ShiftModifier
                        else ""
                    )
                    self._run(item, variant)
                return True
        return super().eventFilter(watched, event)

    # ---------- resultados ----------
    def commands(self, text: str) -> list[Command]:
        text = text.strip()
        lowered = text.lower()
        out: list[Command] = []
        if not text:
            return self._start_suggestions() if self.start_mode else []
        if result := calc.calculate(text):
            out.append(
                Command(
                    "calculator", f"= {result}", "Enter copia o resultado", lambda r=result: self._copy(r)
                )
            )
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
                        lambda s=seconds: self._timer(s),
                    )
                )
        if match := re.match(r"^qr\s+(.+)$", text, re.IGNORECASE | re.DOTALL):
            body = match.group(1).strip()
            out.append(Command("qr", "Mostrar QR Code", body[:60], lambda b=body: self._qr(b)))
        caffeine = parse_caffeine(text)
        if caffeine is not None:
            on, seconds = caffeine
            if on:
                when = f"por {countdown(seconds)}" if seconds else "até você desligar"
                out.append(
                    Command(
                        "coffee", f"Cafeína {when}", "Tela sempre acesa", lambda s=seconds: self._caffeine(s)
                    )
                )
            else:
                out.append(Command("coffee", "Desligar a Cafeína", "O PC volta a dormir", self._caffeine_off))
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
        for action in power.matching(text):
            if action.needs_update and not self.ctx.power.pending:
                continue
            out.append(
                Command(
                    action.icon,
                    action.title,
                    "Energia · Enter duas vezes para confirmar" if action.destructive else "Energia",
                    lambda k=action.key: self._power(k),
                    confirm=action.destructive,
                )
            )
        enabled = {k for k, _, _ in MODULES if self.ctx.settings.module_enabled(k)}
        for key, tab_title, icon_name in MODULES:
            words = TAB_WORDS.get(key, ())
            if key in enabled and any(w.startswith(lowered) or lowered.startswith(w) for w in words):
                out.append(Command(icon_name, f"Abrir {tab_title}", "Aba", lambda k=key: self._tab(k)))
        for shortcut in self.ctx.store.shortcuts():
            if lowered in shortcut.label.lower() or lowered in shortcut.target.lower():
                out.append(self._shortcut_command(shortcut))
        if len(text) >= 2:
            out += [self._entry_command(e) for e in self.ctx.indexer.search(text, MAX_RESULTS)]
        seen: set[tuple[str, str]] = set()
        unique = []
        for command in out:  # o mesmo app pode vir dos atalhos e do índice
            key = (command.title.lower(), command.path.lower())
            if key not in seen:
                seen.add(key)
                unique.append(command)
        return unique[:MAX_RESULTS]

    def _start_suggestions(self) -> list[Command]:
        """Menu Iniciar vazio: os atalhos fixados."""
        return [self._shortcut_command(s) for s in self.ctx.store.shortcuts()[:MAX_RESULTS]]

    def _shortcut_command(self, shortcut: object) -> Command:
        kind, target, name = shortcut.kind, shortcut.target, shortcut.label  # type: ignore[attr-defined]
        command = Command(
            "globe" if kind == "url" else ("folder" if kind == "folder" else "external"),
            name,
            target,
            lambda: self._shortcut(kind, target, name),
            path="" if kind == "url" else target,
        )
        if kind == "folder":
            self._folder_actions(command, target)
        return command

    def _entry_command(self, entry: indexer.Entry) -> Command:
        subtitle = {"app": "App", "uwp": "App da Loja", "folder": entry.path, "file": entry.path}[entry.kind]
        command = Command(
            {"app": "apps", "uwp": "apps", "folder": "folder", "file": "file"}[entry.kind],
            entry.name,
            subtitle,
            lambda e=entry: self._launch(e),
            path="" if entry.kind == "uwp" else entry.path,
        )
        if entry.is_folder:
            self._folder_actions(command, entry.path)
        return command

    def _folder_actions(self, command: Command, folder: str) -> None:
        command.ctrl = lambda f=folder: self._terminal(f)
        command.shift = lambda f=folder: self._editor(f)
        command.actions = [
            ("terminal", "Abrir no Terminal (Ctrl + Enter)", command.ctrl),
            ("code", "Abrir no Cursor / VS Code (Shift + Enter)", command.shift),
        ]

    def _icon(self, command: Command) -> QIcon:
        if command.path and command.icon in ("apps", "external", "folder", "file"):
            if command.path not in self._icon_cache:
                if self._file_icons is None:
                    self._file_icons = QFileIconProvider()
                info = QFileInfo(command.path)
                icon = self._file_icons.icon(info) if info.exists() else QIcon()
                self._icon_cache[command.path] = icon
            if not self._icon_cache[command.path].isNull():
                return self._icon_cache[command.path]
        return icons.icon(command.icon, theme.TEXT_2, 18)

    def _refresh(self) -> None:
        self._armed = ""
        self.results.clear()
        self.status.setText("")
        commands = self.commands(self.input.text())
        for command in commands:
            item = QListWidgetItem()
            item.setData(ACTION_ROLE, command)
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            self.results.addItem(item)
            self.results.setItemWidget(item, ResultRow(command, self._icon(command), self._run_action))
        if commands:
            self.results.setCurrentRow(0)
            first = commands[0]
            if first.ctrl or first.shift:
                self.status.setText("Enter abre · Ctrl + Enter: Terminal · Shift + Enter: Cursor/VS Code")
        elif self.input.text().strip():
            self.status.setText(
                "Nada encontrado. Tente o nome de um app ou pasta, /p pasta, t: tarefa, 2^10…"
            )
        else:
            self.status.setText("Digite para buscar apps, pastas e arquivos · ↑↓ escolhem · Esc fecha")
        rows = min(self.results.count(), MAX_RESULTS)
        self.results.setFixedHeight(rows * ROW_HEIGHT + (6 if rows else 0))
        self.results.setVisible(bool(rows))
        self.resize(getattr(self, "_width", self.width()), self.sizeHint().height())

    def _run(self, item: QListWidgetItem, variant: str = "") -> None:
        command: Command = item.data(ACTION_ROLE)
        if command.confirm and self._armed != command.title:
            self._armed = command.title
            self.status.setText(f"⚠ {command.title}: pressione Enter de novo para confirmar")
            return
        action = {"ctrl": command.ctrl, "shift": command.shift}.get(variant) or command.run
        self._run_action(action)

    def _run_action(self, action: Callable[[], str]) -> None:
        self._armed = ""
        message = action()
        if message:
            self.results.clear()
            self.results.hide()
            self.status.setText(f"✓ {message}")
            self.resize(getattr(self, "_width", self.width()), self.sizeHint().height())
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

    def _copy(self, text: str) -> str:
        QGuiApplication.clipboard().setText(text)
        return f"{text} copiado"

    def _qr(self, text: str) -> str:
        self.close_bar()
        self.ctx.qr_requested.emit(text)
        return ""

    def _caffeine(self, seconds: float | None) -> str:
        self.ctx.caffeine.start(seconds)
        return self.ctx.caffeine.describe()

    def _caffeine_off(self) -> str:
        self.ctx.caffeine.stop()
        return "Cafeína desligada"

    def _power(self, key: str) -> str:
        ok, message = power.run(key)
        if ok:
            return f"{power.BY_KEY[key].title}…" if key not in ("lock", "sleep", "hibernate") else ""
        return f"Não deu: {message}"

    def _shortcut(self, kind: str, target: str, name: str) -> str:
        ok, error = launcher.open_target(kind, target)
        return f"Abrindo {name}" if ok else f"Não deu para abrir: {error}"

    def _launch(self, entry: indexer.Entry) -> str:
        ok, error = indexer.launch(entry)
        return f"Abrindo {entry.name}" if ok else f"Não deu para abrir: {error}"

    def _terminal(self, folder: str) -> str:
        ok, error = indexer.open_terminal(folder)
        return "Terminal aberto na pasta" if ok else f"Não deu para abrir o Terminal: {error}"

    def _editor(self, folder: str) -> str:
        ok, name = indexer.open_in_editor(folder)
        return f"Abrindo no {name}" if ok else name
