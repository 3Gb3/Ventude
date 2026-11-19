"""Mercado: sua lista de ativos com variação do dia e gráfico do mês."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit, QMenu, QPushButton, QVBoxLayout, QWidget

from ventude.core.timefmt import ago
from ventude.services.market import Quote, dump_watchlist, format_price, parse_watchlist
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.pages.base import Page, scroller
from ventude.ui.widgets import Badge, Card, ElidedLabel, EmptyState, IconButton, Sparkline, hbox, label, vbox


class QuoteRow(QWidget):
    def __init__(self, quote: Quote, on_remove) -> None:  # noqa: ANN001
        super().__init__()
        self.quote = quote
        self.setFixedHeight(48)
        name = ElidedLabel(quote.name)
        name.setStyleSheet("font-size: 13px; font-weight: 600;")
        symbol = label(quote.symbol, "Faint")
        up = (quote.change_pct or 0) >= 0
        color = theme.UP if up else theme.DOWN
        spark = Sparkline()
        spark.set_values(quote.history, color)
        price = label(format_price(quote), "")
        price.setStyleSheet("font-size: 13px; font-weight: 600;")
        price.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        price.setMinimumWidth(120)
        change = Badge(
            "—"
            if quote.change_pct is None
            else f"{'+' if up else ''}{quote.change_pct:.2f}%".replace(".", ","),
            color,
        )
        change.setFixedSize(76, 24)
        change.setAlignment(Qt.AlignmentFlag.AlignCenter)
        remove = IconButton("close", "Remover da lista", 26, 13, theme.MUTED)
        remove.clicked.connect(lambda: on_remove(quote.symbol))
        self.setLayout(hbox(vbox(name, symbol, spacing=0), spark, price, change, remove, spacing=14))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self._menu(pos, on_remove))

    def _menu(self, pos, on_remove) -> None:  # noqa: ANN001
        menu = QMenu(self)
        menu.addAction(icons.icon("trash", theme.DANGER, 15), "Remover", lambda: on_remove(self.quote.symbol))
        menu.exec(self.mapToGlobal(pos))


class MarketPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        row, self.subtitle = self.header("Mercado", "Yahoo Finance · cotações com atraso")
        self.refresh_btn = IconButton("refresh", "Atualizar cotações", 34, 16)
        self.refresh_btn.clicked.connect(lambda: self.load(force=True))
        row.addWidget(self.refresh_btn)
        self.card = Card(margins=(16, 6, 10, 6), spacing=0)
        self.box = QWidget()
        self.rows = QVBoxLayout(self.box)
        self.rows.setContentsMargins(0, 0, 6, 0)
        self.rows.setSpacing(0)
        self.rows.addStretch(1)
        self.card.body.addWidget(scroller(self.box))
        self.root.addWidget(self.card, 1)
        self.empty = EmptyState("chart", "Lista vazia", "Adicione ações, índices, moedas ou cripto abaixo.")
        self.root.addWidget(self.empty, 1)
        self.symbol = QLineEdit()
        self.symbol.setPlaceholderText("Adicionar ativo: ITUB4.SA, ^GSPC, EURBRL=X, ETH-USD…")
        self.symbol.returnPressed.connect(self._add)
        add = QPushButton("Adicionar")
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.clicked.connect(self._add)
        self.root.addLayout(hbox(self.symbol, add, spacing=8))
        self.status = label("", "Faint")
        self.root.addWidget(self.status)
        self._loaded_at = 0.0
        self._busy = False

    def load(self, force: bool = False) -> None:
        if self._busy:
            return
        watch = parse_watchlist(self.ctx.settings.market_symbols)
        self.empty.setVisible(not watch)
        self.card.setVisible(bool(watch))
        if not watch:
            return
        self._busy = True
        self.subtitle.setText("Atualizando…")
        self.ctx.run(
            lambda: self.ctx.market.quotes(watch, force=force),
            done=self._show,
            fail=self._failed,
            owner=self,
        )

    def _failed(self, error: BaseException) -> None:
        self._busy = False
        self.subtitle.setText(f"Sem conexão com o Yahoo Finance ({error}).")

    def _show(self, quotes: list[Quote]) -> None:
        self._busy = False
        self._loaded_at = time.time()
        while self.rows.count() > 1:
            child = self.rows.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        for index, quote in enumerate(quotes):
            if index:
                line = QWidget()
                line.setFixedHeight(1)
                line.setStyleSheet(f"background: {theme.STROKE};")
                self.rows.insertWidget(self.rows.count() - 1, line)
            self.rows.insertWidget(self.rows.count() - 1, QuoteRow(quote, self._remove))
        self.subtitle.setText(f"Yahoo Finance · atualizado {ago(self._loaded_at)} · com atraso")

    def _add(self) -> None:
        symbol = self.symbol.text().strip().upper()
        if not symbol:
            return
        self.status.setText(f"Procurando {symbol}…")

        def check() -> object:
            return self.ctx.market.quote(symbol, symbol, force=True)

        self.ctx.run(
            check,
            done=lambda q: self._added(symbol, q),
            fail=lambda _e: self._added(symbol, None),
            owner=self,
        )

    def _added(self, symbol: str, quote: Quote | None) -> None:
        if not quote:
            self.status.setText(f"Não encontrei {symbol} no Yahoo Finance.")
            return
        watch = parse_watchlist(self.ctx.settings.market_symbols)
        if all(s != symbol for s, _ in watch):
            watch.append((symbol, symbol.split(".")[0].lstrip("^")))
            self.ctx.update_settings(market_symbols=dump_watchlist(watch))
        self.symbol.clear()
        self.status.setText("")
        self.load()

    def _remove(self, symbol: str) -> None:
        watch = [(s, n) for s, n in parse_watchlist(self.ctx.settings.market_symbols) if s != symbol]
        self.ctx.update_settings(market_symbols=dump_watchlist(watch))
        self.load()

    def on_show(self) -> None:
        if time.time() - self._loaded_at > 5 * 60:
            self.load()
