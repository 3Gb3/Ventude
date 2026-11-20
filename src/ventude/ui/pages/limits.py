"""IAs: os limites de cada provedor, janela por janela."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ventude.core.timefmt import ago, resets_text
from ventude.limits.model import Report, Window
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.pages.base import Page, scroller
from ventude.ui.widgets import Badge, Card, IconButton, UsageBar, hbox, label, repolish

HOW_TO = {
    "claude": "Usa o Claude Desktop ou o site? Cole a sessionKey do claude.ai em Ajustes "
    "(ou use o Claude Code logado).",
    "antigravity": "Deixe o Antigravity aberto e logado. O Ventude lê as cotas direto dele.",
    "grok": "Cole o cURL ou o cookie do grok.com em Ajustes (vale para o site, o app e os bots).",
    "codex": "Use o Codex logado (comando codex) ou cole o cURL do chatgpt.com em Ajustes.",
    "cursor": "Abra o Cursor logado neste PC ou cole o cookie do cursor.com em Ajustes.",
    "copilot": "Entre no Copilot no seu editor ou cole o token em Ajustes.",
}


class LimitRow(QWidget):
    """Uma janela de limite com o alfinete que a fixa no anel da ilha."""

    pin_clicked = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.key = ""
        self.bar = UsageBar()
        self.pin = IconButton("pin", "", 24, 13)
        self.pin.clicked.connect(lambda: self.pin_clicked.emit(self.key))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(self.bar, 1)
        row.addWidget(self.pin, 0, Qt.AlignmentFlag.AlignTop)

    def set_pin(self, state: str) -> None:
        """state: pinned (fixado) | auto (no anel agora) | off."""
        color = {"pinned": theme.ACCENT, "auto": theme.TEXT_2}.get(state, theme.STROKE_2)
        self.pin.set_icon("pin", color)
        self.pin.setToolTip(
            {
                "pinned": "Fixado no anel da ilha. Clique para voltar ao automático.",
                "auto": "No anel da ilha agora (automático). Clique para fixar.",
            }.get(state, "Mostrar este limite no anel da ilha")
        )


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
        self.bars.setContentsMargins(0, 2, 2, 0)
        self.bars.setSpacing(8)
        self.bars.addStretch(1)
        self.scroll = scroller(self.bars_box)
        self.body.addWidget(self.scroll, 1)
        self.message = label("", "Muted", wrap=True)
        self.body.addWidget(self.message)
        self.setup_btn = QPushButton("Abrir ajustes")
        self.setup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setup_btn.clicked.connect(lambda: ctx.navigate_requested.emit("settings"))
        self.body.addWidget(self.setup_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._rows: dict[str, LimitRow] = {}
        self._layout_key: tuple = ()

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

    def _clear_bars(self) -> None:
        while self.bars.count() > 1:
            item = self.bars.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows.clear()

    def _fill_bars(self, report: Report) -> None:
        windows = report.windows
        groups = list(dict.fromkeys(w.group for w in windows if w.group))
        show_groups = len(groups) > 1
        layout_key = tuple((w.key, w.group) for w in windows) + (show_groups,)
        if layout_key != self._layout_key:
            self._clear_bars()
            self._layout_key = layout_key
            current = None
            for window in windows:
                if show_groups and window.group != current:
                    current = window.group
                    heading = label(window.group.upper(), "Caption")
                    heading.setContentsMargins(0, 4 if self.bars.count() > 1 else 0, 0, 0)
                    self.bars.insertWidget(self.bars.count() - 1, heading)
                row = LimitRow()
                row.pin_clicked.connect(self._toggle_pin)
                self.bars.insertWidget(self.bars.count() - 1, row)
                self._rows[window.key] = row
        pinned = self.ctx.settings.pinned_limit(self.provider)
        head = self.ctx.limits.headline(report)
        for window in windows:
            row = self._rows.get(window.key)
            if row is None:
                continue
            row.key = window.key
            self._fill_row(row, report, window)
            state = "off"
            if head is not None and head.key == window.key:
                state = "pinned" if pinned == window.key else "auto"
            row.set_pin(state)
        self.scroll.setVisible(bool(windows))

    def _fill_row(self, row: LimitRow, report: Report, window: Window) -> None:
        used = window.used_at()
        sub = resets_text(window.resets_at)
        if window.detail and window.key.startswith(("mode:", "credits")):
            sub = f"{sub} · {window.detail}" if sub else window.detail
        tip = window.detail if window.key.startswith("model:") else ""
        row.bar.set_data(
            window.label,
            used,
            theme.usage_color(self.provider, used),
            sub or "Sem data de renovação",
            tip,
            dim=report.state != "ok",
        )

    def _toggle_pin(self, key: str) -> None:
        settings = self.ctx.settings
        current = settings.pinned_limit(self.provider)
        self.ctx.update_settings(
            pinned_limits_json=settings.with_pinned(self.provider, "" if current == key else key)
        )

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
        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        self._placed: list[str] = []
        self.root.addLayout(self.grid, 1)
        self.none = label(
            "Todas as IAs estão desligadas. Ligue as que você usa em Ajustes.", "Muted", wrap=True
        )
        self.root.addWidget(self.none)
        foot = label(
            "Leitura direta das contas que você já usa: nada passa por servidores de terceiros. "
            "O alfinete escolhe qual limite aparece no anel da ilha.",
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
        """Até 3 IAs lado a lado; com mais, em duas linhas."""
        shown = [p for p in self.cards if self.ctx.settings.provider_mode(p) != "off"]
        if shown != self._placed:
            for card in self.cards.values():
                self.grid.removeWidget(card)
                card.hide()
            columns = len(shown) if len(shown) <= 3 else (2 if len(shown) == 4 else 3)
            for index, provider in enumerate(shown):
                self.grid.addWidget(self.cards[provider], index // columns, index % columns)
                self.cards[provider].show()
            for column in range(3):
                self.grid.setColumnStretch(column, 1 if column < max(1, columns) else 0)
            rows = (len(shown) + max(1, columns) - 1) // max(1, columns)
            for row in range(2):
                self.grid.setRowStretch(row, 1 if row < rows else 0)
            self._placed = shown
        self.none.setVisible(not shown)
        minutes = self.ctx.settings.limits_interval_min
        self.subtitle.setText(f"Atualiza sozinho a cada {minutes} min · Gemini a cada 2 min")

    def refresh(self) -> None:
        self._apply_visibility()
        for card in self.cards.values():
            card.refresh()

    def on_show(self) -> None:
        self.refresh()
