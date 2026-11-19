"""Clima: agora e próximos 7 dias, pelo Open-Meteo."""

from __future__ import annotations

import time
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLineEdit, QListWidget, QListWidgetItem, QWidget

from ventude.core.timefmt import WEEKDAYS_SHORT, ago
from ventude.services.weather import Forecast, Place, describe, icon_for
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.pages.base import Page
from ventude.ui.widgets import Card, ElidedLabel, EmptyState, IconButton, TempRange, hbox, label, vbox

PLACE_ROLE = Qt.ItemDataRole.UserRole


def _tint(icon_name: str) -> str:
    return theme.SUN if icon_name in ("sun", "cloud-sun") else theme.SKY


def _chip(icon_name: str, text: str) -> QWidget:
    holder = QWidget()
    glyph = label()
    glyph.setPixmap(icons.pixmap(icon_name, theme.TEXT_2, 14))
    holder.setLayout(hbox(glyph, label(text, "Muted"), spacing=5))
    return holder


class WeatherPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar cidade… (Enter)")
        self.search.returnPressed.connect(self._search)
        self.refresh_btn = IconButton("refresh", "Atualizar previsão", 34, 16)
        self.refresh_btn.clicked.connect(lambda: self.load(force=True))
        self.root.addLayout(hbox(self.search, self.refresh_btn, spacing=8))
        self.results = QListWidget()
        self.results.setMaximumHeight(170)
        self.results.itemClicked.connect(self._choose)
        self.results.hide()
        self.root.addWidget(self.results)

        self.hero = Card(margins=(20, 16, 20, 16))
        self.icon = label()
        self.icon.setFixedSize(64, 64)
        self.temp = label("—")
        self.temp.setFont(theme.font(54, theme.SEMI, display=True))
        self.place = ElidedLabel("", "H2")
        self.desc = label("", "Muted")
        self.chips = hbox(spacing=16)
        info = vbox(None, self.place, self.desc, self.chips, None, spacing=4)
        self.hero.body.addLayout(hbox(self.icon, self.temp, 18, info, spacing=10))
        self.root.addWidget(self.hero)

        self.week = Card(margins=(18, 10, 18, 10), spacing=0)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(5)
        self.week.body.addLayout(self.grid)
        self.root.addWidget(self.week, 1)
        self.empty = EmptyState("sun", "Qual é a sua cidade?", "Busque acima e escolha na lista.")
        self.root.addWidget(self.empty, 1)
        self.status = label("Dados: Open-Meteo", "Faint")
        self.root.addWidget(self.status)
        self._loaded_at = 0.0
        self._busy = False
        self._show_empty(True)

    def _show_empty(self, empty: bool) -> None:
        self.empty.setVisible(empty)
        self.hero.setVisible(not empty)
        self.week.setVisible(not empty)

    # ---------- busca ----------
    def _search(self) -> None:
        query = self.search.text().strip()
        if len(query) < 2:
            return
        self.status.setText("Buscando…")
        self.ctx.run(self.ctx.weather.search, query, done=self._results, fail=self._failed, owner=self)

    def _results(self, places: list[Place]) -> None:
        self.results.clear()
        for place in places:
            item = QListWidgetItem(icons.icon("search", theme.TEXT_2, 14), place.label)
            item.setData(PLACE_ROLE, place)
            self.results.addItem(item)
        self.results.setVisible(bool(places))
        self.status.setText(
            "Escolha uma cidade na lista." if places else "Nenhuma cidade encontrada com esse nome."
        )

    def _choose(self, item: QListWidgetItem) -> None:
        place: Place = item.data(PLACE_ROLE)
        self.results.hide()
        self.search.clear()
        self.ctx.update_settings(weather_city_json=place.to_json())
        self.load(force=True)

    # ---------- previsão ----------
    def load(self, force: bool = False) -> None:
        place = Place.from_json(self.ctx.settings.weather_city_json)
        if not place:
            self._show_empty(True)
            self.status.setText("Dados: Open-Meteo, sem cadastro nem chave.")
            return
        if self._busy:
            return
        self._busy = True
        self.status.setText("Atualizando…")
        self.ctx.run(
            lambda: self.ctx.weather.forecast(place, force=force),
            done=self._show,
            fail=self._failed,
            owner=self,
        )

    def _failed(self, error: BaseException) -> None:
        self._busy = False
        self.status.setText(f"Sem conexão com o Open-Meteo agora ({error}).")

    def _show(self, forecast: Forecast) -> None:
        self._busy = False
        self._loaded_at = time.time()
        self._show_empty(False)
        self.icon.setPixmap(icons.pixmap(forecast.icon, _tint(forecast.icon), 60))
        self.temp.setText(f"{forecast.temp:.0f}°")
        self.place.setText(forecast.place)
        self.desc.setText(forecast.description)
        while self.chips.count():
            child = self.chips.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        for icon_name, text in (
            ("thermo", f"Sensação {forecast.feels:.0f}°"),
            ("droplet", f"Umidade {forecast.humidity}%"),
            ("wind", f"Vento {forecast.wind:.0f} km/h"),
        ):
            self.chips.addWidget(_chip(icon_name, text))
        self.chips.addStretch(1)
        self._fill_week(forecast)
        self.status.setText(f"Open-Meteo · atualizado {ago(self._loaded_at)}")

    def _fill_week(self, forecast: Forecast) -> None:
        while self.grid.count():
            child = self.grid.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        days = forecast.days[:7]
        if not days:
            return
        low = min(d["min"] for d in days)
        high = max(d["max"] for d in days)
        today = date.today()
        for row, day in enumerate(days):
            when = date.fromisoformat(day["date"])
            delta = (when - today).days
            name = "Hoje" if delta == 0 else "Amanhã" if delta == 1 else WEEKDAYS_SHORT[when.weekday()]
            name_label = label(name.capitalize(), "")
            name_label.setFixedWidth(66)
            glyph = label()
            glyph.setPixmap(icons.pixmap(icon_for(day["code"]), _tint(icon_for(day["code"])), 20))
            glyph.setToolTip(describe(day["code"]))
            rain = label(f"{day['rain']}%" if day.get("rain", 0) >= 20 else "", "")
            rain.setStyleSheet(f"color: {theme.SKY}; font-size: 11px; font-weight: 600;")
            rain.setFixedWidth(34)
            lo = label(f"{day['min']:.0f}°", "Muted")
            lo.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lo.setFixedWidth(34)
            bar = TempRange()
            bar.set_range(day["min"], day["max"], low, high)
            hi = label(f"{day['max']:.0f}°", "")
            hi.setFixedWidth(34)
            desc = ElidedLabel(describe(day["code"]), "Faint")
            for column, widget in enumerate((name_label, glyph, rain, desc, lo, bar, hi)):
                self.grid.addWidget(widget, row, column, Qt.AlignmentFlag.AlignVCenter)
        self.grid.setColumnStretch(3, 2)
        self.grid.setColumnStretch(5, 3)

    def on_show(self) -> None:
        if time.time() - self._loaded_at > 15 * 60:
            self.load()
