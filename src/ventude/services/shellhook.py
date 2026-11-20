"""Eventos reais de janela do Windows via RegisterShellHookWindow."""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QWidget

log = logging.getLogger(__name__)

HSHELL_WINDOWCREATED = 1
HSHELL_WINDOWDESTROYED = 2
HSHELL_WINDOWACTIVATED = 4
HSHELL_REDRAW = 6
HSHELL_WINDOWREPLACED = 13
HSHELL_RUDEAPPACTIVATED = 0x8004
HSHELL_FLASH = 0x8006

WINDOW_LIST_CODES = frozenset(
    {
        HSHELL_WINDOWCREATED,
        HSHELL_WINDOWDESTROYED,
        HSHELL_REDRAW,
        HSHELL_WINDOWREPLACED,
    }
)
ACTIVATED_CODES = frozenset({HSHELL_WINDOWACTIVATED, HSHELL_RUDEAPPACTIVATED})


def classify_shell_event(code: int, hwnd: int) -> tuple[str, int] | None:
    """Classifica um evento SHELLHOOK em ('windows' | 'activated' | 'flashed', hwnd)."""
    code = int(code) & 0xFFFF
    hwnd = int(hwnd or 0)
    if code in WINDOW_LIST_CODES:
        return ("windows", hwnd)
    if code in ACTIVATED_CODES:
        return ("activated", hwnd)
    if code == HSHELL_FLASH and hwnd:
        return ("flashed", hwnd)
    return None


class _HookWindow(QWidget):
    """Janela invisível que recebe a mensagem registrada SHELLHOOK."""

    def __init__(self, owner: ShellEvents) -> None:
        super().__init__(None, Qt.WindowType.Tool)
        self._owner = owner
        self._shell_msg = 0
        self._hwnd = 0
        self._registered = False

    def install(self) -> bool:
        if sys.platform != "win32":
            return False
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterWindowMessageW.restype = wintypes.UINT
        user32.RegisterShellHookWindow.argtypes = [wintypes.HWND]
        user32.RegisterShellHookWindow.restype = wintypes.BOOL
        self._hwnd = int(self.winId())
        if not self._hwnd:
            return False
        self._shell_msg = int(user32.RegisterWindowMessageW("SHELLHOOK") or 0)
        if not self._shell_msg:
            return False
        self._registered = bool(user32.RegisterShellHookWindow(self._hwnd))
        return self._registered

    def uninstall(self) -> None:
        if sys.platform == "win32" and self._registered and self._hwnd:
            try:
                user32 = ctypes.windll.user32  # type: ignore[attr-defined]
                user32.DeregisterShellHookWindow.argtypes = [wintypes.HWND]
                user32.DeregisterShellHookWindow.restype = wintypes.BOOL
                user32.DeregisterShellHookWindow(self._hwnd)
            except OSError:
                pass
        self._registered = False
        self._hwnd = 0

    def nativeEvent(self, event_type: object, message: object) -> tuple[bool, int]:  # noqa: N802
        if self._shell_msg and event_type == b"windows_generic_MSG":
            try:
                msg = wintypes.MSG.from_address(int(message))  # type: ignore[arg-type]
                if msg.message == self._shell_msg:
                    self._owner.handle(int(msg.wParam), int(msg.lParam))
                    return True, 0
            except (TypeError, ValueError):
                pass
        return super().nativeEvent(event_type, message)


class ShellEvents(QObject):
    """Emite sinais de alteração, foco e flash de janelas com debounce e limite por HWND."""

    windows_changed = Signal()
    activated = Signal(int)
    flashed = Signal(int)

    def __init__(
        self,
        parent: QObject | None = None,
        debounce_ms: int = 100,
        flash_cooldown_ms: int = 1000,
        clock: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(parent)
        self._debounce_ms = max(0, debounce_ms)
        self._flash_cooldown = max(0.0, flash_cooldown_ms / 1000.0)
        self._clock = clock or time.monotonic
        self._last_flash: dict[int, float] = {}
        self._window: _HookWindow | None = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self.windows_changed)

    @property
    def active(self) -> bool:
        return self._window is not None and self._window._registered

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        app = QApplication.instance()
        if app is not None and app.platformName().lower() == "offscreen":
            return False
        if self._window is not None:
            return self._window._registered
        win = _HookWindow(self)
        if not win.install():
            win.deleteLater()
            log.debug("RegisterShellHookWindow indisponível nesta sessão")
            return False
        self._window = win
        return True

    def stop(self) -> None:
        self._debounce.stop()
        if self._window is not None:
            self._window.uninstall()
            self._window.close()
            self._window.deleteLater()
            self._window = None

    def handle(self, code: int, hwnd: int) -> None:
        event = classify_shell_event(code, hwnd)
        if event is None:
            return
        kind, target = event
        if kind == "windows":
            if self._debounce_ms == 0:
                self.windows_changed.emit()
            else:
                self._debounce.start(self._debounce_ms)
            return
        if kind == "activated":
            self.activated.emit(target)
            return
        if kind == "flashed":
            now = self._clock()
            last = self._last_flash.get(target)
            if last is not None and (now - last) < self._flash_cooldown:
                return
            if len(self._last_flash) > 256:
                self._last_flash.clear()
            self._last_flash[target] = now
            self.flashed.emit(target)
