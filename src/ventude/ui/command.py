"""Busca universal do Ventude: o motor (CommandEngine) e a barra de comandos dentro da ilha (CommandBar).

O motor é o mesmo da tela de Pesquisa e do Menu Iniciar do Ventude Shell. Exemplos: "github", "code"
(apps), "/p projetos" (só pastas), "1450 * 5,85" (calculadora), "t: revisar PR", "a: call 15h",
"timer 15m", "pomodoro", "qr https://...", "cafeina 2h", "wi-fi" (Configurações), "reiniciar", "cor", "ocr".

Pastas: Enter abre no Explorer, Ctrl + Enter abre o Terminal nela e Shift + Enter, o Cursor/VS Code.
Reiniciar, desligar e sair da sessão pedem Enter duas vezes.
"""

from __future__ import annotations

import re
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import quote_plus

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
from ventude.services import apps, calc, indexer, launcher, power
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
KIND_LABELS = {
    "app": "Aplicativo",
    "uwp": "Aplicativo",
    "folder": "Pasta",
    "file": "Arquivo",
    "system": "Sistema",
    "url": "Site",
    "web": "Web",
}
SECTION_ORDER = ("Apps", "Configurações", "Pastas", "Arquivos", "Atalhos", "Ventude", "Energia", "Web")


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
    kind: str = ""  # app | uwp | folder | file | system | url | web | ventude | calc | power
    target: str = ""  # o que fixar/guardar no histórico (caminho, AppID, ms-settings:, link)
    section: str = "Ventude"

    @property
    def pinnable(self) -> bool:
        return self.kind in ("app", "uwp", "folder", "file", "system", "url") and bool(self.target)


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


