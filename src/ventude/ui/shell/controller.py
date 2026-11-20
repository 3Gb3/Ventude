"""ShellController: orquestra Doca flutuante, Eco secundário, Lançador, Prévias, Hook e Interruptor."""

from __future__ import annotations

import logging
import sys
import time

from PySide6.QtCore import QObject, QPoint, QRect, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QWidget

from ventude.services import apps, hardware, indexer, launcher, shellhook, system
from ventude.services import taskbar as taskbar_service
from ventude.services.taskbar import TaskbarHider, WindowInfo
from ventude.ui import brand, icons, theme
from ventude.ui.shell import dock_model
from ventude.ui.shell.common import app_pixmap
from ventude.ui.shell.curtain import PowerFx, uptime_seconds
from ventude.ui.shell.dock import Dock
from ventude.ui.shell.fly import FlyingIcon
from ventude.ui.shell.items import TaskItem, build_items, split_by_monitor, store_families
from ventude.ui.shell.launcher import LauncherWindow
from ventude.ui.shell.thumbs import ThumbnailPopup

log = logging.getLogger(__name__)

WINDOWS_FAST_MS = 500
WINDOWS_SLOW_MS = 1500
DODGE_MS = 120
STATUS_MS = 5000
HOVER_MS = 400
PEEK_MS = 12_000
GREET_UPTIME = 240
TASKBAR_SEED = "shell_taskbar_seeded"
START_SEED = "shell_start_seeded"
EXPLORER = r"C:\Windows\explorer.exe"


def physical_rect(screen: object) -> tuple[int, int, int, int]:
    """Retângulo do monitor em pixels físicos (como o Windows informa as janelas)."""
    geometry = screen.geometry()  # type: ignore[attr-defined]
    ratio = screen.devicePixelRatio()  # type: ignore[attr-defined]
    return (
        geometry.x(),
        geometry.y(),
        geometry.x() + round(geometry.width() * ratio),
        geometry.y() + round(geometry.height() * ratio),
    )


