"""ShellController: liga e desliga o Ventude Shell e faz as peças conversarem.

- Uma barra de tarefas por monitor (ou só no principal) no lugar da barra do Windows, com espaço reservado
  embaixo para as janelas maximizadas não ficarem por cima; some em tela cheia ou fica escondida até o mouse
  encostar embaixo (ocultar automaticamente).
- Menu Iniciar (logo do Ventude), Pesquisa (tecla Windows, Win + S ou a lupa), painéis da barra e prévias.
- Hook da tecla Windows (opcional) e a cortina de energia ("Até logo!").
- A barra do Windows sempre volta: ao desligar o ajuste, ao sair, em erro não tratado e, se o Ventude for
  morto, na próxima abertura (arquivo marcador do TaskbarHider).
"""

from __future__ import annotations

import logging
import sys
import time

from PySide6.QtCore import QObject, QPoint, QRect, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QWidget

from ventude.services import apps, hardware, indexer, launcher, system
from ventude.services import taskbar as taskbar_service
from ventude.services.taskbar import TaskbarHider
from ventude.ui import icons, theme
from ventude.ui.shell.curtain import PowerFx, uptime_seconds
from ventude.ui.shell.flyouts import (
    CalendarFlyout,
    Flyout,
    PowerFlyout,
    QuickSettings,
    TrayFlyout,
    volume_icon,
)
from ventude.ui.shell.taskbar import (
    HEIGHT,
    Taskbar,
    TaskButton,
    TaskItem,
    build_items,
    store_families,
    styled_menu,
)

log = logging.getLogger(__name__)

