"""Notícias: manchetes recentes das fontes escolhidas."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QPushButton, QVBoxLayout, QWidget

from ventude.core.timefmt import ago
from ventude.services import launcher
from ventude.services.news import FEEDS, NewsItem
from ventude.ui import theme
from ventude.ui.context import AppContext
from ventude.ui.pages.base import Page, scroller
from ventude.ui.widgets import Card, ElidedLabel, EmptyState, IconButton, hbox, label

SOURCE_COLORS = {"G1": "#F28B82", "BBC Brasil": "#E8EAED", "Tecnoblog": "#7FD1B9"}


class NewsRow(Card):
    def __init__(self, item: NewsItem) -> None:
        super().__init__(clickable=True, margins=(14, 10, 14, 10), spacing=3)
        self.item = item
        source = label(item.source, "")
        source.setStyleSheet(
            f"color: {SOURCE_COLORS.get(item.source, theme.ACCENT)}; font-size: 11px; font-weight: 700;"
        )
        when = label(ago(item.published) if item.published else "", "Faint")
        self.body.addLayout(hbox(source, when, None, spacing=8))
        title = label(item.title, "", wrap=True)
        title.setStyleSheet("font-size: 13px; font-weight: 600;")
        self.body.addWidget(title)
        if item.summary:
            self.body.addWidget(ElidedLabel(item.summary, "Muted"))
        self.setToolTip(item.link)
        self.clicked.connect(lambda: launcher.open_url(item.link))


class NewsPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        row, self.subtitle = self.header("Notícias")
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.filter = "Todas"
        self.chips: dict[str, QPushButton] = {}
        self.chip_row = hbox(spacing=6)
        row.addLayout(self.chip_row)
        refresh = IconButton("refresh", "Atualizar manchetes", 34, 16)
        refresh.clicked.connect(lambda: self.load(force=True))
        row.addWidget(refresh)
        self.box = QWidget()
        self.rows = QVBoxLayout(self.box)
        self.rows.setContentsMargins(0, 0, 6, 0)
        self.rows.setSpacing(8)
        self.rows.addStretch(1)
        self.scroll = scroller(self.box)
        self.root.addWidget(self.scroll, 1)
        self.empty = EmptyState("news", "Sem manchetes", "Escolha fontes em Ajustes ou tente atualizar.")
        self.root.addWidget(self.empty, 1)
        self.empty.hide()
        self.items: list[NewsItem] = []
        self._loaded_at = 0.0
        self._busy = False
        ctx.settings_changed.connect(lambda _s: self._build_chips())
        self._build_chips()

    def _sources(self) -> list[str]:
        return [s.strip() for s in self.ctx.settings.news_sources.split(",") if s.strip() in FEEDS]

    def _build_chips(self) -> None:
        names = ["Todas", *self._sources()]
        if list(self.chips) == names:
            return
        for button in self.chips.values():
            self.group.removeButton(button)
            button.deleteLater()
        self.chips.clear()
        for name in names:
            chip = QPushButton(name)
            chip.setObjectName("Chip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _c=False, n=name: self._set_filter(n))
            self.group.addButton(chip)
            self.chip_row.addWidget(chip)
            self.chips[name] = chip
        if self.filter not in self.chips:
            self.filter = "Todas"
        self.chips[self.filter].setChecked(True)
        self._loaded_at = 0.0

    def _set_filter(self, name: str) -> None:
        self.filter = name
        self._render()

    def load(self, force: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        self.subtitle.setText("Atualizando…")
        sources = self._sources()
        self.ctx.run(
            lambda: self.ctx.news.fetch(sources, force=force),
            done=self._show,
            fail=lambda e: self._show([]),
            owner=self,
        )

    def _show(self, items: list[NewsItem]) -> None:
        self._busy = False
        self._loaded_at = time.time()
        self.items = items
        self._render()

    def _render(self) -> None:
        while self.rows.count() > 1:
            child = self.rows.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        shown = [i for i in self.items if self.filter in ("Todas", i.source)][:40]
        for index, item in enumerate(shown):
            self.rows.insertWidget(index, NewsRow(item))
        self.scroll.setVisible(bool(shown))
        self.empty.setVisible(not shown)
        self.scroll.verticalScrollBar().setValue(0)
        self.subtitle.setText(
            f"{len(shown)} manchetes · atualizado {ago(self._loaded_at)}" if shown else "Nada por aqui"
        )

    def on_show(self) -> None:
        if time.time() - self._loaded_at > 10 * 60:
            self.load()