class CommandEngine(QObject):
    """Monta os resultados da busca e executa as ações (usado pela ilha, pela Pesquisa e pelo Iniciar)."""

    navigate = Signal(str)  # abrir uma aba da ilha
    tool = Signal(str)  # "color" | "ocr"
    dismiss = Signal()  # a ação abriu outra coisa: quem mostra a busca pode fechar

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx

    # ---------- resultados ----------
    def commands(
        self, text: str, start_mode: bool = False, limit: int = MAX_RESULTS, shell: bool = False
    ) -> list[Command]:
        text = text.strip()
        lowered = text.lower()
        out: list[Command] = []
        if not text:
            return self.start_suggestions(limit) if start_mode else []
        if result := calc.calculate(text):
            out.append(
                Command(
                    "calculator",
                    f"= {result}",
                    "Enter copia o resultado",
                    lambda r=result: self._copy(r),
                    kind="calc",
                    section="Calculadora",
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
                    kind="power",
                    section="Energia",
                )
            )
        enabled = {k for k, _, _ in MODULES if self.ctx.settings.module_enabled(k)}
        tabs: list[Command] = []
        for key, tab_title, icon_name in MODULES:
            words = TAB_WORDS.get(key, ())
            if key in enabled and any(w.startswith(lowered) or lowered.startswith(w) for w in words):
                tabs.append(
                    Command(icon_name, f"Abrir {tab_title}", "Aba do Ventude", lambda k=key: self._tab(k))
                )
        if not shell:
            out += tabs  # na ilha, as abas vêm antes; na Pesquisa do Shell, depois dos apps
        for shortcut in self.ctx.store.shortcuts():
            if lowered in shortcut.label.lower() or lowered in shortcut.target.lower():
                out.append(self._shortcut_command(shortcut))
        if len(text) >= 2:
            entries = self.ctx.indexer.search(text, max(limit, MAX_RESULTS))
            found = [self._entry_command(e) for e in entries]
            app_titles = {c.title.lower() for c in found if c.kind in ("app", "uwp")}
            out += [c for c in found if c.kind in ("app", "uwp")]
            if shell or start_mode:
                out += [
                    self._system_command(item)
                    for item in apps.match_system(text)
                    if item.title.lower() not in app_titles
                ]
            out += [c for c in found if c.kind not in ("app", "uwp")]
        if shell:
            out += tabs
            if len(text) >= 2:
                out.append(self._web_command(text))
        seen: set[tuple[str, str]] = set()
        unique = []
        for command in out:  # o mesmo app pode vir dos atalhos e do índice
            key = (command.title.lower(), command.path.lower())
            if key not in seen:
                seen.add(key)
                unique.append(command)
        return unique[:limit]

    def start_suggestions(self, limit: int = MAX_RESULTS) -> list[Command]:
        """Busca vazia: os atalhos fixados."""
        return [self._shortcut_command(s) for s in self.ctx.store.shortcuts()[:limit]]

    def _shortcut_command(self, shortcut: object) -> Command:
        kind, target, name = shortcut.kind, shortcut.target, shortcut.label  # type: ignore[attr-defined]
        command = Command(
            "globe" if kind == "url" else ("folder" if kind == "folder" else "external"),
            name,
            target,
            lambda: self._shortcut(kind, target, name),
            path="" if kind == "url" else target,
            kind=kind if kind in ("app", "folder", "file", "url") else "app",
            target=target,
            section="Atalhos",
        )
        if kind == "folder":
            self._folder_actions(command, target)
        return command

    def _entry_command(self, entry: indexer.Entry) -> Command:
        subtitle = {"app": "Aplicativo", "uwp": "Aplicativo", "folder": entry.path, "file": entry.path}[
            entry.kind
        ]
        command = Command(
            {"app": "apps", "uwp": "apps", "folder": "folder", "file": "file"}[entry.kind],
            entry.name,
            subtitle,
            lambda e=entry: self._launch(e),
            path="" if entry.kind == "uwp" else entry.path,
            kind=entry.kind,
            target=entry.path,
            section={"app": "Apps", "uwp": "Apps", "folder": "Pastas", "file": "Arquivos"}[entry.kind],
        )
        if entry.is_folder:
            self._folder_actions(command, entry.path)
        return command

    def _system_command(self, item: apps.SystemItem) -> Command:
        return Command(
            item.icon,
            item.title,
            "Sistema",
            lambda i=item: self._system(i),
            kind="system",
            target=item.target,
            section="Configurações",
        )

    def _web_command(self, text: str) -> Command:
        return Command(
            "globe",
            f"Pesquisar na web: {text}",
            "Abre no navegador",
            lambda t=text: self._web(t),
            kind="web",
            target=text,
            section="Web",
        )

    def _folder_actions(self, command: Command, folder: str) -> None:
        command.ctrl = lambda f=folder: self._terminal(f)
        command.shift = lambda f=folder: self._editor(f)
        command.actions = [
            ("terminal", "Abrir no Terminal (Ctrl + Enter)", command.ctrl),
            ("code", "Abrir no Cursor / VS Code (Shift + Enter)", command.shift),
        ]

    # ---------- ações ----------
    def _remember(self, kind: str, label: str, target: str) -> None:
        try:
            self.ctx.store.record_launch(kind, label, target)
        except Exception:  # noqa: BLE001 - histórico nunca atrapalha abrir
            pass

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
        self.dismiss.emit()
        self.tool.emit(name)
        return ""

    def _tab(self, key: str) -> str:
        self.dismiss.emit()
        self.navigate.emit(key)
        return ""

    def _copy(self, text: str) -> str:
        QGuiApplication.clipboard().setText(text)
        return f"{text} copiado"

    def _qr(self, text: str) -> str:
        self.dismiss.emit()
        self.ctx.qr_requested.emit(text)
        return ""

    def _caffeine(self, seconds: float | None) -> str:
        self.ctx.caffeine.start(seconds)
        return self.ctx.caffeine.describe()

    def _caffeine_off(self) -> str:
        self.ctx.caffeine.stop()
        return "Cafeína desligada"

    def _power(self, key: str) -> str:
        ok, message = self.ctx.request_power(key)
        if self.ctx.power_fx is not None:
            self.dismiss.emit()
            return ""
        if ok:
            return f"{power.BY_KEY[key].title}…" if key not in ("lock", "sleep", "hibernate") else ""
        return f"Não deu: {message}"

    def _shortcut(self, kind: str, target: str, name: str) -> str:
        ok, error = launcher.open_target(kind, target)
        if ok:
            self._remember(kind if kind in KIND_LABELS else "app", name, target)
        return f"Abrindo {name}" if ok else f"Não deu para abrir: {error}"

    def _launch(self, entry: indexer.Entry) -> str:
        ok, error = indexer.launch(entry)
        if ok:
            self._remember(entry.kind, entry.name, entry.path)
        return f"Abrindo {entry.name}" if ok else f"Não deu para abrir: {error}"

    def _system(self, item: apps.SystemItem) -> str:
        ok, error = apps.open_system(item.target)
        if ok:
            self._remember("system", item.title, item.target)
        return f"Abrindo {item.title}" if ok else f"Não deu para abrir: {error}"

    def _web(self, text: str) -> str:
        webbrowser.open(f"https://www.google.com/search?q={quote_plus(text)}")
        return "Abrindo o navegador"

    def _terminal(self, folder: str) -> str:
        ok, error = indexer.open_terminal(folder)
        return "Terminal aberto na pasta" if ok else f"Não deu para abrir o Terminal: {error}"

    def _editor(self, folder: str) -> str:
        ok, name = indexer.open_in_editor(folder)
        return f"Abrindo no {name}" if ok else name


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
        self.engine = CommandEngine(ctx, self)
        self.engine.navigate.connect(self.navigate.emit)
        self.engine.tool.connect(self.tool.emit)
        self.engine.dismiss.connect(self.close_bar)
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
        return self.engine.commands(text, self.start_mode)

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
        if not self.isVisible():
            return  # a ação já fechou a barra (abriu uma aba, uma ferramenta...)
        if message:
            self.results.clear()
            self.results.hide()
            self.status.setText(f"✓ {message}")
            self.resize(getattr(self, "_width", self.width()), self.sizeHint().height())
            self._close_timer.start()
        else:
            self.close_bar()
