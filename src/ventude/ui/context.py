"""Tudo que as telas compartilham: dados, serviços e ajustes vivos."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ventude.core.config import LAYOUT_VERSION, Settings
from ventude.core.store import Store
from ventude.core.tasks import Tasks
from ventude.limits.manager import Fetcher, LimitsManager
from ventude.services.audio import AudioService
from ventude.services.caffeine import CaffeineService
from ventude.services.hardware import HardwareMonitor
from ventude.services.indexer import Indexer
from ventude.services.lyrics import LyricsService
from ventude.services.market import MarketService
from ventude.services.media import MediaService
from ventude.services.news import NewsService
from ventude.services.notifications import NotificationCenter
from ventude.services.power import PowerService
from ventude.services.privacy import PrivacyMonitor
from ventude.services.shelf import ShelfService
from ventude.services.shellicons import IconCache
from ventude.services.timer import TimerService
from ventude.services.weather import Forecast, Place, WeatherService
from ventude.services.winnotify import WindowsNotifications

WEATHER_EVERY = 15 * 60_000


def migrate_shortcuts_to_start_pins(store: Store) -> int:
    """Copia atalhos do painel antigo para os fixados do Lançador ('start'), sem duplicar."""
    from ventude.ui.shell.items import find_matching_pin

    moved = 0
    valid_kinds = ("app", "uwp", "folder", "file", "system", "url")
    for shortcut in store.shortcuts():
        kind = shortcut.kind if shortcut.kind in valid_kinds else "url"
        if find_matching_pin(store.pins("start"), kind, shortcut.label, shortcut.target) is None:
            store.add_pin("start", kind, shortcut.label, shortcut.target, first=False)
            moved += 1
    return moved


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
    qr_requested = Signal(str)  # texto/link para mostrar como QR Code dentro da ilha
    tool_requested = Signal(str)  # "color" | "ocr" pedidos fora da ilha (painel rápido, Pesquisa)

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
        self.timer = TimerService()
        self.shelf = ShelfService(store)
        # Serviços da 1.2 "shell": nenhum mexe no sistema antes de start() (os testes não chamam).
        self.hardware = HardwareMonitor(
            self.run, lambda: self.settings.pinned_metrics(), lambda: self.settings.hardware_alerts
        )
        self.audio = AudioService()
        self.privacy = PrivacyMonitor(self.run)
        self.caffeine = CaffeineService()
        self.power = PowerService()
        self.lyrics = LyricsService(store, self.run)
        self.indexer = Indexer(self.run)
        self.icons = IconCache()  # ícones reais do Windows (barra, Iniciar e busca)
        self.notifications = NotificationCenter(store)  # tela de Notificações (Lançador e Doca)
        self.windows_notices = WindowsNotifications(self.run)  # as dos outros apps (central do Windows)
        self.notifications.attach_windows(self.windows_notices)
        self.tray_message.connect(lambda title, text: self.notifications.add(title, text))
        # Ventude Shell (2.0): o app liga estes dois; nos testes ficam vazios e tudo cai no modo da ilha.
        self.shell: object | None = None  # ShellController
        self.power_fx: object | None = None  # PowerFx: "Até logo!" antes de desligar, reiniciar...
        self.lock_screen: object | None = None  # LockScreen: tela de bloqueio do Ventude (só no app)
        self.settings_changed.connect(lambda _s: self.hardware.sync())
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

    def apply_migrations(self) -> None:
        """Aplica migrações de configuração e fixados para a versão atual do layout."""
        version = self.settings.layout_version
        if version >= LAYOUT_VERSION:
            return
        modules = self.settings.enabled_modules() | {"hardware", "audio"}
        if version < 4:
            modules |= {"shelf"}
        changes: dict[str, object] = {
            "modules": ",".join(sorted(modules)),
            "layout_version": LAYOUT_VERSION,
        }
        if version < 6:
            changes.update(replace_taskbar=True, replace_start_menu=True)
        if version < 7:
            # 3.0 (Calma): hora/data/clima vivem só na Ilha; Doca centrada com Esquivar (ou Oculta).
            changes.update(
                pill_date=True,
                pill_weather=True,
                dock_mode="oculta" if self.settings.taskbar_autohide else "esquivar",
            )
            migrate_shortcuts_to_start_pins(self.store)
        if version < 8:
            from ventude.ui.shell.items import dedupe_pins

            dedupe_pins(self.store, "start")
            dedupe_pins(self.store, "taskbar")
        if version < 9:
            # A detecção agora ignora janelas maximizadas: dá para sumir só em jogos e vídeos de verdade.
            # Passar o mouse abre o Início (antes, entrar pela zona do clima abria sempre o Clima).
            changes.update(hide_fullscreen=True, zone_open=False)
        self.update_settings(**changes)
        if version < 4:
            self.detect_providers()

    def start(self) -> None:
        """Serviços que rodam sozinhos (chamado pelo app; os testes não chamam)."""
        self.weather_timer.start()
        QTimer.singleShot(1200, self.refresh_weather)
        self.reminders.start()
        self.apply_migrations()
        self._sync_windows_notices(self.settings)
        self.settings_changed.connect(self._sync_windows_notices)
        self.hardware.start()
        self.audio.start()
        self.privacy.start()
        self.power.start()
        self.lyrics.start()
        self.indexer.start()
        self.shelf.set_clipboard_history(
            self.settings.clipboard_history and self.settings.module_enabled("shelf")
        )
        self.settings_changed.connect(
            lambda s: self.shelf.set_clipboard_history(s.clipboard_history and s.module_enabled("shelf"))
        )

    def _sync_windows_notices(self, settings: Settings) -> None:
        if settings.windows_notifications and not self.windows_notices.active:
            self.windows_notices.start()
        elif not settings.windows_notifications and self.windows_notices.active:
            self.windows_notices.stop()

    def stop(self) -> None:
        """Desfaz o que mexe no sistema (chamado ao sair)."""
        self.windows_notices.timer.stop()
        self.caffeine.stop()
        if self.lock_screen is not None:
            self.lock_screen.stop()  # type: ignore[attr-defined]
        self.audio.stop()
        self.hardware.stop()
        self.privacy.stop()

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

    # ---------- Ventude Shell ----------
    def request_power(self, key: str) -> tuple[bool, str]:
        """Bloquear, suspender, desligar... Com o Shell, passa pela animação ("Até logo!") antes."""
        from ventude.services import power

        if key == "lock" and self.lock_screen is not None:
            # A tela do Ventude cobre tudo; o primeiro toque chama o bloqueio do Windows (senha).
            self.lock_screen.show_lock(auto=False)  # type: ignore[attr-defined]
            return True, ""
        if self.power_fx is not None and self.settings.power_animation:
            self.power_fx.play(key)  # type: ignore[attr-defined]
            return True, ""
        return power.run(key)

    def open_search(self, text: str = "") -> bool:
        """Abre a tela de Pesquisa do Shell (False se o Shell não estiver rodando)."""
        if self.shell is None:
            return False
        self.shell.open_search(text)  # type: ignore[attr-defined]
        return True

    def open_start(self) -> bool:
        if self.shell is None:
            return False
        self.shell.toggle_start()  # type: ignore[attr-defined]
        return True

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
