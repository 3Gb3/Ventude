"""Tudo que as telas compartilham: dados, serviços e ajustes vivos."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ventude.core.config import Settings
from ventude.core.store import Store
from ventude.core.tasks import Tasks
from ventude.limits.manager import Fetcher, LimitsManager
from ventude.services.market import MarketService
from ventude.services.media import MediaService
from ventude.services.news import NewsService
from ventude.services.weather import Forecast, Place, WeatherService

WEATHER_EVERY = 15 * 60_000


class AppContext(QObject):
    settings_changed = Signal(object)
    data_changed = Signal(str)  # "tasks" | "events" | "shortcuts"
    navigate_requested = Signal(str)
    hold_open = Signal(bool)  # diálogos nativos: não recolher a ilha
    quit_requested = Signal()
    reset_position_requested = Signal()
    tray_message = Signal(str, str)  # aviso que não coube na ilha vai para a bandeja
    weather_changed = Signal(object)  # Forecast | None: ilha, Início e Clima juntos
    weather_failed = Signal(str)

    def __init__(
        self,
        store: Store,
        tasks: Tasks,
        *,
        media: MediaService | None = None,
        fetchers: dict[str, Fetcher] | None = None,
        home: Path | None = None,
    ) -> None:
        super().__init__()
        self.store, self.tasks = store, tasks
        self.settings: Settings = store.load_settings()
        self.media = media or MediaService(source=self.settings.media_source)
        self.media.set_source(self.settings.media_source)
        self.weather = WeatherService(store)
        self.market = MarketService(store)
        self.news = NewsService(store)
        self.limits = LimitsManager(store, tasks, lambda: self.settings, home=home, fetchers=fetchers)
        self.weather_focus = ""  # dia (ISO) que a aba Clima deve abrir nos detalhes
        self.forecast: Forecast | None = None
        self.weather_busy = False
        self._weather_city = self.settings.weather_city_json
        self._weather_again = False
        self._located = False
        from ventude.ui.toast import Reminders

        self.reminders = Reminders(store)
        self.weather_timer = QTimer(self, interval=WEATHER_EVERY)
        self.weather_timer.timeout.connect(lambda: self.refresh_weather(force=True))
        self.settings_changed.connect(self._weather_settings)

    def start(self) -> None:
        """Serviços que rodam sozinhos (chamado pelo app; os testes não chamam)."""
        self.weather_timer.start()
        QTimer.singleShot(1200, self.refresh_weather)
        self.reminders.start()
        if self.settings.layout_version < 4:  # primeira abertura da 1.2: liga as IAs já instaladas
            self.detect_providers(lambda _found: self.update_settings(layout_version=4))

    # ---------- IAs instaladas ----------
    def detect_providers(self, done: Callable[[list[str]], None] | None = None) -> None:
        """Liga Codex, Cursor e Copilot que tiverem login neste PC (e dá anel se couber)."""
        from ventude.limits import detect

        def apply(found: list[str]) -> None:
            settings = self.settings
            new = [p for p in found if p in self.limits.providers() and settings.provider_mode(p) == "off"]
            changes: dict[str, object] = {f"{p}_mode": "auto" for p in new}
            rings = settings.ring_providers
            for provider in new:
                current = settings.copy(ring_providers=rings, **changes)
                if sum(current.ring_enabled(p) for p in self.limits.providers()) < 4:
                    rings = current.with_ring(provider, True)
            if new:
                changes["ring_providers"] = rings
                self.update_settings(**changes)
                for provider in new:
                    self.limits.refresh(provider, force=True)
            if done:
                done(new)

        self.run(detect.installed, self.limits.home, done=apply, owner=self)

    # ---------- clima (um só lugar para a ilha, o Início e a aba Clima) ----------
    def refresh_weather(self, force: bool = False) -> None:
        place = Place.from_json(self.settings.weather_city_json)
        if not place:
            self.forecast = None
            self.weather_changed.emit(None)
            if self.settings.weather_auto_locate and not self._located:
                self._located = True
                self.run(self.weather.locate, done=self._located_at, owner=self)
            return
        if self.weather_busy:
            self._weather_again = self._weather_again or force
            return
        self.weather_busy = True
        city = self.settings.weather_city_json
        self.run(
            lambda: self.weather.forecast(place, force=force),
            done=lambda forecast: self._weather_done(forecast, city),
            fail=self._weather_fail,
            owner=self,
        )

    def _located_at(self, place: Place | None) -> None:
        if place and not self.settings.weather_city_json:
            self.update_settings(weather_city_json=place.to_json())

    def _weather_done(self, forecast: Forecast, city: str = "") -> None:
        self.weather_busy = False
        if city and city != self.settings.weather_city_json:  # a cidade mudou no meio da consulta
            self._weather_again = False
            self.refresh_weather()
            return
        self.forecast = forecast
        self.weather_changed.emit(forecast)
        if self._weather_again:
            self._weather_again = False
            self.refresh_weather(force=True)

    def _weather_fail(self, error: BaseException) -> None:
        self.weather_busy = False
        self.weather_failed.emit(str(error))

    def _weather_settings(self, settings: Settings) -> None:
        if settings.weather_city_json != self._weather_city:
            self._weather_city = settings.weather_city_json
            self.forecast = None
            self.refresh_weather()

    def update_settings(self, **changes: object) -> Settings:
        updated = self.settings.copy(**changes)
        if updated == self.settings:
            return updated
        self.settings = updated
        self.store.save_settings(updated)
        self.media.set_source(updated.media_source)
        self.settings_changed.emit(updated)
        return updated

    def run(
        self,
        fn: Callable[..., object],
        *args: object,
        done: Callable[[object], None] | None = None,
        fail: Callable[[BaseException], None] | None = None,
        owner: QObject | None = None,
    ) -> None:
        self.tasks.run(fn, *args, done=done, fail=fail, owner=owner)

    @contextmanager
    def keep_open(self) -> Iterator[None]:
        self.hold_open.emit(True)
        try:
            yield
        finally:
            self.hold_open.emit(False)