class ShellController(QObject):
    """Liga Doca, Eco, Lançador, Controles, ShellEvents, Hook da tecla Win e Cortina."""

    def __init__(
        self,
        ctx: object,
        island: object | None = None,
        hider: TaskbarHider | None = None,
        keys: system.KeyboardHook | None = None,
        fx: PowerFx | None = None,
        events: shellhook.ShellEvents | None = None,
    ) -> None:
        super().__init__()
        self.ctx, self.island = ctx, island
        self.hider = hider or TaskbarHider()
        self.hider.reserve = self._reserve
        self.keys = keys or system.KeyboardHook()
        self.keys.start_pressed.connect(self.win_pressed)
        self.keys.search_pressed.connect(lambda: self.open_search())
        self.keys.controls_pressed.connect(self.open_controls)
        self.keys.slot_pressed.connect(self.activate_slot)
        self.events = events or shellhook.ShellEvents(self)
        self.events.windows_changed.connect(self.refresh_windows)
        self.events.activated.connect(self._on_hwnd_activated)
        self.events.flashed.connect(self._on_hwnd_flashed)

        self.fx = fx or PowerFx(ctx, island=island)
        ctx.power_fx = self.fx  # type: ignore[attr-defined]
        ctx.shell = self  # type: ignore[attr-defined]

        self.docks: list[Dock] = []
        self.echos: list[object] = []
        self.items: list[TaskItem] = []
        self.windows: list[WindowInfo] = []
        self.launcher: LauncherWindow | None = None
        self.thumbs: ThumbnailPopup | None = None
        self.failed = False
        self._wanted = False
        self._busy = False
        self._hover_target: tuple[Dock, TaskItem, QRect] | None = None
        self._last_inside = time.monotonic()
        self._edge_revealed = False
        self._peeking = False
        self.network = ""
        self.wifi = ""
        self._battery_low = False
        self._battery_pct: int | None = None

        self.windows_timer = QTimer(self, interval=WINDOWS_FAST_MS)
        self.windows_timer.timeout.connect(self.refresh_windows)
        self.dodge_timer = QTimer(self, interval=DODGE_MS)
        self.dodge_timer.timeout.connect(self._check_dodge)
        self.reassert_timer = QTimer(self, interval=2000)
        self.reassert_timer.timeout.connect(self.hider.reassert)
        self.status_timer = QTimer(self, interval=STATUS_MS)
        self.status_timer.timeout.connect(self.update_status)
        self.hover_timer = QTimer(self, singleShot=True, interval=HOVER_MS)
        self.hover_timer.timeout.connect(self._show_thumbs)
        self.peek_timer = QTimer(self, singleShot=True, interval=PEEK_MS)
        self.peek_timer.timeout.connect(lambda: self.peek_windows_taskbar(False))

        ctx.settings_changed.connect(lambda _s: self.sync())  # type: ignore[attr-defined]
        ctx.data_changed.connect(self._data_changed)  # type: ignore[attr-defined]
        ctx.audio.changed.connect(lambda _s: self.update_status(network=False))  # type: ignore[attr-defined]
        ctx.power.changed.connect(self._power_pending)  # type: ignore[attr-defined]
        ctx.indexer.apps_ready.connect(lambda _n: self.seed_start_pins())  # type: ignore[attr-defined]
        ctx.icons.changed.connect(lambda _n: self._icons_arrived())  # type: ignore[attr-defined]

    @property
    def taskbars(self) -> list[Dock]:
        return self.docks

    # ---------- ligar e desligar ----------
    @property
    def taskbar_wanted(self) -> bool:
        return bool(self.ctx.settings.replace_taskbar) and sys.platform == "win32"  # type: ignore[attr-defined]

    @property
    def active(self) -> bool:
        return bool(self.docks) and any(d.isVisible() or d.slid for d in self.docks)

    def start(self) -> None:
        if not self.ctx.settings.replace_taskbar:  # type: ignore[attr-defined]
            self.hider.recover()
        self.seed_taskbar_pins()
        self.sync()
        self.maybe_greet()

    def sync(self) -> None:
        wanted = self.taskbar_wanted
        self._sync_keys(wanted)
        if wanted != self._wanted:
            self.failed = False
            self._wanted = wanted
        if wanted and not self.failed:
            self._ensure_docks()
            self._sync_events(True)
            if not self.hider.hidden:
                if not self.hider.hide():
                    log.warning("Barra do Windows não encontrada: a Doca do Ventude aparece mesmo assim")
            else:
                self.hider.apply_work_areas()
            for dock in self.docks:
                dock.place()
            self.windows_timer.setInterval(WINDOWS_SLOW_MS if self.events.active else WINDOWS_FAST_MS)
            for timer in (self.windows_timer, self.dodge_timer, self.reassert_timer, self.status_timer):
                if not timer.isActive():
                    timer.start()
            self.refresh_windows()
            self.update_status()
        else:
            self._sync_events(False)
            self.restore()
        self.sync_echos()
        if self.island is not None and hasattr(self.island, "set_shell_mode"):
            self.island.set_shell_mode(wanted and not self.failed)  # type: ignore[attr-defined]

    def _sync_keys(self, taskbar_on: bool) -> None:
        settings = self.ctx.settings  # type: ignore[attr-defined]
        shell_on = bool(settings.replace_taskbar or settings.replace_start_menu)
        features: set[str] = set()
        if settings.replace_start_menu:
            features.add("launcher")
        if shell_on and getattr(settings, "hotkey_controls", True):
            features.add("controls")
        if taskbar_on and getattr(settings, "hotkey_dock_numbers", True):
            features.add("slots")
        self.keys.set_features(features)
        want = bool(features)
        if want and not self.keys.active:
            self.keys.install()
        elif not want and self.keys.active:
            self.keys.remove()

    def _sync_events(self, want: bool) -> None:
        if want and not self.events.active:
            self.events.start()
        elif not want and self.events.active:
            self.events.stop()

    def _screens(self) -> list[object]:
        screens = QGuiApplication.screens()
        if not self.ctx.settings.taskbar_all_monitors:  # type: ignore[attr-defined]
            primary = QGuiApplication.primaryScreen()
            return [primary] if primary else screens[:1]
        return list(screens)

    def _ensure_docks(self) -> None:
        screens = self._screens()
        current = [d.screen_ for d in self.docks]
        if current == screens:
            return
        for dock in self.docks:
            dock.stop_bar()
            dock.deleteLater()
        self.docks = []
        for screen in screens:
            dock = Dock(self.ctx, self, screen)
            dock.item_clicked.connect(lambda it, anchor, d=dock: self.item_clicked(it, anchor, d))
            dock.item_middle_clicked.connect(lambda it, d=dock: self.launch_item(it, d))
            dock.item_menu_requested.connect(lambda it, pos, d=dock: self.item_menu(it, pos, d))
            dock.slot_hovered.connect(
                lambda it, anchor, entered, d=dock: self.slot_hovered(d, it, anchor, entered)
            )
            dock.pin_reordered.connect(self._reorder_taskbar_pins)
            dock.pin_unpinned.connect(self._unpin_taskbar_pin)
            dock.pin_dropped.connect(self._drop_pin_on_dock)
            self._apply_items_to_dock(dock)
            dock.start_bar(entrance=True)
            self.docks.append(dock)

    def sync_echos(self) -> None:
        from ventude.ui.echo import EchoPill

        settings = self.ctx.settings  # type: ignore[attr-defined]
        island_hidden = bool(getattr(self.island, "_user_hidden", False))
        want = (
            self.taskbar_wanted
            and not self.failed
            and bool(getattr(settings, "island_echo", True))
            and not island_hidden
        )
        screens = list(QGuiApplication.screens() or [])
        island_screen = (
            self.island.screen() if isinstance(self.island, QWidget) else None
        ) or QGuiApplication.primaryScreen()
        secondary = [s for s in screens if s is not island_screen]
        if not want or not secondary:
            for echo in self.echos:
                echo.set_shown(False)  # type: ignore[attr-defined]
            if not secondary:
                for echo in self.echos:
                    echo.deleteLater()  # type: ignore[attr-defined]
                self.echos = []
            return
        current = [e.screen_ for e in self.echos]  # type: ignore[attr-defined]
        if current != secondary:
            for echo in self.echos:
                echo.set_shown(False)  # type: ignore[attr-defined]
                echo.deleteLater()  # type: ignore[attr-defined]
            self.echos = [EchoPill(self.ctx, s) for s in secondary]
        for echo in self.echos:
            echo.place()  # type: ignore[attr-defined]
            echo.set_shown(True)  # type: ignore[attr-defined]

    def screens_changed(self) -> None:
        if self.taskbar_wanted and not self.failed:
            self._ensure_docks()
            self.hider.apply_work_areas()
            for dock in self.docks:
                dock.place()
        self.sync_echos()

    def _reserve(self, monitor: tuple[int, int, int, int]) -> int:
        settings = self.ctx.settings  # type: ignore[attr-defined]
        if not self.taskbar_wanted or getattr(settings, "dock_mode", "esquivar") != "sempre":
            return 0
        if not settings.taskbar_all_monitors and (monitor[0], monitor[1]) != (0, 0):
            return 0
        return round(dock_model.RESERVE_H * taskbar_service.monitor_scale(monitor))

    def restore(self) -> None:
        """Sempre seguro: mostra a barra do Windows de volta e esconde a Doca e Ecos."""
        for timer in (
            self.windows_timer,
            self.dodge_timer,
            self.reassert_timer,
            self.status_timer,
            self.peek_timer,
            self.hover_timer,
        ):
            timer.stop()
        if self.hider.hidden or self.hider.marker.exists():
            self.hider.restore()
        for dock in self.docks:
            dock.stop_bar()
        for echo in self.echos:
            echo.set_shown(False)  # type: ignore[attr-defined]
        if self.thumbs is not None:
            self.thumbs.hide_now()

    def emergency(self) -> None:
        self.failed = True
        try:
            self._sync_events(False)
            self.restore()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao restaurar a barra de tarefas")
        try:
            self.keys.remove()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao remover o atalho da tecla Windows")

    def shutdown(self) -> None:
        self.keys.remove()
        self._sync_events(False)
        self.restore()
        for dock in self.docks:
            dock.deleteLater()
        self.docks.clear()
        for echo in self.echos:
            echo.deleteLater()  # type: ignore[attr-defined]
        self.echos.clear()

    # ---------- fixados ----------
    def seed_taskbar_pins(self) -> None:
        store = self.ctx.store  # type: ignore[attr-defined]
        if store.meta(TASKBAR_SEED):
            return
        found = apps.windows_taskbar_pins()
        for label_text, link in found:
            lowered = label_text.lower()
            if "explorer" in lowered or "explorador" in lowered:
                store.add_pin("taskbar", "app", "Explorador de Arquivos", EXPLORER)
            else:
                store.add_pin("taskbar", "app", label_text, link)
        if not store.pins("taskbar"):
            store.add_pin("taskbar", "app", "Explorador de Arquivos", EXPLORER)
        store.set_meta(TASKBAR_SEED, "1")

    def seed_start_pins(self) -> None:
        store = self.ctx.store  # type: ignore[attr-defined]
        if store.meta(START_SEED):
            return
        entries = list(self.ctx.indexer.apps)  # type: ignore[attr-defined]
        if not entries:
            return
        by_name = {e.name: e for e in entries}
        for name in apps.pick_start_seeds(by_name):
            entry = by_name[name]
            store.add_pin("start", entry.kind, entry.name, entry.path)
        store.set_meta(START_SEED, "1")
        if self.launcher is not None and self.launcher.isVisible():
            self.launcher.refresh_pins()

    def toggle_pin(
        self,
        place: str,
        kind: str,
        label_text: str,
        target: str,
        *,
        start_pos: QPoint | None = None,
    ) -> str:
        store = self.ctx.store  # type: ignore[attr-defined]
        where = "no Lançador" if place == "start" else "na Doca"
        pin = store.pinned(place, target)
        if pin is not None:
            store.remove_pin(pin.id)
            message = f"Desafixado {'do Lançador' if place == 'start' else 'da Doca'}"
            self.ctx.data_changed.emit("pins")  # type: ignore[attr-defined]
            return message
        norm_kind = kind if kind in ("app", "uwp", "folder", "file", "url", "system") else "app"
        store.add_pin(place, norm_kind, label_text, target)
        self.ctx.data_changed.emit("pins")  # type: ignore[attr-defined]
        if place == "taskbar" and start_pos is not None and self.docks:
            new_pin = store.pinned("taskbar", target)
            dock = self._dock_for()
            if new_pin is not None and dock is not None:
                end_pos = dock.slot_screen_rect(f"pin:{new_pin.id}").center()
                px = app_pixmap(self.ctx, norm_kind, target, 40)
                FlyingIcon.fly(px, start_pos, end_pos, self.ctx.settings)  # type: ignore[attr-defined]
        return f"Fixado {where}"

    def _reorder_taskbar_pins(self, ids: list[int]) -> None:
        self.ctx.store.reorder_pins(ids)  # type: ignore[attr-defined]
        self.ctx.data_changed.emit("pins")  # type: ignore[attr-defined]

    def _unpin_taskbar_pin(self, pin_id: int) -> None:
        self.ctx.store.remove_pin(pin_id)  # type: ignore[attr-defined]
        self.ctx.data_changed.emit("pins")  # type: ignore[attr-defined]

    def _drop_pin_on_dock(self, kind: str, label_text: str, target: str, insert_index: int) -> None:
        store = self.ctx.store  # type: ignore[attr-defined]
        norm_kind = kind if kind in ("app", "uwp", "folder", "file", "url", "system") else "app"
        existing = store.pinned("taskbar", target)
        if existing is None:
            store.add_pin("taskbar", norm_kind, label_text, target)
            existing = store.pinned("taskbar", target)
        if existing is not None:
            ids = [p.id for p in store.pins("taskbar") if p.id != existing.id]
            clamped = max(0, min(insert_index, len(ids)))
            ids.insert(clamped, existing.id)
            store.reorder_pins(ids)
        self.ctx.data_changed.emit("pins")  # type: ignore[attr-defined]

    def _data_changed(self, kind: str) -> None:
        if kind == "pins":
            self.refresh_windows(force=True)
            if self.launcher is not None and self.launcher.isVisible():
                self.launcher.refresh_pins()

    # ---------- janelas abertas ----------
    def refresh_windows(self, force: bool = False) -> None:
        if self._busy and not force:
            return
        if not self.docks:
            return
        self._busy = True
        self.ctx.run(  # type: ignore[attr-defined]
            taskbar_service.list_windows,
            done=self._windows_done,
            fail=self._windows_failed,
            owner=self,
        )

    def _windows_failed(self, error: BaseException) -> None:
        self._busy = False
        log.debug("Lista de janelas falhou: %r", error)

    def _windows_done(self, windows: list[WindowInfo]) -> None:
        self._busy = False
        if not self.docks:
            return
        self.apply_windows(windows)

    def _on_hwnd_activated(self, hwnd: int) -> None:
        if not self.windows:
            return
        updated = [
            WindowInfo(
                w.hwnd,
                w.title,
                w.pid,
                w.exe,
                w.minimized,
                w.maximized,
                w.hwnd == hwnd,
                w.app_id,
                w.monitor,
            )
            for w in self.windows
        ]
        self.apply_windows(updated)
        QTimer.singleShot(40, self._check_dodge)

    def _on_hwnd_flashed(self, hwnd: int) -> None:
        for dock in self.docks:
            dock.flash_hwnd(hwnd)

    def _apply_items_to_dock(self, dock: Dock) -> None:
        if len(self.docks) <= 1:
            dock.set_items(self.items)
        else:
            dock.set_items(split_by_monitor(self.items, physical_rect(dock.screen_)))

    def apply_windows(self, windows: list[WindowInfo]) -> None:
        self.windows = list(windows)
        pins = self.ctx.store.pins("taskbar")  # type: ignore[attr-defined]
        families = store_families(self.ctx.indexer.apps)  # type: ignore[attr-defined]
        self.items = build_items(pins, windows, families)
        for dock in self.docks:
            self._apply_items_to_dock(dock)
        if self.thumbs is not None and self.thumbs.isVisible() and self.thumbs.item is not None:
            item = next((i for i in self.items if i.key == self.thumbs.item.key), None)
            if item is None:
                self.thumbs.hide_now()
            else:
                self.thumbs.update_item(item)

    def _icons_arrived(self) -> None:
        for dock in self.docks:
            dock.update()

    # ---------- cliques e slots na Doca ----------
    def activate_slot(self, number: int, new_instance: bool = False) -> None:
        dock = self._dock_for()
        if dock is None:
            return
        item = dock.slot_at(number)
        if item is None:
            return
        if new_instance:
            self.launch_item(item, dock)
        else:
            self.item_clicked(item, dock.slot_screen_rect(item.key), dock)

    def item_clicked(self, item: TaskItem, anchor_rect: QRect, dock: Dock | None = None) -> None:
        windows = item.windows
        if not windows:
            self.launch_item(item, dock)
            return
        if len(windows) == 1:
            window = windows[0]
            if window.foreground and not window.minimized and not item.other_monitor:
                taskbar_service.minimize(window.hwnd)
            else:
                taskbar_service.activate(window.hwnd)
        else:
            current = next((n for n, w in enumerate(windows) if w.foreground), -1)
            target = windows[(current + 1) % len(windows)] if current >= 0 else windows[0]
            taskbar_service.activate(target.hwnd)
            dock = dock or self._dock_for()
            if dock is not None:
                self._show_thumbs(item, anchor_rect, dock)
        QTimer.singleShot(150, self.refresh_windows)

    def launch_item(self, item: TaskItem, dock: Dock | None = None) -> None:
        if not item.launchable:
            return
        if self.launch(item.kind, item.target, item.label, record=False):
            target_dock = dock or self._dock_for()
            if target_dock is not None and not item.windows:
                target_dock.bounce_key(item.key)

    def item_menu(self, item: TaskItem, pos: QPoint, dock: Dock) -> None:
        self.hover_timer.stop()
        if self.thumbs is not None:
            self.thumbs.hide_now()
        menu = brand.ink_menu(dock)
        header = menu.addAction(item.label)
        header.setEnabled(False)
        menu.addSeparator()
        if item.launchable:
            menu.addAction(
                icons.icon("external", theme.TEXT_2, 15),
                "Abrir nova janela",
                lambda: self.launch_item(item, dock),
            )
            pinned_bar = item.pinned
            menu.addAction(
                icons.icon("pin", theme.ACCENT if pinned_bar else theme.TEXT_2, 15),
                "Desafixar da Doca" if pinned_bar else "Fixar na Doca",
                lambda: self.toggle_pin("taskbar", item.kind, item.label, item.target),
            )
            pinned_start = self.ctx.store.pinned("start", item.target) is not None  # type: ignore[attr-defined]
            menu.addAction(
                icons.icon("pin", theme.TEXT_2, 15),
                "Desafixar do Lançador" if pinned_start else "Fixar no Lançador",
                lambda: self.toggle_pin("start", item.kind, item.label, item.target),
            )
        if item.exe:
            menu.addAction(
                icons.icon("folder-open", theme.TEXT_2, 15),
                "Abrir local do arquivo",
                lambda: apps.open_location(item.exe),
            )
        if item.kind == "app" and item.target.lower().endswith((".exe", ".lnk")):
            menu.addAction(
                icons.icon("admin", theme.TEXT_2, 15),
                "Executar como administrador",
                lambda: apps.run_as_admin(item.target),
            )
        if item.windows:
            menu.addSeparator()
            if len(item.windows) == 1:
                window = item.windows[0]
                menu.addAction(
                    icons.icon("restore" if window.maximized else "maximize", theme.TEXT_2, 15),
                    "Restaurar" if window.maximized else "Maximizar",
                    lambda h=window.hwnd: taskbar_service.toggle_maximize(h),
                )
                menu.addAction(
                    icons.icon("close", theme.DANGER, 15),
                    "Fechar janela",
                    lambda h=window.hwnd: taskbar_service.close(h),
                )
            else:
                menu.addAction(
                    icons.icon("close", theme.DANGER, 15),
                    f"Fechar todas as {len(item.windows)} janelas",
                    lambda ws=list(item.windows): [taskbar_service.close(w.hwnd) for w in ws],
                )
        menu.addSeparator()
        mode_menu = menu.addMenu(icons.icon("dock", theme.TEXT_2, 15), "Modo da Doca")
        cur_mode = getattr(self.ctx.settings, "dock_mode", "esquivar")  # type: ignore[attr-defined]
        for label_text, val in (
            ("Esquivar janelas", "esquivar"),
            ("Sempre visível", "sempre"),
            ("Oculta", "oculta"),
        ):
            act = mode_menu.addAction(label_text)
            act.setCheckable(True)
            act.setChecked(cur_mode == val)
            act.triggered.connect(lambda _c=False, v=val: self.ctx.update_settings(dock_mode=v))  # type: ignore[attr-defined]
        menu.addAction(
            icons.icon("monitor", theme.TEXT_2, 15),
            "Mostrar barra do Windows por 12s",
            lambda: self.peek_windows_taskbar(True),
        )
        menu.addAction(icons.icon("settings", theme.TEXT_2, 15), "Ajustes do Shell", self.open_settings)
        dock.menu_open = True
        try:
            menu.exec(pos)
        finally:
            dock.menu_open = False
        QTimer.singleShot(300, lambda: self.refresh_windows(force=True))

    # ---------- prévias ao vivo ----------
    def slot_hovered(self, dock: Dock, item: TaskItem | None, anchor_rect: QRect, entered: bool) -> None:
        if entered and item is not None:
            self._hover_target = (dock, item, anchor_rect)
            if item.windows:
                if self.thumbs is not None and self.thumbs.isVisible():
                    self._show_thumbs(item, anchor_rect, dock)
                else:
                    self.hover_timer.start()
        else:
            self.hover_timer.stop()
            self._hover_target = None
            if self.thumbs is not None and self.thumbs.isVisible():
                self.thumbs.schedule_hide()

    def _show_thumbs(
        self,
        item: TaskItem | None = None,
        anchor_rect: QRect | None = None,
        dock: Dock | None = None,
    ) -> None:
        if item is None or anchor_rect is None or dock is None:
            if self._hover_target is None:
                return
            dock, item, anchor_rect = self._hover_target
        if not item.windows or not dock.isVisible():
            return
        if self.thumbs is None:
            self.thumbs = ThumbnailPopup(self.ctx, self)
        for d in self.docks:
            d.suppress_hover_label = d is dock
        self.thumbs.show_for(item, anchor_rect, dock.dock_top_global(), dock.screen_rect())

    # ---------- abrir apps ----------
    def launch(self, kind: str, target: str, label_text: str, record: bool = True) -> bool:
        if kind == "uwp":
            ok, error = indexer.launch(indexer.entry(label_text, target, "uwp"))
        elif kind == "system":
            ok, error = apps.open_system(target)
        elif kind == "url":
            ok, error = launcher.open_target("url", target)
        else:
            ok, error = launcher.open_target(kind if kind in ("folder", "file") else "app", target)
        if not ok:
            self.ctx.tray_message.emit("Não deu para abrir", f"{label_text}: {error}")  # type: ignore[attr-defined]
            return False
        if record:
            self.ctx.store.record_launch(kind, label_text, target)  # type: ignore[attr-defined]
        self.close_menus()
        for dock in self.docks:
            for item in dock.items:
                if item.target.lower() == target.lower() and not item.windows:
                    dock.bounce_key(item.key)
                    break
        QTimer.singleShot(700, lambda: self.refresh_windows(force=True))
        return True

    # ---------- Lançador & Controles ----------
    def _dock_for(self) -> Dock | None:
        screen = QGuiApplication.screenAt(QCursor.pos())
        for dock in self.docks:
            if dock.screen_ is screen:
                return dock
        return self.docks[0] if self.docks else None

    def _launcher(self) -> LauncherWindow:
        if self.launcher is None:
            self.launcher = LauncherWindow(self.ctx, self)
            self.launcher.set_person(apps.display_name(), apps.account_picture())
            self.launcher.set_power_badge(bool(self.ctx.power.pending))  # type: ignore[attr-defined]
            self.launcher.shown.connect(lambda: self._suspend_island_hover(True))
            self.launcher.hidden_.connect(lambda: self._suspend_island_hover(False))
        return self.launcher

    def _suspend_island_hover(self, on: bool) -> None:
        if self.island is not None and hasattr(self.island, "suspend_hover"):
            self.island.suspend_hover(on)  # type: ignore[attr-defined]

    def open_search(self, text: str = "") -> None:
        if self.fx.active:
            return
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        rect = screen.geometry() if screen else QRect(0, 0, 1920, 1080)
        self._launcher().open_launcher(rect, text=text)

    def toggle_start(self) -> None:
        if self.fx.active:
            return
        if self.launcher is not None and self.launcher.isVisible() and not self.launcher.closing:
            self.launcher.dismiss()
        else:
            self.open_search("")

    def win_pressed(self) -> None:
        self.toggle_start()

    def open_controls(self) -> None:
        if self.fx.active or self.island is None:
            return
        self.close_menus()
        if hasattr(self.island, "user_hidden"):
            self.island.user_hidden(False)  # type: ignore[attr-defined]
        if hasattr(self.island, "open_controls"):
            self.island.open_controls(by_key=True)  # type: ignore[attr-defined]
        self.sync_echos()

    def close_menus(self) -> None:
        if self.launcher is not None and self.launcher.isVisible():
            self.launcher.dismiss()

    def menus_open(self) -> bool:
        return bool(self.launcher is not None and self.launcher.isVisible())

    def open_settings(self) -> None:
        self.close_menus()
        self.ctx.navigate_requested.emit("settings")  # type: ignore[attr-defined]
        if self.island is not None and hasattr(self.island, "show_shell_settings"):
            QTimer.singleShot(250, self.island.show_shell_settings)  # type: ignore[attr-defined]

    def power_action(self, key: str) -> None:
        self.close_menus()
        QTimer.singleShot(80, lambda: self.ctx.request_power(key))  # type: ignore[attr-defined]

    def account_menu(self, anchor: QWidget) -> None:
        menu = brand.ink_menu(anchor)
        menu.addAction(
            icons.icon("user", theme.TEXT_2, 15),
            "Configurações da conta",
            lambda: self.launch("system", "ms-settings:yourinfo", "Contas", record=False),
        )
        menu.addAction(icons.icon("lock", theme.TEXT_2, 15), "Bloquear", lambda: self.power_action("lock"))
        menu.addAction(
            icons.icon("log-out", theme.TEXT_2, 15),
            "Sair da sessão",
            lambda: self.power_action("logoff"),
        )
        menu.adjustSize()
        menu.exec(anchor.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 6)))

    def _power_pending(self, pending: bool) -> None:
        if self.launcher is not None:
            self.launcher.set_power_badge(pending)

    def maybe_greet(self) -> None:
        settings = self.ctx.settings  # type: ignore[attr-defined]
        if not (settings.shell_greeting and self.taskbar_wanted and settings.power_animation):
            return
        if uptime_seconds() > GREET_UPTIME:
            return
        forecast = self.ctx.forecast  # type: ignore[attr-defined]
        detail = f"{forecast.temp:.0f}° e {forecast.description.lower()}" if forecast else ""
        QTimer.singleShot(400, lambda: self.fx.greet(apps.display_name(), detail))

    def peek_windows_taskbar(self, on: bool = True) -> None:
        if not self.hider.hidden:
            return
        self._peeking = on
        for dock in self.docks:
            dock.setVisible(not on)
        self.hider.peek(on)
        if on:
            self.peek_timer.start()

    # ---------- estado na ilha ----------
    def update_status(self, network: bool = True) -> None:
        if network:
            try:
                self.network = hardware.network_kind()
                self.wifi = apps.wifi_name() if self.network == "wifi" else ""
            except OSError:
                self.network, self.wifi = "", ""
            battery = hardware.battery() if sys.platform == "win32" else None
            if battery is not None:
                self._battery_low = battery.percent <= 15 and not battery.charging
                self._battery_pct = battery.percent
            else:
                self._battery_low = False
                self._battery_pct = None
        state = self.ctx.audio.state  # type: ignore[attr-defined]
        if self.island is not None and hasattr(self.island, "set_status"):
            from ventude.ui.island import StatusState

            self.island.set_status(  # type: ignore[attr-defined]
                StatusState(
                    network=self.network,
                    wifi=self.wifi,
                    speaker_muted=bool(state.master_muted),
                    battery_low=self._battery_low,
                    battery_pct=self._battery_pct,
                )
            )

    # ---------- dodge e borda inferior ----------
    def _check_dodge(self) -> None:
        if self._peeking or not self.docks:
            return
        thumbs_open = bool(self.thumbs is not None and self.thumbs.isVisible())
        if not thumbs_open:
            for dock in self.docks:
                dock.suppress_hover_label = False
        mode = getattr(self.ctx.settings, "dock_mode", "esquivar")  # type: ignore[attr-defined]
        fg = taskbar_service.foreground_rect()
        fg_rect, fg_mon = fg if fg is not None else (None, None)
        pos = QCursor.pos()
        now = time.monotonic()

        for dock in self.docks:
            mon_phys = physical_rect(dock.screen_)
            fullscreen = taskbar_service.covering_window(mon_phys, fullscreen_only=True)
            dock.fullscreen = fullscreen
            sgeom = dock.screen_rect()
            screen_rect = (sgeom.left(), sgeom.top(), sgeom.right() + 1, sgeom.bottom() + 1)
            at_edge = dock_model.edge_reveal(pos.x(), pos.y(), screen_rect)
            inside = dock.isVisible() and not dock.slid and dock.geometry().contains(pos)
            if at_edge:
                self._last_inside = now
                self._edge_revealed = True
            elif inside or dock.menu_open or thumbs_open or dock.dragging:
                self._last_inside = now

            if mode == "oculta":
                hover_reveal = at_edge or inside or (now - self._last_inside <= 0.8)
            else:
                if self._edge_revealed and not (at_edge or inside or now - self._last_inside <= 0.8):
                    self._edge_revealed = False
                hover_reveal = self._edge_revealed

            ratio = dock.screen_.devicePixelRatio()  # type: ignore[attr-defined]
            cap = dock.capsule_rect_on_screen()
            phys_cap = (
                sgeom.x() + round((cap.left() - sgeom.left()) * ratio),
                sgeom.y() + round((cap.top() - sgeom.top()) * ratio),
                sgeom.x() + round((cap.right() + 1 - sgeom.left()) * ratio),
                sgeom.y() + round((cap.bottom() + 1 - sgeom.top()) * ratio),
            )
            local_fg = fg_rect if (fg_mon == mon_phys or fg_mon == (0, 0, 0, 0)) else None
            hide = dock_model.should_dodge(
                phys_cap,
                local_fg,
                mode,
                menu_open=dock.menu_open or self.menus_open(),
                thumb_open=thumbs_open,
                dragging=dock.dragging,
                hover_reveal=hover_reveal,
                fullscreen=fullscreen,
            )
            if hide != dock.slid:
                dock.slide(hide)


__all__ = ["ShellController", "physical_rect"]