WINDOWS_MS = 1000
COVER_MS = 700
EDGE_MS = 140
STATUS_MS = 5000
HOVER_MS = 450
PEEK_MS = 12_000
GREET_UPTIME = 240  # s: só cumprimenta logo depois de ligar o PC
MENU_GAP = 12
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
    def __init__(
        self,
        ctx: object,
        island: object | None = None,
        hider: TaskbarHider | None = None,
        keys: system.KeyboardHook | None = None,
        fx: PowerFx | None = None,
    ) -> None:
        super().__init__()
        self.ctx, self.island = ctx, island
        self.hider = hider or TaskbarHider()
        self.hider.reserve = self._reserve
        self.keys = keys or system.KeyboardHook()
        self.keys.start_pressed.connect(self.win_pressed)
        self.keys.search_pressed.connect(lambda: self.open_search())
        self.fx = fx or PowerFx(ctx)
        ctx.power_fx = self.fx  # type: ignore[attr-defined]
        ctx.shell = self  # type: ignore[attr-defined]
        self.taskbars: list[Taskbar] = []
        self.items: list[TaskItem] = []
        self.start_menu = None
        self.search = None
        self.thumbs = None
        self.flyouts: dict[str, Flyout] = {}
        self.failed = False
        self._wanted = False
        self._busy = False
        self._hovered: TaskButton | None = None
        self._last_inside = time.monotonic()
        self._peeking = False
        self.network = ""
        self.wifi = ""
        self.windows_timer = QTimer(self, interval=WINDOWS_MS)
        self.windows_timer.timeout.connect(self.refresh_windows)
        self.cover_timer = QTimer(self, interval=COVER_MS)
        self.cover_timer.timeout.connect(self._check_cover)
        self.edge_timer = QTimer(self, interval=EDGE_MS)
        self.edge_timer.timeout.connect(self._check_edge)
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
        ctx.weather_changed.connect(self._weather)  # type: ignore[attr-defined]
        ctx.audio.changed.connect(lambda _s: self.update_status(network=False))  # type: ignore[attr-defined]
        ctx.power.changed.connect(self._power_pending)  # type: ignore[attr-defined]
        ctx.indexer.apps_ready.connect(lambda _n: self.seed_start_pins())  # type: ignore[attr-defined]
        ctx.icons.changed.connect(lambda _n: self._icons_arrived())  # type: ignore[attr-defined]

    # ---------- ligar e desligar ----------
    @property
    def taskbar_wanted(self) -> bool:
        return bool(self.ctx.settings.replace_taskbar) and sys.platform == "win32"  # type: ignore[attr-defined]

    @property
    def active(self) -> bool:
        return bool(self.taskbars) and any(t.isVisible() or t.slid for t in self.taskbars)

    def start(self) -> None:
        if not self.ctx.settings.replace_taskbar:  # type: ignore[attr-defined]
            self.hider.recover()  # o Ventude anterior fechou com a barra escondida
        self.seed_taskbar_pins()
        self.sync()
        self.maybe_greet()

    def sync(self) -> None:
        settings = self.ctx.settings  # type: ignore[attr-defined]
        self._sync_keys(bool(settings.replace_start_menu))
        wanted = self.taskbar_wanted
        if wanted != self._wanted:
            self.failed = False  # mexeu no ajuste: tenta de novo
            self._wanted = wanted
        if wanted and not self.failed:
            self._ensure_taskbars()
            if not self.hider.hidden:
                if not self.hider.hide():
                    log.warning("Barra do Windows não encontrada: a do Ventude aparece mesmo assim")
            else:
                self.hider.apply_work_areas()  # ocultar automaticamente mudou a área reservada
            for bar in self.taskbars:
                bar.place()
                bar.set_weather(self.ctx.forecast)  # type: ignore[attr-defined]
                bar.relayout()
            for timer in (self.windows_timer, self.cover_timer, self.reassert_timer, self.status_timer):
                if not timer.isActive():
                    timer.start()
            if settings.taskbar_autohide:
                self.edge_timer.start()
            else:
                self.edge_timer.stop()
                for bar in self.taskbars:
                    if bar.slid:
                        bar.slide(False)
            self.refresh_windows()
            self.update_status()
        else:
            self.restore()
        if self.island is not None and hasattr(self.island, "set_shell_mode"):
            self.island.set_shell_mode(wanted and not self.failed)  # type: ignore[attr-defined]

    def _sync_keys(self, want: bool) -> None:
        if want and not self.keys.active:
            self.keys.install()
        elif not want and self.keys.active:
            self.keys.remove()

    def _screens(self) -> list[object]:
        screens = QGuiApplication.screens()
        if not self.ctx.settings.taskbar_all_monitors:  # type: ignore[attr-defined]
            primary = QGuiApplication.primaryScreen()
            return [primary] if primary else screens[:1]
        return list(screens)

    def _ensure_taskbars(self) -> None:
        screens = self._screens()
        current = [bar.screen_ for bar in self.taskbars]
        if current == screens:
            return
        for bar in self.taskbars:
            bar.stop_bar()
            bar.deleteLater()
        self.taskbars = []
        for screen in screens:
            bar = Taskbar(self.ctx, self, screen)
            bar.start.clicked.connect(lambda b=bar: self.toggle_start(b))
            bar.search.clicked.connect(lambda b=bar: self.toggle_search(b))
            bar.view.clicked.connect(apps.task_view)
            bar.weather.clicked.connect(lambda: self.ctx.navigate_requested.emit("weather"))  # type: ignore[attr-defined]
            bar.chevron.clicked.connect(lambda b=bar: self.toggle_flyout("tray", b.chevron, b))
            bar.quick.clicked.connect(lambda b=bar: self.toggle_flyout("quick", b.quick, b))
            bar.quick.wheel.connect(lambda up, steps: self.ctx.audio.step_master(up, steps))  # type: ignore[attr-defined]
            bar.clock.clicked.connect(lambda b=bar: self.toggle_flyout("calendar", b.clock, b))
            bar.corner.clicked.connect(apps.show_desktop)
            bar.set_items(self.items)
            bar.set_weather(self.ctx.forecast)  # type: ignore[attr-defined]
            bar.start_bar(entrance=True)
            self.taskbars.append(bar)

    def screens_changed(self) -> None:
        if self.taskbar_wanted and not self.failed:
            self._ensure_taskbars()
            self.hider.apply_work_areas()
            for bar in self.taskbars:
                bar.place()

    def _reserve(self, monitor: tuple[int, int, int, int]) -> int:
        settings = self.ctx.settings  # type: ignore[attr-defined]
        if settings.taskbar_autohide or not self.taskbar_wanted:
            return 0
        if not settings.taskbar_all_monitors and (monitor[0], monitor[1]) != (0, 0):
            return 0
        return round(HEIGHT * taskbar_service.monitor_scale(monitor))

    def restore(self) -> None:
        """Sempre seguro: mostra a barra do Windows de volta e esconde a do Ventude."""
        for timer in (
            self.windows_timer,
            self.cover_timer,
            self.edge_timer,
            self.reassert_timer,
            self.status_timer,
            self.peek_timer,
        ):
            timer.stop()
        if self.hider.hidden or self.hider.marker.exists():
            self.hider.restore()
        for bar in self.taskbars:
            bar.stop_bar()
        if self.thumbs is not None:
            self.thumbs.hide_now()
        for flyout in self.flyouts.values():
            flyout.hide()

    def emergency(self) -> None:
        """Erro inesperado: a barra do Windows volta na hora e fica até mexer no ajuste."""
        self.failed = True
        try:
            self.restore()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao restaurar a barra de tarefas")
        try:
            self.keys.remove()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao remover o atalho da tecla Windows")

    def shutdown(self) -> None:
        self.keys.remove()
        self.restore()

    # ---------- fixados ----------
    def seed_taskbar_pins(self) -> None:
        store = self.ctx.store  # type: ignore[attr-defined]
        if store.meta(TASKBAR_SEED):
            return
        found = apps.windows_taskbar_pins()
        for label, link in found:
            lowered = label.lower()
            if "explorer" in lowered or "explorador" in lowered:
                store.add_pin("taskbar", "app", "Explorador de Arquivos", EXPLORER)
            else:
                store.add_pin("taskbar", "app", label, link)
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
        if self.start_menu is not None and self.start_menu.isVisible():
            self.start_menu.refresh_pins()

    def toggle_pin(self, place: str, kind: str, label: str, target: str) -> str:
        store = self.ctx.store  # type: ignore[attr-defined]
        where = "no Iniciar" if place == "start" else "na barra de tarefas"
        pin = store.pinned(place, target)
        if pin is not None:
            store.remove_pin(pin.id)
            message = f"Desafixado {'do Iniciar' if place == 'start' else 'da barra de tarefas'}"
        else:
            store.add_pin(
                place,
                kind if kind in ("app", "uwp", "folder", "file", "url", "system") else "app",
                label,
                target,
            )
            message = f"Fixado {where}"
        self.ctx.data_changed.emit("pins")  # type: ignore[attr-defined]
        return message

    def _data_changed(self, kind: str) -> None:
        if kind == "pins":
            self.refresh_windows(force=True)
            if self.start_menu is not None and self.start_menu.isVisible():
                self.start_menu.refresh_pins()

    # ---------- janelas abertas ----------
    def refresh_windows(self, force: bool = False) -> None:
        if self._busy and not force:
            return
        if not self.taskbars:
            return
        self._busy = True
        self.ctx.run(
            taskbar_service.list_windows, done=self._windows_done, fail=self._windows_failed, owner=self
        )  # type: ignore[attr-defined]

    def _windows_failed(self, error: BaseException) -> None:
        self._busy = False
        log.debug("Lista de janelas falhou: %r", error)

    def _windows_done(self, windows: list[taskbar_service.WindowInfo]) -> None:
        self._busy = False
        self.apply_windows(windows)

    def apply_windows(self, windows: list[taskbar_service.WindowInfo]) -> None:
        pins = self.ctx.store.pins("taskbar")  # type: ignore[attr-defined]
        self.items = build_items(pins, windows, store_families(self.ctx.indexer.apps))  # type: ignore[attr-defined]
        for bar in self.taskbars:
            bar.set_items(self.items)
        if self.thumbs is not None and self.thumbs.isVisible() and self.thumbs.item is not None:
            item = next((i for i in self.items if i.key == self.thumbs.item.key), None)
            if item is None:
                self.thumbs.hide_now()
            else:
                self.thumbs.update_item(item)

    def _icons_arrived(self) -> None:
        for bar in self.taskbars:
            bar.update()
            for button in bar.buttons.values():
                button.update()

    # ---------- cliques na barra ----------
    def item_clicked(self, button: TaskButton) -> None:
        item = button.item
        windows = item.windows
        if not windows:
            self.launch_item(item, button)
            return
        if len(windows) == 1:
            window = windows[0]
            if window.foreground and not window.minimized:
                taskbar_service.minimize(window.hwnd)
            else:
                taskbar_service.activate(window.hwnd)
        else:
            current = next((n for n, w in enumerate(windows) if w.foreground), -1)
            target = windows[(current + 1) % len(windows)] if current >= 0 else windows[0]
            taskbar_service.activate(target.hwnd)
            self._show_thumbs(button)
        QTimer.singleShot(150, self.refresh_windows)

    def launch_item(self, item: TaskItem, button: TaskButton | None = None) -> None:
        if not item.launchable:
            return
        if self.launch(item.kind, item.target, item.label, record=False) and button is not None:
            button.jump()

    def item_menu(self, button: TaskButton, pos: QPoint) -> None:
        item = button.item
        self.hover_timer.stop()
        if self.thumbs is not None:
            self.thumbs.hide_now()
        menu = styled_menu()
        header = menu.addAction(item.label)
        header.setEnabled(False)
        menu.addSeparator()
        if item.launchable:
            menu.addAction(
                icons.icon("external", theme.TEXT_2, 15),
                "Abrir nova janela",
                lambda: self.launch_item(item, button),
            )
        if item.launchable:
            pinned_bar = item.pinned
            menu.addAction(
                icons.icon("pin", theme.ACCENT if pinned_bar else theme.TEXT_2, 15),
                "Desafixar da barra de tarefas" if pinned_bar else "Fixar na barra de tarefas",
                lambda: self.toggle_pin("taskbar", item.kind, item.label, item.target),
            )
            pinned_start = self.ctx.store.pinned("start", item.target) is not None  # type: ignore[attr-defined]
            menu.addAction(
                icons.icon("pin", theme.TEXT_2, 15),
                "Desafixar do Iniciar" if pinned_start else "Fixar no Iniciar",
                lambda: self.toggle_pin("start", item.kind, item.label, item.target),
            )
        if item.exe:
            menu.addAction(
                icons.icon("folder-open", theme.TEXT_2, 15),
                "Abrir local do arquivo",
                lambda: apps.open_location(item.exe),
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
        menu.exec(pos)
        QTimer.singleShot(300, lambda: self.refresh_windows(force=True))

    def taskbar_menu(self, bar: Taskbar, pos: QPoint) -> None:
        settings = self.ctx.settings  # type: ignore[attr-defined]
        menu = styled_menu()
        menu.addAction(
            icons.icon("activity", theme.TEXT_2, 15),
            "Gerenciador de Tarefas",
            lambda: apps.open_system("taskmgr.exe"),
        )
        menu.addAction(
            icons.icon("settings", theme.TEXT_2, 15), "Ajustes da barra de tarefas", self.open_settings
        )
        menu.addSeparator()
        align = menu.addAction(
            "Centralizar os ícones" if settings.taskbar_align == "left" else "Alinhar os ícones à esquerda",
            lambda: self.ctx.update_settings(
                taskbar_align="center" if settings.taskbar_align == "left" else "left"
            ),  # type: ignore[attr-defined]
        )
        align.setIcon(icons.icon("dock", theme.TEXT_2, 15))
        hide = menu.addAction("Ocultar automaticamente")
        hide.setCheckable(True)
        hide.setChecked(settings.taskbar_autohide)
        hide.toggled.connect(lambda on: self.ctx.update_settings(taskbar_autohide=on))  # type: ignore[attr-defined]
        menu.addSeparator()
        menu.addAction(
            icons.icon("monitor", theme.TEXT_2, 15),
            "Voltar para a barra do Windows",
            lambda: self.ctx.update_settings(replace_taskbar=False),  # type: ignore[attr-defined]
        )
        menu.exec(pos)

    # ---------- prévias ao vivo ----------
    def button_hovered(self, button: TaskButton, entered: bool) -> None:
        if entered:
            self._hovered = button
            if button.item.windows:
                if self.thumbs is not None and self.thumbs.isVisible():
                    self._show_thumbs(button)
                else:
                    self.hover_timer.start()
        else:
            self.hover_timer.stop()
            if self.thumbs is not None and self.thumbs.isVisible():
                self.thumbs.schedule_hide()

    def _show_thumbs(self, button: TaskButton | None = None) -> None:
        button = button or self._hovered
        if button is None or not button.item.windows or not button.isVisible():
            return
        if self.thumbs is None:
            from ventude.ui.shell.thumbs import ThumbnailPopup

            self.thumbs = ThumbnailPopup(self.ctx, self)
        bar = button.taskbar
        self.thumbs.show_for(button, bar.y(), bar.screen_rect())

    # ---------- abrir apps ----------
    def launch(self, kind: str, target: str, label: str, record: bool = True) -> bool:
        if kind == "uwp":
            ok, error = indexer.launch(indexer.entry(label, target, "uwp"))
        elif kind == "system":
            ok, error = apps.open_system(target)
        elif kind == "url":
            ok, error = launcher.open_target("url", target)
        else:
            ok, error = launcher.open_target(kind if kind in ("folder", "file") else "app", target)
        if not ok:
            self.ctx.tray_message.emit("Não deu para abrir", f"{label}: {error}")  # type: ignore[attr-defined]
            return False
        if record:
            self.ctx.store.record_launch(kind, label, target)  # type: ignore[attr-defined]
        self.close_menus()
        for bar in self.taskbars:
            for button in bar.buttons.values():
                if button.item.target.lower() == target.lower() and not button.item.windows:
                    button.jump()
        QTimer.singleShot(700, lambda: self.refresh_windows(force=True))
        return True

    # ---------- Iniciar e Pesquisa ----------
    def _bar_for(self, bar: Taskbar | None = None) -> Taskbar | None:
        if bar is not None:
            return bar
        screen = QGuiApplication.screenAt(QCursor.pos())
        for candidate in self.taskbars:
            if candidate.screen_ is screen:
                return candidate
        return self.taskbars[0] if self.taskbars else None

    def menu_rect(self, width: int, height: int, bar: Taskbar | None = None) -> QRect:
        bar = self._bar_for(bar)
        screen = (
            bar.screen_
            if bar is not None
            else (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen())
        )
        area = screen.geometry()  # type: ignore[attr-defined]
        bottom = area.bottom() - (HEIGHT if bar is not None and bar.isVisible() and not bar.slid else 0)
        height = min(height, area.height() - HEIGHT - 2 * MENU_GAP)
        width = min(width, area.width() - 2 * MENU_GAP)
        if bar is not None and self.ctx.settings.taskbar_align == "left":  # type: ignore[attr-defined]
            x = area.left() + MENU_GAP
        else:
            x = area.center().x() - width // 2
        return QRect(x, bottom - height - MENU_GAP + 1, width, height)

    def _start(self):  # noqa: ANN202
        if self.start_menu is None:
            from ventude.ui.shell.start_menu import StartMenu

            self.start_menu = StartMenu(self.ctx, self)
            self.start_menu.set_person(apps.display_name(), apps.account_picture())
            self.start_menu.set_power_badge(bool(self.ctx.power.pending))  # type: ignore[attr-defined]
            self.start_menu.shown.connect(lambda: self._start_state(True))
            self.start_menu.hidden_.connect(lambda: self._start_state(False))
        return self.start_menu

    def _search(self):  # noqa: ANN202
        if self.search is None:
            from ventude.ui.shell.search import SearchScreen

            self.search = SearchScreen(self.ctx, self)
        return self.search

    def _start_state(self, open_: bool) -> None:
        for bar in self.taskbars:
            bar.start.open = open_
            bar.start.update()

    def toggle_start(self, bar: Taskbar | None = None) -> None:
        menu = self._start()
        if menu.isVisible() and not menu.closing:
            menu.dismiss()
            return
        if self.search is not None and self.search.isVisible():
            self.search.dismiss()
        self._close_flyouts()
        from ventude.ui.shell.start_menu import START_SIZE

        menu.open_menu(self.menu_rect(START_SIZE.width(), START_SIZE.height(), bar))
        self._start_state(True)

    def open_search(self, text: str = "", bar: Taskbar | None = None) -> None:
        screen = self._search()
        if self.start_menu is not None and self.start_menu.isVisible():
            self.start_menu.dismiss()
        self._close_flyouts()
        from ventude.ui.shell.search import SEARCH_SIZE

        if screen.isVisible() and not screen.closing:
            if text:
                screen.input.setText(text)
            screen.activateWindow()
            screen.input.setFocus()
            return
        screen.open_search(self.menu_rect(SEARCH_SIZE.width(), SEARCH_SIZE.height(), bar), text)

    def toggle_search(self, bar: Taskbar | None = None) -> None:
        screen = self._search()
        if screen.isVisible() and not screen.closing:
            screen.dismiss()
        else:
            self.open_search(bar=bar)

    def win_pressed(self) -> None:
        """Tecla Windows sozinha: abre (ou fecha) a Pesquisa do Ventude."""
        if self.fx.active:
            return
        self.toggle_search()

    def close_menus(self) -> None:
        if self.start_menu is not None and self.start_menu.isVisible():
            self.start_menu.dismiss()
        if self.search is not None and self.search.isVisible():
            self.search.dismiss()
        self._close_flyouts()

    def menus_open(self) -> bool:
        return any(
            w is not None and w.isVisible() for w in (self.start_menu, self.search, *self.flyouts.values())
        )

    def open_settings(self) -> None:
        self.close_menus()
        self.ctx.navigate_requested.emit("settings")  # type: ignore[attr-defined]
        if self.island is not None and hasattr(self.island, "show_shell_settings"):
            QTimer.singleShot(250, self.island.show_shell_settings)  # type: ignore[attr-defined]

    # ---------- painéis ----------
    def flyout_open(self) -> bool:
        return any(f.isVisible() for f in self.flyouts.values())

    def _close_flyouts(self, keep: str = "") -> None:
        for name, flyout in self.flyouts.items():
            if name != keep and flyout.isVisible():
                flyout.close_animated()

    def _flyout(self, name: str) -> Flyout:
        if name not in self.flyouts:
            factory = {
                "quick": QuickSettings,
                "calendar": CalendarFlyout,
                "tray": TrayFlyout,
                "power": PowerFlyout,
            }
            flyout = factory[name](self.ctx, self)
            if name == "power":
                flyout.hidden_.connect(lambda: QTimer.singleShot(120, self._power_closed))
            self.flyouts[name] = flyout
        return self.flyouts[name]

    def toggle_flyout(self, name: str, anchor: QWidget, bar: Taskbar | None = None) -> None:
        flyout = self._flyout(name)
        if flyout.isVisible() and not flyout.closing:
            flyout.close_animated()
            return
        self._close_flyouts(keep=name)
        if name != "power" and self.start_menu is not None and self.start_menu.isVisible():
            self.start_menu.dismiss()
        if name == "quick":
            flyout.refresh()  # type: ignore[attr-defined]
            self.ctx.run(self._read_network, done=self._network_read, owner=self)  # type: ignore[attr-defined]
        elif name == "calendar":
            flyout.refresh()  # type: ignore[attr-defined]
        elif name == "tray":
            flyout.set_apps([])  # type: ignore[attr-defined]
            flyout.note.setText("Procurando…")  # type: ignore[attr-defined]
            self.ctx.run(apps.tray_apps, done=flyout.set_apps, owner=flyout)  # type: ignore[attr-defined]
        elif name == "power":
            flyout.refresh()  # type: ignore[attr-defined]
        top_left = anchor.mapToGlobal(QPoint(0, 0))
        anchor_rect = QRect(top_left, anchor.size())
        screen = anchor.screen().geometry()
        if name == "power":
            flyout.open_above(anchor_rect, screen, top_left.y() - 4, "right")
        else:
            bar = bar or self._bar_for()
            flyout.open_above(anchor_rect, screen, bar.y() if bar is not None else screen.bottom() - HEIGHT)

    def show_power(self, anchor: QWidget) -> None:
        self.toggle_flyout("power", anchor)

    def _power_closed(self) -> None:
        if self.start_menu is not None and self.start_menu.isVisible():
            self.start_menu._close_if_inactive()

    def account_menu(self, anchor: QWidget) -> None:
        menu = styled_menu()
        menu.addAction(
            icons.icon("user", theme.TEXT_2, 15),
            "Configurações da conta",
            lambda: self.launch("system", "ms-settings:yourinfo", "Contas", record=False),
        )
        menu.addAction(icons.icon("lock", theme.TEXT_2, 15), "Bloquear", lambda: self.power("lock"))
        menu.addAction(
            icons.icon("log-out", theme.TEXT_2, 15), "Sair da sessão", lambda: self.power("logoff")
        )
        menu.adjustSize()
        menu.exec(anchor.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 6)))

    def tile_menu(self, parent: QWidget, tile: object, pos: QPoint) -> None:
        store = self.ctx.store  # type: ignore[attr-defined]
        kind, target, label = tile.kind, tile.target, tile.label  # type: ignore[attr-defined]
        menu = styled_menu(parent)
        menu.addAction(
            icons.icon("external", theme.TEXT_2, 15), "Abrir", lambda: self.launch(kind, target, label)
        )
        if kind in ("app", "uwp", "folder", "file", "system", "url"):
            start_pin = store.pinned("start", target)
            menu.addAction(
                icons.icon("pin", theme.ACCENT if start_pin else theme.TEXT_2, 15),
                "Desafixar do Iniciar" if start_pin else "Fixar no Iniciar",
                lambda: self._pin_and_refresh("start", kind, label, target),
            )
            if start_pin is not None and tile.source == "pinned":  # type: ignore[attr-defined]
                menu.addAction(
                    icons.icon("arrow-up", theme.TEXT_2, 15),
                    "Mover para o início",
                    lambda pid=start_pin.id: (store.move_pin_first(pid), self.ctx.data_changed.emit("pins")),  # type: ignore[attr-defined]
                )
            bar_pin = store.pinned("taskbar", target)
            menu.addAction(
                icons.icon("dock", theme.TEXT_2, 15),
                "Desafixar da barra de tarefas" if bar_pin else "Fixar na barra de tarefas",
                lambda: self._pin_and_refresh("taskbar", kind, label, target),
            )
        if kind == "app" and target.lower().endswith((".exe", ".lnk")):
            menu.addAction(
                icons.icon("admin", theme.TEXT_2, 15),
                "Executar como administrador",
                lambda: (self.close_menus(), apps.run_as_admin(target)),
            )
        if kind in ("app", "file", "folder") and target:
            menu.addAction(
                icons.icon("folder-open", theme.TEXT_2, 15),
                "Abrir local do arquivo",
                lambda: (self.close_menus(), apps.open_location(target)),
            )
        if tile.source == "recent-launch":  # type: ignore[attr-defined]
            menu.addSeparator()
            menu.addAction(
                icons.icon("close", theme.TEXT_2, 15),
                "Remover da lista",
                lambda: (store.forget_launch(target), self.start_menu and self.start_menu.refresh_recent()),
            )
        if tile.source == "app" and kind in ("app", "uwp"):  # type: ignore[attr-defined]
            menu.addSeparator()
            menu.addAction(
                icons.icon("trash", theme.DANGER, 15),
                "Desinstalar…",
                lambda: self.launch("system", "ms-settings:appsfeatures", "Apps instalados", record=False),
            )
        menu.exec(pos)

    def _pin_and_refresh(self, place: str, kind: str, label: str, target: str) -> None:
        self.toggle_pin(place, kind, label, target)

    # ---------- energia ----------
    def power(self, key: str) -> None:
        self.close_menus()
        QTimer.singleShot(140, lambda: self.ctx.request_power(key))  # type: ignore[attr-defined]

    def _power_pending(self, pending: bool) -> None:
        if self.start_menu is not None:
            self.start_menu.set_power_badge(pending)

    def maybe_greet(self) -> None:
        settings = self.ctx.settings  # type: ignore[attr-defined]
        if not (settings.shell_greeting and self.taskbar_wanted and settings.power_animation):
            return
        if uptime_seconds() > GREET_UPTIME:
            return
        forecast = self.ctx.forecast  # type: ignore[attr-defined]
        detail = f"{forecast.temp:.0f}° e {forecast.description.lower()}" if forecast else ""
        QTimer.singleShot(400, lambda: self.fx.greet(apps.display_name(), detail))

    # ---------- barra do Windows por um tempo ----------
    def peek_windows_taskbar(self, on: bool = True) -> None:
        if not self.hider.hidden:
            return
        self._peeking = on
        for bar in self.taskbars:
            bar.setVisible(not on)
        self.hider.peek(on)
        if on:
            self.peek_timer.start()

    # ---------- estado: clima, rede, volume, bateria ----------
    def _weather(self, forecast: object | None) -> None:
        for bar in self.taskbars:
            bar.set_weather(forecast)

    @staticmethod
    def _read_network() -> tuple[str, str]:
        kind = hardware.network_kind()
        return kind, apps.wifi_name() if kind == "wifi" else ""

    def _network_read(self, result: tuple[str, str]) -> None:
        self.network, self.wifi = result
        flyout = self.flyouts.get("quick")
        if flyout is not None:
            flyout.set_network(self.network, self.wifi)  # type: ignore[attr-defined]
        self.update_status(network=False)

    def update_status(self, network: bool = True) -> None:
        if not self.taskbars:
            return
        if network:
            try:
                self.network = hardware.network_kind()
            except OSError:
                self.network = ""
        state = self.ctx.audio.state  # type: ignore[attr-defined]
        volume = volume_icon(state.master, state.master_muted)
        battery = hardware.battery() if sys.platform == "win32" else None
        glyph = ""
        if battery is not None:
            glyph = "battery-charging" if battery.charging else "battery"
        network_icon = {"wifi": "wifi", "ethernet": "ethernet"}.get(self.network, "wifi-off")
        level = f"{round((state.master or 0) * 100)}%" if state.master is not None else "—"
        name = {"wifi": "Wi-Fi", "ethernet": "Ethernet"}.get(self.network, "Sem rede")
        tip = f"{name} · Volume {level}" + (f" · Bateria {battery.percent}%" if battery else "")
        for bar in self.taskbars:
            bar.quick.set_state(network_icon, volume, glyph, f"{battery.percent}%" if battery else "")
            bar.quick.setToolTip(tip)
            bar.relayout()

    # ---------- some e volta ----------
    def _check_cover(self) -> None:
        if self.ctx.settings.taskbar_autohide or self._peeking:  # type: ignore[attr-defined]
            return
        for bar in self.taskbars:
            covered = taskbar_service.covering_window(physical_rect(bar.screen_), fullscreen_only=True)
            if covered and not bar.slid and not self.menus_open():
                bar.slide(True)
            elif not covered and bar.slid:
                bar.slide(False)

    def _check_edge(self) -> None:
        if self._peeking:
            return
        pos = QCursor.pos()
        now = time.monotonic()
        for bar in self.taskbars:
            area = bar.screen_rect()
            at_edge = area.contains(pos) and pos.y() >= area.bottom() - 2
            inside = bar.isVisible() and bar.geometry().contains(pos)
            busy = self.menus_open() or (self.thumbs is not None and self.thumbs.isVisible())
            if at_edge or inside or busy:
                self._last_inside = now
                if bar.slid:
                    bar.slide(False)
            elif not bar.slid and now - self._last_inside > 0.8:
                bar.slide(True)
