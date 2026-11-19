"""IAs: os limites de cada provedor, janela por janela."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from ventude.core.timefmt import ago, resets_text
from ventude.limits.model import Report
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.pages.base import Page, scroller
from ventude.ui.widgets import Badge, Card, IconButton, UsageBar, hbox, label, repolish

HOW_TO = {
    "claude": "Use o Claude Code logado (comando claude) ou cole a sessionKey do claude.ai em Ajustes.",
    "antigravity": "Deixe o Antigravity aberto e logado. O Ventude lê as cotas direto dele.",
    "grok": "Use o Grok CLI logado (comando grok) ou cole o cookie do grok.com em Ajustes.",
}


class ProviderCard(Card):
    def __init__(self, ctx: AppContext, provider: str) -> None:
        super().__init__(margins=(16, 14, 14, 14), spacing=10)
        self.ctx, self.provider = ctx, provider
        color = theme.provider_color(provider)
        dot = label()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background: {color}; border-radius: 5px;")
        self.name = label("", "H2")
        self.plan = Badge("", color)
        self.refresh_btn = IconButton("refresh", "Atualizar agora", 28, 15)
        self.refresh_btn.clicked.connect(lambda: ctx.limits.refresh(provider, force=True))
        self.body.addLayout(hbox(dot, self.name, self.plan, None, self.refresh_btn, spacing=8))
        self.source = label("", "Faint")
        self.body.addWidget(self.source)
        self.bars_box = QWidget()
        self.bars = QVBoxLayout(self.bars_box)
        self.bars.setContentsMargins(0, 2, 6, 0)
        self.bars.setSpacing(12)
        self.bars.addStretch(1)
        self.scroll = scroller(self.bars_box)
        self.body.addWidget(self.scroll, 1)
        self.message = label("", "Muted", wrap=True)
        self.body.addWidget(self.message)
        self.setup_btn = QPushButton("Abrir ajustes")
        self.setup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setup_btn.clicked.connect(lambda: ctx.navigate_requested.emit("settings"))
        self.body.addWidget(self.setup_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._pool: list[UsageBar] = []

    def refresh(self) -> None:
        report = self.ctx.limits.report(self.provider)
        busy = self.ctx.limits.busy(self.provider)
        self.name.setText(report.name)
        self.plan.setText(report.plan)
        self.plan.setVisible(bool(report.plan))
        self.refresh_btn.setEnabled(not busy)
        self.refresh_btn.setToolTip("Consultando…" if busy else "Atualizar agora")
        self.source.setText(self._source_text(report, busy))
        self._fill_bars(report)
        self._message(report)

    @staticmethod
    def _source_text(report: Report, busy: bool) -> str:
        if busy and not report.windows:
            return "Consultando…"
        parts = []
        if report.source:
            parts.append(f"via {report.source}")
        if report.fetched_at:
            parts.append(ago(report.fetched_at))
        if busy:
            parts.append("atualizando…")
        return " · ".join(parts) or "Aguardando a primeira leitura"

    def _fill_bars(self, report: Report) -> None:
        windows = report.windows
        while len(self._pool) < len(windows):
            bar = UsageBar()
            self._pool.append(bar)
            self.bars.insertWidget(self.bars.count() - 1, bar)
        for index, bar in enumerate(self._pool):
            if index >= len(windows):
                bar.hide()
                continue
            window = windows[index]
            used = window.used_at()
            sub = resets_text(window.resets_at)
            if window.detail and window.key.startswith(("mode:", "credits")):
                sub = f"{sub} · {window.detail}" if sub else window.detail
            tip = window.detail if window.key.startswith("model:") else ""
            bar.set_data(
                window.label,
                used,
                theme.usage_color(self.provider, used),
                sub or "Sem data de renovação",
                tip,
                dim=report.state != "ok",
            )
            bar.show()
        self.scroll.setVisible(bool(windows))

    def _message(self, report: Report) -> None:
        text, style = "", "Muted"
        if report.state == "setup":
            text = report.message or HOW_TO.get(self.provider, "")
        elif report.state == "stale":
            text, style = report.message, "Warn"
        elif report.state == "error":
            text, style = report.message, "Error"
        elif report.state == "ok" and not report.windows:
            text = "Nenhum limite informado para esta conta."
        if self.message.objectName() != style:
            self.message.setObjectName(style)
            repolish(self.message)
        self.message.setText(text)
        self.message.setVisible(bool(text))
        self.setup_btn.setVisible(report.state in ("setup", "error") and not report.windows)


class LimitsPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        row, self.subtitle = self.header("Limites das IAs")
        refresh = QPushButton(" Atualizar tudo")
        refresh.setIcon(icons.icon("refresh", theme.TEXT, 15))
        refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh.clicked.connect(ctx.limits.refresh_all)
        row.addWidget(refresh)
        self.cards = {p: ProviderCard(ctx, p) for p in ctx.limits.providers()}
        grid = hbox(spacing=12)
        for card in self.cards.values():
            grid.addWidget(card, 1)
        self.root.addLayout(grid, 1)
        self.none = label(
            "Todas as IAs estão desligadas. Ligue as que você usa em Ajustes.", "Muted", wrap=True
        )
        self.root.addWidget(self.none)
        foot = label(
            "Leitura direta das contas que você já usa: nada passa por servidores de terceiros.",
            "Faint",
        )
        self.root.addWidget(foot)
        ctx.limits.changed.connect(self._changed)
        ctx.limits.ticked.connect(self.refresh)
        ctx.settings_changed.connect(lambda _s: self.refresh())
        self.refresh()

    def _changed(self, provider: str) -> None:
        if provider in self.cards:
            self._apply_visibility()
            self.cards[provider].refresh()

    def _apply_visibility(self) -> None:
        shown = 0
        for provider, card in self.cards.items():
            visible = self.ctx.settings.provider_mode(provider) != "off"
            card.setVisible(visible)
            shown += visible
        self.none.setVisible(shown == 0)
        minutes = self.ctx.settings.limits_interval_min
        self.subtitle.setText(f"Atualiza sozinho a cada {minutes} min · Gemini a cada 2 min")

    def refresh(self) -> None:
        self._apply_visibility()
        for card in self.cards.values():
            card.refresh()

    def on_show(self) -> None:
        self.refresh()
