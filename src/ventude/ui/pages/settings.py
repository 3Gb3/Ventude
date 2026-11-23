"""Ajustes: tudo salvo na hora, sem botão de salvar."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ventude import __version__
from ventude.core import updates
from ventude.core.config import LOCK_MAX_MIN, LOCK_PRESETS_MIN, MODULES, Settings
from ventude.core.paths import app_data_dir
from ventude.limits import claude, codex, copilot, cursor, grok
from ventude.limits.model import PROVIDERS
from ventude.services import system
from ventude.services.news import FEEDS
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.lockscreen import timeout_label
from ventude.ui.pages.base import Page, scroller
from ventude.ui.widgets import Card, Switch, hbox, label, repolish, vbox

DELAYS = [
    ("Na hora", 150),
    ("Meio segundo", 500),
    ("1 segundo", 1000),
    ("2 segundos", 2000),
    ("5 segundos", 5000),
]
HOVER_DELAYS = [("Na hora", 0), ("Bem rápido (0,15 s)", 150), ("0,3 segundo", 300), ("0,6 segundo", 600)]
ANIMATIONS = [
    ("Rápida", "rapida"),
    ("Normal", "normal"),
    ("Suave", "suave"),
    ("Nenhuma", "nenhuma"),
]
DOCK_MODE_OPTIONS = [
    ("Esquivar janelas", "esquivar"),
    ("Sempre visível", "sempre"),
    ("Oculta (só com o mouse embaixo)", "oculta"),
]
MEDIA_SOURCES = [("Só o Spotify", "spotify"), ("Qualquer player", "any")]
INTERVALS = [("2 min", 2), ("5 min", 5), ("10 min", 10), ("15 min", 15), ("30 min", 30)]
CLAUDE_MODES = [
    ("Automático", "auto"),
    ("Só Claude Code", "code"),
    ("Só claude.ai", "web"),
    ("Desligado", "off"),
]
ANTIGRAVITY_MODES = [("Automático", "auto"), ("Desligado", "off")]
GROK_MODES = [("Automático", "auto"), ("Só Grok CLI", "cli"), ("Só grok.com", "web"), ("Desligado", "off")]


def combo(options: list[tuple[str, object]], current: object) -> QComboBox:
    box = QComboBox()
    box.setCursor(Qt.CursorShape.PointingHandCursor)
    box.setMinimumWidth(170)
    for text, value in options:
        box.addItem(text, value)
    index = next((i for i, (_, v) in enumerate(options) if v == current), 0)
    box.setCurrentIndex(index)
    return box


def setting_row(title: str, hint: str, control: QWidget) -> QWidget:
    row = QWidget()
    text = vbox(label(title, ""), spacing=1)
    if hint:
        text.addWidget(label(hint, "Faint", wrap=True))
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 4, 0, 4)
    layout.setSpacing(16)
    layout.addLayout(text, 1)
    layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


def section(title: str, dot: str = "") -> tuple[Card, QVBoxLayout]:
    card = Card(margins=(18, 14, 18, 14), spacing=6)
    heading = label(title, "H2")
    if dot:
        mark = label()
        mark.setFixedSize(10, 10)
        mark.setStyleSheet(f"background: {dot}; border-radius: 5px;")
        card.body.addLayout(hbox(mark, heading, None, spacing=8))
    else:
        card.body.addWidget(heading)
    return card, card.body


class SecretField(QWidget):
    """Campo para colar um segredo (fica protegido pelo Windows) e ver se conectou."""

    def __init__(self, ctx: AppContext, name: str, placeholder: str, provider: str) -> None:
        super().__init__()
        self.ctx, self.name, self.provider = ctx, name, provider
        self.input = QLineEdit()
        self.input.setEchoMode(QLineEdit.EchoMode.Password)
        self.hint = placeholder
        self.input.setPlaceholderText(placeholder)
        self.save = QPushButton("Salvar")
        self.save.setObjectName("Primary")
        self.clear = QPushButton("Apagar")
        for button in (self.save, self.clear):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save.clicked.connect(self._save)
        self.clear.clicked.connect(self._clear)
        self.input.returnPressed.connect(self._save)
        self.state = label("", "Faint", wrap=True)
        self.setLayout(vbox(hbox(self.input, self.save, self.clear, spacing=8), self.state, spacing=4))
        ctx.limits.changed.connect(lambda p: p == provider and self._status())
        self._sync()

    def _sync(self) -> None:
        has = bool(self.ctx.store.secret(self.name))
        self.input.clear()
        self.input.setPlaceholderText("Guardado com segurança. Cole outro para trocar." if has else self.hint)
        self.clear.setEnabled(has)
        self._status()

    def _status(self) -> None:
        has = bool(self.ctx.store.secret(self.name))
        report = self.ctx.limits.report(self.provider)
        text, style = "", "Faint"
        if has:
            text = "Guardado e protegido pelo Windows (DPAPI)."
            if self.ctx.limits.busy(self.provider):
                text += " Testando a conexão…"
            elif report.state == "ok" and report.source:
                text += f" Conectado via {report.source}."
            elif report.state in ("error", "stale") and report.message:
                text, style = report.message, "Warn"
        if self.state.objectName() != style:
            self.state.setObjectName(style)
            repolish(self.state)
        self.state.setText(text)
        self.state.setVisible(bool(text))

    def _save(self) -> None:
        value = self.input.text().strip()
        if not value:
            return
        self.ctx.store.set_secret(self.name, value)
        self._sync()
        self.ctx.limits.refresh(self.provider, force=True)

    def _clear(self) -> None:
        self.ctx.store.set_secret(self.name, "")
        self._sync()
        self.ctx.limits.refresh(self.provider, force=True)


AUTO_LABELS = {"antigravity": "Automático (Gemini mais usado)"}


class ProviderTable(QWidget):
    """Quais IAs ficam ligadas, quais têm anel na ilha e qual limite cada anel mostra."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 4, 0, 4)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        for column, title in enumerate(("IA", "Ligada", "Na ilha", "Anel mostra")):
            heading = label(title, "Caption")
            grid.addWidget(heading, 0, column, Qt.AlignmentFlag.AlignLeft)
        self.active: dict[str, Switch] = {}
        self.ring: dict[str, Switch] = {}
        self.limit: dict[str, QComboBox] = {}
        for row, provider in enumerate(ctx.limits.providers(), start=1):
            dot = label()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background: {theme.provider_color(provider)}; border-radius: 5px;")
            name = QWidget()
            name.setLayout(hbox(dot, label(PROVIDERS.get(provider, provider), ""), None, spacing=8))
            active, ring = Switch(), Switch()
            active.setToolTip("Consultar o limite desta IA e mostrar na aba IAs")
            ring.setToolTip("Mostrar o anel desta IA na ilha fechada e no Início")
            limit = QComboBox()
            limit.setCursor(Qt.CursorShape.PointingHandCursor)
            limit.setMinimumWidth(210)
            limit.setToolTip("Qual limite o anel acompanha: sessão de 5h, semana, um modelo...")
            active.toggled.connect(lambda on, p=provider: self._set_active(p, on))
            ring.toggled.connect(lambda on, p=provider: self._set_ring(p, on))
            limit.activated.connect(lambda _i, p=provider: self._set_limit(p))
            grid.addWidget(name, row, 0)
            grid.addWidget(active, row, 1)
            grid.addWidget(ring, row, 2)
            grid.addWidget(limit, row, 3)
            self.active[provider], self.ring[provider], self.limit[provider] = active, ring, limit
        grid.setColumnStretch(0, 1)
        ctx.settings_changed.connect(self.sync)
        ctx.limits.changed.connect(lambda p: p in self.limit and self._fill_limit(p))
        self.sync(ctx.settings)

    def sync(self, settings: Settings) -> None:
        for provider in self.active:
            on = settings.provider_mode(provider) != "off"
            for switch, value in (
                (self.active[provider], on),
                (self.ring[provider], settings.ring_enabled(provider)),
            ):
                switch.blockSignals(True)
                switch.setChecked(value)
                switch.blockSignals(False)
            self.ring[provider].setEnabled(on)
            self.limit[provider].setEnabled(on)
            self._fill_limit(provider)

    def _fill_limit(self, provider: str) -> None:
        box = self.limit[provider]
        pinned = self.ctx.settings.pinned_limit(provider)
        report = self.ctx.limits.report(provider)
        options = [(AUTO_LABELS.get(provider, "Automático (o mais usado)"), "")]
        options += [(w.label, w.key) for w in report.windows]
        if pinned and pinned not in {key for _, key in options}:
            options.append(("Escolhido (sem leitura agora)", pinned))
        current = [(box.itemText(i), box.itemData(i)) for i in range(box.count())]
        box.blockSignals(True)
        if current != options:
            box.clear()
            for text, key in options:
                box.addItem(text, key)
        box.setCurrentIndex(max(0, next((i for i, (_, k) in enumerate(options) if k == pinned), 0)))
        box.blockSignals(False)

    def _set_active(self, provider: str, on: bool) -> None:
        settings = self.ctx.settings
        changes: dict[str, object] = {f"{provider}_mode": "auto" if on else "off"}
        rings = [p for p in self.active if settings.ring_enabled(p)]
        if on and provider not in rings and len(rings) < 4:
            changes["ring_providers"] = settings.with_ring(provider, True)
        self.ctx.update_settings(**changes)
        self.ctx.limits.refresh(provider, force=True)

    def _set_ring(self, provider: str, on: bool) -> None:
        self.ctx.update_settings(ring_providers=self.ctx.settings.with_ring(provider, on))

    def _set_limit(self, provider: str) -> None:
        key = self.limit[provider].currentData() or ""
        self.ctx.update_settings(pinned_limits_json=self.ctx.settings.with_pinned(provider, key))


class SettingsPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 8, 0)
        column.setSpacing(12)
        self.root.addWidget(scroller(content), 1)
        s = ctx.settings
        self._building = True

        # ---------- geral ----------
        card, body = section("Geral")
        self.autostart = Switch()
        self.autostart.setChecked(
            system.autostart_enabled() or (s.start_with_windows and not system.autostart_supported())
        )
        self.autostart.setEnabled(system.autostart_supported())
        self.autostart.toggled.connect(self._autostart)
        body.addWidget(
            setting_row("Iniciar com o Windows", "Abre sozinho ao entrar na sua conta.", self.autostart)
        )
        self.hotkey = self._switch(s.hotkey_enabled, "hotkey_enabled")
        body.addWidget(
            setting_row(
                f"Atalho {system.HOTKEY_LABEL}", "Abre e fecha o painel de qualquer lugar.", self.hotkey
            )
        )
        self.hotkey_command = self._switch(s.hotkey_command, "hotkey_command")
        body.addWidget(
            setting_row(
                "Atalho abre a barra de comandos",
                "Busque atalhos e crie tarefas (t: …), compromissos (a: … 15h) e timers (timer 15m). "
                "Com o painel aberto: Ctrl + K ou comece a digitar.",
                self.hotkey_command,
            )
        )
        self.hover = self._switch(s.hover_open, "hover_open")
        body.addWidget(
            setting_row("Abrir ao passar o mouse", "Desligado: abre só com clique ou atalho.", self.hover)
        )
        self.hover_delay = combo(
            HOVER_DELAYS, min(HOVER_DELAYS, key=lambda d: abs(d[1] - s.hover_delay_ms))[1]
        )
        self.hover_delay.currentIndexChanged.connect(
            lambda: self._set(hover_delay_ms=self.hover_delay.currentData())
        )
        body.addWidget(
            setting_row(
                "Tempo para abrir",
                "Quanto o mouse precisa ficar sobre a ilha. Um pouco de espera evita aberturas sem querer.",
                self.hover_delay,
            )
        )
        self.zone_open = self._switch(s.zone_open, "zone_open")
        body.addWidget(
            setting_row(
                "Abrir na aba apontada",
                "Mouse nos anéis abre a aba IAs; no clima, a aba Clima. Clique na data abre a Agenda.",
                self.zone_open,
            )
        )
        self.fullscreen = self._switch(s.hide_fullscreen, "hide_fullscreen")
        body.addWidget(
            setting_row(
                "Sumir em tela cheia", "Esconde a ilha em jogos, vídeos e apresentações.", self.fullscreen
            )
        )
        self.clock = self._switch(s.clock_24h, "clock_24h")
        body.addWidget(setting_row("Relógio de 24 horas", "", self.clock))
        self.monitor = QComboBox()
        self.monitor.setCursor(Qt.CursorShape.PointingHandCursor)
        self.monitor.setMinimumWidth(170)
        self._fill_monitors()
        self.monitor.currentIndexChanged.connect(self._monitor)
        body.addWidget(setting_row("Monitor", "Onde a ilha aparece.", self.monitor))
        self.delay = combo(DELAYS, min(DELAYS, key=lambda d: abs(d[1] - s.collapse_delay_ms))[1])
        self.delay.currentIndexChanged.connect(lambda: self._set(collapse_delay_ms=self.delay.currentData()))
        body.addWidget(setting_row("Fechar o painel", "Depois que o mouse sai.", self.delay))
        self.anim = combo(ANIMATIONS, s.animation)
        self.anim.currentIndexChanged.connect(lambda: self._set(animation=self.anim.currentData()))
        body.addWidget(setting_row("Animação", "Abrir, fechar e trocar de aba.", self.anim))
        self.media_source = combo(MEDIA_SOURCES, s.media_source)
        self.media_source.currentIndexChanged.connect(
            lambda: self._set(media_source=self.media_source.currentData())
        )
        body.addWidget(
            setting_row(
                "Música",
                "Qual player a ilha mostra e controla. Só o Spotify: ignora vídeos e abas do navegador.",
                self.media_source,
            )
        )
        self.progress = self._switch(s.pill_progress, "pill_progress")
        body.addWidget(
            setting_row(
                "Andamento da música na ilha",
                "Linha fina na base da ilha fechada. Na ilha: clique do meio ou na capa toca/pausa, "
                "a roda do mouse muda o volume e Shift + roda troca de faixa.",
                self.progress,
            )
        )
        self.toasts = self._switch(s.island_toasts, "island_toasts")
        body.addWidget(
            setting_row(
                "Avisos na ilha",
                "Limites das IAs, lembretes e timer aparecem na própria ilha por alguns segundos. "
                "Desligado (ou em tela cheia): vão para a bandeja.",
                self.toasts,
            )
        )
        self.toast_music = self._switch(s.toast_music, "toast_music")
        body.addWidget(
            setting_row("Música nova na ilha", "Mostra capa e nome quando a faixa troca.", self.toast_music)
        )
        self.reminders = self._switch(s.agenda_reminders, "agenda_reminders")
        body.addWidget(
            setting_row(
                "Lembretes da Agenda",
                "Avisa 10 e 5 minutos antes dos compromissos com horário.",
                self.reminders,
            )
        )
        self.weather_pill = self._switch(s.pill_weather, "pill_weather")
        body.addWidget(
            setting_row(
                "Clima na ilha",
                "Ícone e temperatura de agora na ilha fechada (cidade da aba Clima).",
                self.weather_pill,
            )
        )
        self.weather_locate = self._switch(s.weather_auto_locate, "weather_auto_locate")
        body.addWidget(
            setting_row(
                "Achar a cidade sozinho",
                "Sem cidade escolhida na aba Clima, estima pelo seu IP (ipinfo.io) na abertura.",
                self.weather_locate,
            )
        )
        self.pill_date = self._switch(s.pill_date, "pill_date")
        body.addWidget(
            setting_row(
                "Data na ilha",
                "Dia da semana na ilha fechada. Com o Ventude Shell, a data já aparece no relógio da barra.",
                self.pill_date,
            )
        )
        self.clipboard = self._switch(s.clipboard_history, "clipboard_history")
        body.addWidget(
            setting_row(
                "Histórico da área de transferência na Estante",
                "Guarda os últimos 20 textos, links, cores e imagens que você copia (só na memória; fixe o "
                "que quiser manter). Senhas copiadas de gerenciadores ficam de fora.",
                self.clipboard,
            )
        )
        self.privacy = self._switch(s.pill_privacy, "pill_privacy")
        body.addWidget(
            setting_row(
                "Microfone e câmera em uso na ilha",
                "Ponto laranja (microfone) e verde (câmera) com o nome do app. Clique no laranja muta o "
                "microfone.",
                self.privacy,
            )
        )
        self.hw_alerts = self._switch(s.hardware_alerts, "hardware_alerts")
        body.addWidget(
            setting_row(
                "Avisos de temperatura e memória",
                "Avisa na ilha quando a GPU ou o processador passam de 85 °C ou a RAM passa de 92%.",
                self.hw_alerts,
            )
        )
        self.lyrics = self._switch(s.lyrics_enabled, "lyrics_enabled")
        body.addWidget(
            setting_row(
                "Letra da música no Início",
                "Verso atual sincronizado no player. Busca no lrclib.net (envia só título e artista).",
                self.lyrics,
            )
        )
        column.addWidget(card)

        # ---------- Ventude Shell (3.0) ----------
        card, body = section("Ventude Shell", theme.ACCENT)
        self.shell_card = card
        body.addWidget(
            label(
                "Doca flutuante, Lançador unificado e Pesquisa na tecla Windows no lugar da barra "
                "padrão. A barra do Windows sempre volta ao desligar aqui ou fechar o Ventude.",
                "Faint",
                wrap=True,
            )
        )
        windows_only = system.autostart_supported()
        self.replace_taskbar = self._switch(s.replace_taskbar, "replace_taskbar")
        self.replace_taskbar.setEnabled(windows_only)
        body.addWidget(
            setting_row(
                "Doca do Ventude",
                "Substitui a barra do Windows pela cápsula flutuante centrada; hora, data, clima e "
                "Controles ficam só na ilha.",
                self.replace_taskbar,
            )
        )
        self.replace_start = self._switch(s.replace_start_menu, "replace_start_menu")
        self.replace_start.setEnabled(windows_only)
        body.addWidget(
            setting_row(
                "Tecla Windows abre o Lançador",
                "Win sozinha e Win + S abrem o Lançador (apps, janelas @, arquivos /, comandos >). "
                "Win + E, D, L, V, Tab e setas continuam do Windows.",
                self.replace_start,
            )
        )
        self.dock_mode = combo(DOCK_MODE_OPTIONS, s.dock_mode)
        self.dock_mode.currentIndexChanged.connect(
            lambda: self._set(dock_mode=self.dock_mode.currentData())
        )
        body.addWidget(
            setting_row(
                "Modo da Doca",
                "Esquivar recua quando uma janela encosta; Sempre reserva 64 px; "
                "Oculta só aparece ao encostar o mouse embaixo (ou apertar Win).",
                self.dock_mode,
            )
        )
        self.island_echo = self._switch(s.island_echo, "island_echo")
        body.addWidget(
            setting_row(
                "Eco nos monitores secundários",
                "Pílula mínima só com o relógio no topo das outras telas.",
                self.island_echo,
            )
        )
        self.all_monitors = self._switch(s.taskbar_all_monitors, "taskbar_all_monitors")
        body.addWidget(setting_row("Doca em todos os monitores", "", self.all_monitors))
        self.hotkey_controls = self._switch(s.hotkey_controls, "hotkey_controls")
        self.hotkey_controls.setEnabled(windows_only)
        body.addWidget(
            setting_row(
                "Win + A abre Controles",
                "Abre as teclas rápidas (rede, Cafeína, microfone, Foco, Silêncio) direto na ilha.",
                self.hotkey_controls,
            )
        )
        self.hotkey_dock_numbers = self._switch(s.hotkey_dock_numbers, "hotkey_dock_numbers")
        self.hotkey_dock_numbers.setEnabled(windows_only)
        body.addWidget(
            setting_row(
                "Win + 1..9 alterna apps da Doca",
                "Com Shift abre uma nova instância do app.",
                self.hotkey_dock_numbers,
            )
        )
        self.quiet_mode = self._switch(s.quiet_mode, "quiet_mode")
        body.addWidget(
            setting_row(
                "Modo Silêncio",
                "Suprime toasts informativos na ilha (alertas críticos continuam).",
                self.quiet_mode,
            )
        )
        self.windows_notifications = self._switch(s.windows_notifications, "windows_notifications")
        body.addWidget(
            setting_row(
                "Notificações do Windows",
                "Mostra as notificações dos outros apps (Discord, WhatsApp, e-mail...) na tela de "
                "Notificações do Ventude. O Windows pode pedir permissão na primeira vez.",
                self.windows_notifications,
            )
        )
        self.power_animation = self._switch(s.power_animation, "power_animation")
        body.addWidget(
            setting_row(
                "Animação de energia",
                "Antes de desligar, reiniciar, suspender, bloquear ou sair, as telas escurecem e "
                "aparece o Interruptor com alguns segundos para cancelar (Esc).",
                self.power_animation,
            )
        )
        self.greeting = self._switch(s.shell_greeting, "shell_greeting")
        body.addWidget(
            setting_row(
                "Saudação ao ligar",
                "“Bom dia, …!” com a data e o clima logo depois de ligar o PC.",
                self.greeting,
            )
        )
        self.agent_command = QLineEdit(s.agent_command)
        self.agent_command.setPlaceholderText("claude")
        self.agent_command.setFixedWidth(170)
        self.agent_command.editingFinished.connect(
            lambda: self._set(agent_command=self.agent_command.text().strip() or "claude")
        )
        body.addWidget(
            setting_row(
                "Comando do agente",
                "CLI executada no verbo “Agente aqui” em pastas e repositórios Git.",
                self.agent_command,
            )
        )
        column.addWidget(card)

        # ---------- bloqueio ----------
        column.addWidget(self._lock_card(s))

        # ---------- abas ----------
        card, body = section("Abas")
        body.addWidget(label("Desligue o que não usa: a aba some e nada dela roda.", "Faint"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        self.module_switches: dict[str, Switch] = {}
        optional = [m for m in MODULES if m[0] not in ("home", "settings")]
        for index, (key, title, icon_name) in enumerate(optional):
            switch = Switch()
            switch.setChecked(s.module_enabled(key))
            switch.toggled.connect(self._modules)
            glyph = label()
            glyph.setPixmap(icons.pixmap(icon_name, theme.TEXT_2, 16))
            cell = QWidget()
            cell.setLayout(hbox(glyph, label(title, ""), None, switch, spacing=8))
            grid.addWidget(cell, index // 2, index % 2)
            self.module_switches[key] = switch
        body.addLayout(grid)
        body.addWidget(label("Fontes de notícias", "Caption"))
        sources = s.news_sources.split(",")
        self.news_switches: dict[str, Switch] = {}
        news_row = hbox(spacing=18)
        for name in FEEDS:
            switch = Switch()
            switch.setChecked(name in sources)
            switch.toggled.connect(self._news)
            news_row.addLayout(hbox(label(name, ""), switch, spacing=8))
            self.news_switches[name] = switch
        news_row.addStretch(1)
        body.addLayout(news_row)
        column.addWidget(card)

        # ---------- IAs ----------
        card, body = section("Limites das IAs")
        body.addWidget(
            label(
                "Ligue as IAs que você usa, escolha quais ganham anel na ilha e no Início e qual limite "
                "cada anel acompanha (sessão de 5h, semana, um modelo...). O alfinete na aba IAs faz o "
                "mesmo.",
                "Faint",
                wrap=True,
            )
        )
        self.providers = ProviderTable(ctx)
        body.addWidget(self.providers)
        detect = QPushButton(" Detectar IAs instaladas")
        detect.setIcon(icons.icon("search", theme.TEXT, 15))
        detect.setCursor(Qt.CursorShape.PointingHandCursor)
        detect.clicked.connect(self._detect)
        self.detect_status = label("", "Faint")
        body.addLayout(hbox(detect, self.detect_status, None, spacing=10))
        self.rings = self._switch(s.pill_rings, "pill_rings")
        body.addWidget(
            setting_row("Anéis na ilha", "Liga ou desliga todos os anéis da ilha fechada.", self.rings)
        )
        self.notify = self._switch(s.limits_notify, "limits_notify")
        body.addWidget(
            setting_row("Avisos", "Notifica ao chegar em 80% e 95% e quando o limite renova.", self.notify)
        )
        self.interval = combo(INTERVALS, s.limits_interval_min)
        self.interval.currentIndexChanged.connect(
            lambda: self._set(limits_interval_min=self.interval.currentData())
        )
        body.addWidget(
            setting_row("Atualizar a cada", "Todas menos o Gemini, que é lido a cada 2 min.", self.interval)
        )
        column.addWidget(card)

        self.mode_combos: dict[str, QComboBox] = {}
        self.sections: dict[str, Card] = {
            "claude": self._provider_claude(),
            "antigravity": self._provider_antigravity(),
            "grok": self._provider_grok(),
            "codex": self._provider_codex(),
            "cursor": self._provider_cursor(),
            "copilot": self._provider_copilot(),
        }
        for card in self.sections.values():
            column.addWidget(card)

        # ---------- sobre ----------
        card, body = section("Ventude")
        body.addWidget(label(f"Versão {__version__} · dados em {app_data_dir()}", "Faint", wrap=True))
        folder = QPushButton(" Abrir pasta de dados")
        folder.setIcon(icons.icon("folder", theme.TEXT, 15))
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_data_dir()))))
        reset = QPushButton(" Recentralizar ilha")
        reset.setIcon(icons.icon("monitor", theme.TEXT, 15))
        reset.clicked.connect(ctx.reset_position_requested.emit)
        quit_btn = QPushButton(" Sair do Ventude")
        quit_btn.setObjectName("Danger")
        quit_btn.setIcon(icons.icon("power", theme.DANGER, 15))
        quit_btn.clicked.connect(ctx.quit_requested.emit)
        self.update_btn = QPushButton(" Procurar atualização")
        self.update_btn.setIcon(icons.icon("download", theme.TEXT, 15))
        self.update_btn.clicked.connect(self._check_update)
        self.update_status = label("", "Faint", wrap=True)
        self._update_dialog: QWidget | None = None
        for button in (folder, reset, quit_btn, self.update_btn):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        body.addLayout(hbox(self.update_btn, self.update_status, None, spacing=10))
        body.addLayout(hbox(folder, reset, None, quit_btn, spacing=8))
        column.addWidget(card)
        column.addStretch(1)
        self._building = False
        ctx.settings_changed.connect(self.sync)
        ctx.settings_changed.connect(self._sync_providers)
        self._sync_providers(ctx.settings)

    # ---------- provedores ----------
    def _provider_claude(self) -> Card:
        card, body = section("Claude", theme.provider_color("claude"))
        found = claude.read_credentials(self.ctx.limits.home) is not None
        status = (
            "Claude Code encontrado neste PC."
            if found
            else "Claude Code não encontrado no Windows (no WSL ele é procurado com a distro aberta)."
        )
        mode = combo(CLAUDE_MODES, self.ctx.settings.claude_mode)
        mode.currentIndexChanged.connect(lambda: self._provider_mode("claude", mode.currentData()))
        self.mode_combos["claude"] = mode
        body.addWidget(setting_row("Fonte", status, mode))
        renew = self._switch(self.ctx.settings.claude_refresh_login, "claude_refresh_login")
        renew.toggled.connect(lambda _c: self.ctx.limits.refresh("claude", force=True))
        body.addWidget(
            setting_row(
                "Renovar o login do Claude Code",
                "O token dura poucas horas. Quando vence, o Ventude renova do mesmo jeito que o "
                "Claude Code faz, sem precisar abrir o terminal.",
                renew,
            )
        )
        body.addWidget(label("claude.ai (para quem usa o site ou o Claude Desktop)", "Caption"))
        body.addWidget(
            SecretField(self.ctx, "claude_session", "sessionKey (sk-ant-sid01-…) ou cURL copiado", "claude")
        )
        body.addWidget(
            label(
                "O Claude Desktop usa a mesma conta do site: os limites são os mesmos. Como pegar: "
                "entre em claude.ai no navegador com essa conta e tecle F12. Jeito rápido: Aplicativo > "
                "Cookies > claude.ai > copie o valor de sessionKey. Se o Cloudflare barrar: aba Rede, "
                "clique numa requisição para claude.ai, botão direito > Copiar > Copiar como cURL e "
                "cole aqui.",
                "Faint",
                wrap=True,
            )
        )
        return card

    def _provider_antigravity(self) -> Card:
        card, body = section("Gemini · Antigravity", theme.provider_color("antigravity"))
        mode = combo(ANTIGRAVITY_MODES, self.ctx.settings.antigravity_mode)
        mode.currentIndexChanged.connect(lambda: self._provider_mode("antigravity", mode.currentData()))
        self.mode_combos["antigravity"] = mode
        body.addWidget(
            setting_row(
                "Fonte",
                "Lê a cota de cada modelo (Gemini, Claude, GPT...) direto do Antigravity aberto. "
                "O anel da ilha segue os modelos Gemini; use o alfinete na aba IAs para fixar outro.",
                mode,
            )
        )
        return card

    def _provider_grok(self) -> Card:
        card, body = section("Grok", theme.provider_color("grok"))
        found = grok.read_cli_token(self.ctx.limits.home) is not None
        status = "Grok CLI encontrado neste PC." if found else "Grok CLI não encontrado neste PC."
        mode = combo(GROK_MODES, self.ctx.settings.grok_mode)
        mode.currentIndexChanged.connect(lambda: self._provider_mode("grok", mode.currentData()))
        self.mode_combos["grok"] = mode
        body.addWidget(setting_row("Fonte", status, mode))
        body.addWidget(label("grok.com (site, app e bots do Grok)", "Caption"))
        body.addWidget(
            SecretField(self.ctx, "grok_cookie", "cURL copiado ou cookie (sso=…; sso-rw=…)", "grok")
        )
        body.addWidget(
            label(
                "O app e os bots do Grok usam a mesma conta do grok.com. Como pegar: abra grok.com "
                "logado, tecle F12 > Rede e atualize a página. Clique com o botão direito numa "
                "requisição para grok.com (ex.: rate-limits) > Copiar > Copiar como cURL e cole aqui. "
                "O Grok usado dentro do Cursor conta no limite do Cursor, não no da sua conta Grok.",
                "Faint",
                wrap=True,
            )
        )
        return card

    def _provider_codex(self) -> Card:
        card, body = section("Codex · ChatGPT", theme.provider_color("codex"))
        found = codex.read_login(self.ctx.limits.home) is not None
        body.addWidget(
            label(
                "Login do Codex encontrado neste PC." if found else "Codex não encontrado neste PC.",
                "Muted",
            )
        )
        body.addWidget(label("chatgpt.com (sem o Codex instalado)", "Caption"))
        body.addWidget(SecretField(self.ctx, "codex_session", "cURL copiado do chatgpt.com", "codex"))
        body.addWidget(
            label(
                "O Codex usa os limites do seu plano do ChatGPT: a janela de 5h e a semanal. Com o "
                "Codex logado (comando codex), não precisa de nada. Sem ele: abra chatgpt.com logado, "
                "tecle F12 > Rede, botão direito numa requisição para chatgpt.com > Copiar > Copiar "
                "como cURL e cole aqui.",
                "Faint",
                wrap=True,
            )
        )
        return card

    def _provider_cursor(self) -> Card:
        card, body = section("Cursor", theme.provider_color("cursor"))
        found = any(path.exists() for path in cursor.state_paths(self.ctx.limits.home))
        body.addWidget(
            label("Cursor encontrado neste PC." if found else "Cursor não encontrado neste PC.", "Muted")
        )
        body.addWidget(label("cursor.com (sem o Cursor instalado)", "Caption"))
        body.addWidget(
            SecretField(self.ctx, "cursor_cookie", "WorkosCursorSessionToken=… ou cURL copiado", "cursor")
        )
        body.addWidget(
            label(
                "O Ventude lê o login do Cursor instalado. Os modelos usados dentro do Cursor (Grok, Claude, "
                "GPT...) contam neste limite. Sem o Cursor: abra cursor.com/dashboard logado, tecle F12 > "
                "Aplicativo > Cookies > cursor.com e copie o valor de WorkosCursorSessionToken.",
                "Faint",
                wrap=True,
            )
        )
        return card

    def _provider_copilot(self) -> Card:
        card, body = section("GitHub Copilot", theme.provider_color("copilot"))
        found = copilot.read_token(self.ctx.limits.home) is not None
        body.addWidget(
            label(
                "Login do Copilot encontrado neste PC." if found else "Copilot não encontrado neste PC.",
                "Muted",
            )
        )
        body.addWidget(label("Token (sem o Copilot instalado)", "Caption"))
        body.addWidget(SecretField(self.ctx, "copilot_token", "ghu_… ou gho_…", "copilot"))
        body.addWidget(
            label(
                "O Ventude lê o login da extensão do Copilot (VS Code, Visual Studio, JetBrains) e mostra as "
                "requisições premium, o chat e o autocompletar do mês.",
                "Faint",
                wrap=True,
            )
        )
        return card

    def _check_update(self) -> None:
        self.update_btn.setEnabled(False)
        self.update_status.setText("Procurando no GitHub…")
        self.ctx.run(
            updates.latest, __version__, done=self._update_checked, fail=self._update_failed, owner=self
        )

    def _update_checked(self, update: updates.Update | None) -> None:
        self.update_btn.setEnabled(True)
        if update is None:
            self.update_status.setText(f"Você já está na versão mais nova ({__version__}).")
            return
        if not updates.enabled():
            self.update_status.setText(
                f"Versão {update.version} disponível. Rodando do código-fonte: atualize com git pull."
            )
            return
        self.update_status.setText(f"Versão {update.version} disponível.")
        from ventude.ui.update_dialog import ask_to_update

        if self._update_dialog is not None:
            self._update_dialog.close()
        self._update_dialog = ask_to_update(update, self.ctx.tasks, self.ctx.quit_requested.emit)

    def _update_failed(self, error: BaseException) -> None:
        self.update_btn.setEnabled(True)
        self.update_status.setText(f"Não deu para verificar agora: {error}")

    def _detect(self) -> None:
        self.detect_status.setText("Procurando o Codex, o Cursor e o Copilot neste PC…")

        def done(found: list[str]) -> None:
            names = ", ".join(PROVIDERS.get(p, p) for p in found)
            self.detect_status.setText(f"Ligado: {names}." if found else "Nenhuma IA nova encontrada.")

        self.ctx.detect_providers(done)

    def _sync_providers(self, settings: Settings) -> None:
        """Seções só das IAs ligadas e combos de fonte iguais à tabela."""
        for provider, card in self.sections.items():
            card.setVisible(settings.provider_mode(provider) != "off")
        for provider, box in self.mode_combos.items():
            mode = settings.provider_mode(provider)
            index = box.findData(mode)
            if index >= 0 and index != box.currentIndex():
                box.blockSignals(True)
                box.setCurrentIndex(index)
                box.blockSignals(False)

    # ---------- ações ----------
    def _lock_card(self, s: Settings) -> Card:
        """Tela de bloqueio: tempo parado até bloquear (prontos, personalizado ou nunca) e a senha."""
        card, body = section("Bloqueio")
        body.addWidget(
            label(
                "Depois do tempo sem mexer no PC, a tela de bloqueio do Ventude cobre todas as telas "
                "(relógio, clima, música e avisos). Não bloqueia com Cafeína, vídeo tocando "
                "ou jogo em tela cheia.",
                "Faint",
                wrap=True,
            )
        )
        options: list[tuple[str, object]] = [(timeout_label(m), m) for m in LOCK_PRESETS_MIN]
        options += [("Personalizado…", -1), ("Nunca", 0)]
        custom = s.lock_after_min > 0 and s.lock_after_min not in LOCK_PRESETS_MIN
        self.lock_after = combo(options, -1 if custom else s.lock_after_min)
        self.lock_custom = QSpinBox()
        self.lock_custom.setRange(1, LOCK_MAX_MIN)
        self.lock_custom.setSuffix(" min")
        self.lock_custom.setFixedWidth(110)
        self.lock_custom.setValue(s.lock_after_min if custom else 20)
        self.lock_custom.setVisible(custom)
        self.lock_custom.editingFinished.connect(lambda: self._set(lock_after_min=self.lock_custom.value()))
        self.lock_after.currentIndexChanged.connect(self._lock_choice)
        controls = QWidget()
        row = QHBoxLayout(controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.lock_custom)
        row.addWidget(self.lock_after)
        body.addWidget(setting_row("Bloquear depois de", "Padrão: 5 minutos.", controls))
        self.lock_password = self._switch(s.lock_require_password, "lock_require_password")
        body.addWidget(
            setting_row(
                "Pedir a senha do Windows",
                "Ao mexer na tela de bloqueio, o Windows pede a senha, o PIN ou o Windows Hello. "
                "Mexer nos primeiros 5 segundos depois de ela aparecer sozinha não pede.",
                self.lock_password,
            )
        )
        self.win_triple = self._switch(s.win_triple_hide, "win_triple_hide")
        body.addWidget(
            setting_row(
                "Windows 3× esconde a ilha",
                "Aperte a tecla Windows três vezes seguidas: a ilha é sugada para o meio e some. "
                "Mais três vezes e ela volta.",
                self.win_triple,
            )
        )
        test = QPushButton("Ver a tela de bloqueio")
        test.setCursor(Qt.CursorShape.PointingHandCursor)
        test.clicked.connect(self._preview_lock)
        body.addWidget(test, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _lock_choice(self) -> None:
        value = int(self.lock_after.currentData())
        self.lock_custom.setVisible(value < 0)
        if value < 0:
            self.lock_custom.setFocus()
            self._set(lock_after_min=self.lock_custom.value())
        else:
            self._set(lock_after_min=value)

    def _preview_lock(self) -> None:
        """Mostra a tela sem pedir senha (é só uma prévia): qualquer toque volta."""
        lock = getattr(self.ctx, "lock_screen", None)
        if lock is None:
            return
        lock.show_lock(preview=True)

    def _switch(self, value: bool, field: str) -> Switch:
        switch = Switch()
        switch.setChecked(value)
        switch.toggled.connect(lambda checked: self._set(**{field: checked}))
        return switch

    def _set(self, **changes: object) -> None:
        if not self._building:
            self.ctx.update_settings(**changes)

    def _provider_mode(self, provider: str, mode: str) -> None:
        self._set(**{f"{provider}_mode": mode})
        self.ctx.limits.refresh(provider, force=True)

    def _autostart(self, enabled: bool) -> None:
        if system.set_autostart(enabled):
            self._set(start_with_windows=enabled)

    def _modules(self) -> None:
        enabled = [k for k, sw in self.module_switches.items() if sw.isChecked()]
        self._set(modules=",".join(enabled))

    def _news(self) -> None:
        names = [n for n, sw in self.news_switches.items() if sw.isChecked()]
        self._set(news_sources=",".join(names))

    def _fill_monitors(self) -> None:
        self.monitor.blockSignals(True)
        self.monitor.clear()
        for index, screen in enumerate(QGuiApplication.screens()):
            size = screen.geometry()
            self.monitor.addItem(f"{index + 1} · {size.width()}×{size.height()}", index)
        self.monitor.setCurrentIndex(min(self.ctx.settings.monitor_index, self.monitor.count() - 1))
        self.monitor.blockSignals(False)

    def _monitor(self) -> None:
        self._set(monitor_index=max(0, self.monitor.currentIndex()), island_position_json="")

    def sync(self, settings: Settings) -> None:
        self._fill_monitors()
        for switch, value in (
            (self.replace_taskbar, settings.replace_taskbar),
            (self.replace_start, settings.replace_start_menu),
            (self.island_echo, settings.island_echo),
            (self.all_monitors, settings.taskbar_all_monitors),
            (self.hotkey_controls, settings.hotkey_controls),
            (self.hotkey_dock_numbers, settings.hotkey_dock_numbers),
            (self.quiet_mode, settings.quiet_mode),
            (self.power_animation, settings.power_animation),
            (self.greeting, settings.shell_greeting),
            (self.pill_date, settings.pill_date),
            (self.weather_pill, settings.pill_weather),
        ):
            if switch.isChecked() != value:
                switch.blockSignals(True)
                switch.setChecked(value)
                switch.blockSignals(False)
        index = self.dock_mode.findData(settings.dock_mode)
        if index >= 0 and index != self.dock_mode.currentIndex():
            self.dock_mode.blockSignals(True)
            self.dock_mode.setCurrentIndex(index)
            self.dock_mode.blockSignals(False)
        if not self.agent_command.hasFocus() and self.agent_command.text() != settings.agent_command:
            self.agent_command.setText(settings.agent_command)

    def scroll_to_shell(self) -> None:
        """Abre os Ajustes já na seção do Ventude Shell (menu da barra, painel rápido)."""
        area = self.findChild(QScrollArea)
        if area is not None:
            area.verticalScrollBar().setValue(max(0, self.shell_card.y() - 8))

    def on_show(self) -> None:
        self._fill_monitors()
        if system.autostart_supported():
            self.autostart.blockSignals(True)
            self.autostart.setChecked(system.autostart_enabled())
            self.autostart.blockSignals(False)
