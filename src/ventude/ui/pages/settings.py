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
    QVBoxLayout,
    QWidget,
)

from ventude import __version__
from ventude.core.config import MODULES, Settings
from ventude.core.paths import app_data_dir
from ventude.limits import claude, grok
from ventude.services import system
from ventude.services.news import FEEDS
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
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
ANIMATIONS = [("Rápida", "rapida"), ("Normal", "normal"), ("Suave", "suave")]
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
            elif report.state == "ok" and report.source in ("claude.ai", "grok.com"):
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
        column.addWidget(card)

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
        self.rings = self._switch(s.pill_rings, "pill_rings")
        body.addWidget(setting_row("Anéis na ilha", "Mostra o uso de cada IA na ilha fechada.", self.rings))
        self.notify = self._switch(s.limits_notify, "limits_notify")
        body.addWidget(
            setting_row("Avisos", "Notifica ao chegar em 80% e 95% e quando o limite renova.", self.notify)
        )
        self.interval = combo(INTERVALS, s.limits_interval_min)
        self.interval.currentIndexChanged.connect(
            lambda: self._set(limits_interval_min=self.interval.currentData())
        )
        body.addWidget(
            setting_row("Atualizar a cada", "Claude e Grok. O Gemini é lido a cada 2 min.", self.interval)
        )
        column.addWidget(card)

        column.addWidget(self._provider_claude())
        column.addWidget(self._provider_antigravity())
        column.addWidget(self._provider_grok())

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
        for button in (folder, reset, quit_btn):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        body.addLayout(hbox(folder, reset, None, quit_btn, spacing=8))
        column.addWidget(card)
        column.addStretch(1)
        self._building = False

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
        body.addWidget(label("claude.ai (para quem usa o site ou o app do Claude)", "Caption"))
        body.addWidget(
            SecretField(self.ctx, "claude_session", "sessionKey (sk-ant-sid01-…) ou cURL copiado", "claude")
        )
        body.addWidget(
            label(
                "Como pegar: abra claude.ai logado e tecle F12. Jeito rápido: Aplicativo > Cookies > "
                "claude.ai > copie o valor de sessionKey. Se o Cloudflare barrar: aba Rede, clique "
                "numa requisição para claude.ai, botão direito > Copiar > Copiar como cURL e cole aqui.",
                "Faint",
                wrap=True,
            )
        )
        return card

    def _provider_antigravity(self) -> Card:
        card, body = section("Gemini · Antigravity", theme.provider_color("antigravity"))
        mode = combo(ANTIGRAVITY_MODES, self.ctx.settings.antigravity_mode)
        mode.currentIndexChanged.connect(lambda: self._provider_mode("antigravity", mode.currentData()))
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
        body.addWidget(setting_row("Fonte", status, mode))
        body.addWidget(label("grok.com (para quem usa o site ou o app)", "Caption"))
        body.addWidget(
            SecretField(self.ctx, "grok_cookie", "cURL copiado ou cookie (sso=…; sso-rw=…)", "grok")
        )
        body.addWidget(
            label(
                "Como pegar: abra grok.com logado, tecle F12 > Rede e atualize a página. Clique com o "
                "botão direito numa requisição para grok.com (ex.: rate-limits) > Copiar > "
                "Copiar como cURL e cole aqui. Assim vão juntos o cookie e o navegador usado.",
                "Faint",
                wrap=True,
            )
        )
        return card

    # ---------- ações ----------
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

    def on_show(self) -> None:
        self._fill_monitors()
        if system.autostart_supported():
            self.autostart.blockSignals(True)
            self.autostart.setChecked(system.autostart_enabled())
            self.autostart.blockSignals(False)
