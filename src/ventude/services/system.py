"""Integração com o Windows: iniciar com o sistema e atalho global de teclado."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "Ventude"
HOTKEY_ID = 0x5644  # "VD"
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
VK_SPACE = 0x20
HOTKEY_LABEL = "Ctrl + Alt + Espaço"


def launch_command() -> str:
    """Comando que abre o Ventude sem janela de console."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else exe}" -m ventude'


def autostart_supported() -> bool:
    return sys.platform == "win32"


def autostart_enabled() -> bool:
    if not autostart_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    if not autostart_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        log.exception("Não foi possível alterar a inicialização automática")
        return False


def register_hotkey(hwnd: int) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes

    ok = ctypes.windll.user32.RegisterHotKey(hwnd, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_SPACE)
    if not ok:
        log.info("Atalho %s já está em uso por outro programa", HOTKEY_LABEL)
    return bool(ok)


def unregister_hotkey(hwnd: int) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.UnregisterHotKey(hwnd, HOTKEY_ID)


def fullscreen_app_over(own_hwnd: int) -> bool:
    """True se a janela em primeiro plano ocupa o monitor inteiro onde a ilha está."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        hwnd = user32.GetForegroundWindow()
        if not hwnd or int(hwnd) == int(own_hwnd):
            return False
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        if name.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False
        monitor = user32.MonitorFromWindow(hwnd, 2)
        if monitor != user32.MonitorFromWindow(own_hwnd, 2):
            return False
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        rect = wintypes.RECT()
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        m = info.rcMonitor
        return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
    except Exception:  # noqa: BLE001
        log.debug("Checagem de tela cheia falhou", exc_info=True)
        return False


def is_hotkey_message(message: object) -> bool:
    if sys.platform != "win32":
        return False
    from ctypes import wintypes

    try:
        msg = wintypes.MSG.from_address(int(message))
    except (TypeError, ValueError):
        return False
    return msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID


VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAE, 0xAF
KEYEVENTF_KEYUP = 0x0002


def volume_step(up: bool, steps: int = 1) -> bool:
    """Sobe ou desce o volume do Windows (como as teclas de volume, com o aviso do sistema)."""
    if sys.platform != "win32" or steps <= 0:
        return False
    import ctypes

    key = VK_VOLUME_UP if up else VK_VOLUME_DOWN
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        for _ in range(min(steps, 10)):
            user32.keybd_event(key, 0, 0, 0)
            user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)
    except (AttributeError, OSError):
        log.debug("Não foi possível mudar o volume", exc_info=True)
        return False
    return True
