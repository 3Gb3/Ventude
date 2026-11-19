"""Tudo que as telas compartilham: dados, serviços e ajustes vivos."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ventude.core.config import Settings
from ventude.core.store import Store
from ventude.core.tasks import Tasks
from ventude.limits.manager import Fetcher, LimitsManager
from ventude.services.market import MarketService
from ventude.services.media import MediaService
from ventude.services.news import NewsService
from ventude.services.weather import WeatherService


class AppContext(QObject):
    settings_changed = Signal(object)
    data_changed = Signal(str)  # "tasks" | "events" | "shortcuts"
    navigate_requested = Signal(str)
    hold_open = Signal(bool)  # diálogos nativos: não recolher a ilha
    quit_requested = Signal()
    reset_position_requested = Signal()

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
        self.media = media or MediaService()
        self.weather = WeatherService(store)
        self.market = MarketService(store)
        self.news = NewsService(store)
        self.limits = LimitsManager(store, tasks, lambda: self.settings, home=home, fetchers=fetchers)

    def update_settings(self, **changes: object) -> Settings:
        updated = self.settings.copy(**changes)
        if updated == self.settings:
            return updated
        self.settings = updated
        self.store.save_settings(updated)
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
