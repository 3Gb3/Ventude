"""Integração com o Windows: iniciar com o sistema, atalho global, tecla Windows e volume."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

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


SPI_GETCLIENTAREAANIMATION = 0x1042


def client_area_animation() -> bool:
    """True quando as animações do Windows estão ligadas (respeita Movimento Reduzido)."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        enabled = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(  # type: ignore[attr-defined]
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0
        )
        return bool(enabled.value) if ok else True
    except (AttributeError, OSError):
        return True


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


# ---------- tecla Windows abre o Ventude (hook de teclado de baixo nível) ----------
VK_LWIN, VK_RWIN, VK_A, VK_S = 0x5B, 0x5C, 0x41, 0x53
WIN_KEYS = (VK_LWIN, VK_RWIN)
DIGIT_VKS = range(0x31, 0x3A)  # 1..9
MODIFIERS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}  # Shift, Ctrl e Alt (esq./dir.)
SHIFT_KEYS = {0x10, 0xA0, 0xA1}
NON_SHIFT_MODS = MODIFIERS - SHIFT_KEYS
MASK_VK = 0xE8  # tecla sem uso: no meio do Win, faz o Windows achar que foi um atalho e não abrir o Iniciar
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP, WM_QUIT = 0x0100, 0x0101, 0x0104, 0x0105, 0x0012
LLKHF_INJECTED = 0x10
KEYEVENTF_EXTENDEDKEY = 0x0001
INPUT_KEYBOARD = 1
PASS, BLOCK = "pass", "block"


class WinKeyLogic:
    """Decide o que fazer com cada tecla quando o Ventude Shell está ligado.

    - Win sozinha (apertou e soltou): abre o Lançador e não deixa o Iniciar abrir ("start").
    - Win + S: abre o Lançador ("search") e engole o S.
    - Win + A: abre Controles ("controls") e engole o A.
    - Win + 1..9 (ou Win + Shift + 1..9): aciona o slot N da Doca ("slot:N" / "slot_new:N").
    - Win + qualquer outra tecla (E, D, L, N, V, Tab, setas, Shift + S...): segue para o Windows.
    Teclas injetadas por programas (inclusive pelo próprio Ventude) passam direto.
    """

    def __init__(self, features: set[str] | None = None) -> None:
        self.features: set[str] = {"launcher"} if features is None else set(features)
        self.wins: set[int] = set()
        self.mods: set[int] = set()
        self.combo = False  # outra tecla passou junto com o Win (atalho do Windows)
        self.swallowed = False  # engolimos uma tecla (Win + S/A/1..9): o Windows precisa da máscara
        self._down_swallowed: set[int] = set()

    def set_features(self, features: set[str]) -> None:
        self.features = set(features)

    def feed(self, vk: int, down: bool, injected: bool = False) -> tuple[str, str]:
        """(pass|block, evento) -> '' | 'start' | 'search' | 'controls' | 'slot:N' | 'slot_new:N' | 'mask'."""
        if injected:
            return PASS, ""
        if vk in MODIFIERS:
            (self.mods.add if down else self.mods.discard)(vk)
            if down and self.wins:
                self.combo = True
            return PASS, ""
        if vk in WIN_KEYS:
            if down:
                if not self.wins:
                    self.combo, self.swallowed = bool(self.mods), False
                    self._down_swallowed.clear()
                self.wins.add(vk)
                return PASS, ""
            self.wins.discard(vk)
            if self.wins:
                return PASS, ""
            self._down_swallowed.clear()
            if self.swallowed:
                return BLOCK, "mask"
            if not self.combo and "launcher" in self.features:
                return BLOCK, "start"
            return PASS, ""
        if not self.wins:
            if vk in self._down_swallowed and not down:
                self._down_swallowed.discard(vk)
                return BLOCK, ""
            return PASS, ""
        if vk == VK_S and not self.mods and "launcher" in self.features:
            if down:
                first = vk not in self._down_swallowed
                self._down_swallowed.add(vk)
                self.swallowed = self.combo = True
                return BLOCK, "search" if first else ""
            self._down_swallowed.discard(vk)
            return BLOCK, ""
        if vk == VK_A and not self.mods and "controls" in self.features:
            if down:
                first = vk not in self._down_swallowed
                self._down_swallowed.add(vk)
                self.swallowed = self.combo = True
                return BLOCK, "controls" if first else ""
            self._down_swallowed.discard(vk)
            return BLOCK, ""
        if vk in DIGIT_VKS and not (self.mods & NON_SHIFT_MODS) and "slots" in self.features:
            digit = vk - 0x30
            if down:
                first = vk not in self._down_swallowed
                self._down_swallowed.add(vk)
                self.swallowed = self.combo = True
                if first:
                    shifted = bool(self.mods & SHIFT_KEYS)
                    return BLOCK, f"slot_new:{digit}" if shifted else f"slot:{digit}"
                return BLOCK, ""
            self._down_swallowed.discard(vk)
            return BLOCK, ""
        self.combo = True
        return PASS, ""


class KeyboardHook(QObject):
    """WH_KEYBOARD_LL numa thread própria (com laço de mensagens). remove() desliga na hora."""

    start_pressed = Signal()
    search_pressed = Signal()
    controls_pressed = Signal()
    slot_pressed = Signal(int, bool)  # (slot 1..9, nova janela?)

    def __init__(self) -> None:
        super().__init__()
        self._features: set[str] = {"launcher"}
        self.logic = WinKeyLogic(self._features)
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook = 0
        self._proc = None

    def set_features(self, features: set[str]) -> None:
        self._features = set(features)
        self.logic.set_features(self._features)

    @property
    def active(self) -> bool:
        return bool(self._hook) and self._thread is not None and self._thread.is_alive()

    def install(self) -> bool:
        if sys.platform != "win32":
            return False
        if self.active:
            return True
        self.logic = WinKeyLogic(self._features)
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), name="ventude-keys", daemon=True)
        self._thread.start()
        ready.wait(2)
        if not self._hook:
            log.warning("Não deu para instalar o atalho da tecla Windows")
        return bool(self._hook)

    def remove(self) -> None:
        thread = self._thread
        if thread is None:
            return
        if thread.is_alive() and self._thread_id:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)  # type: ignore[attr-defined]
            thread.join(timeout=1.5)
        self._thread = None

    def _run(self, ready: threading.Event) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        lresult = ctypes.c_ssize_t

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        hookproc = ctypes.WINFUNCTYPE(lresult, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = lresult
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hookproc, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        def win_held() -> bool:
            return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in WIN_KEYS)

        def proc(code: int, wparam: int, lparam: int) -> int:
            if code == 0:
                data = KBDLLHOOKSTRUCT.from_address(lparam)
                down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                if self.logic.wins and data.vkCode not in WIN_KEYS and not win_held():
                    self.logic.wins.clear()  # perdemos o "soltar" do Win (ex.: tela do UAC): não engole o S
                try:
                    action, event = self.logic.feed(int(data.vkCode), down, bool(data.flags & LLKHF_INJECTED))
                except Exception:  # noqa: BLE001 - nunca travar o teclado
                    action, event = PASS, ""
                if event in ("start", "mask"):
                    release_win_masked(int(data.vkCode))
                if event == "start":
                    self.start_pressed.emit()
                elif event == "search":
                    self.search_pressed.emit()
                elif event == "controls":
                    self.controls_pressed.emit()
                elif event.startswith("slot_new:"):
                    self.slot_pressed.emit(int(event.split(":", 1)[1]), True)
                elif event.startswith("slot:"):
                    self.slot_pressed.emit(int(event.split(":", 1)[1]), False)
                if action == BLOCK:
                    return 1
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._proc = hookproc(proc)
        self._thread_id = kernel32.GetCurrentThreadId()
        self._hook = (
            user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0) or 0
        )
        ready.set()
        if not self._hook:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(self._hook)
        self._hook = 0


def release_win_masked(win_vk: int) -> None:
    """Máscara (desce/sobe) + soltar o Win numa chamada só: o Iniciar não abre e o Win não fica preso."""
    import ctypes
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]

    events = (INPUT * 3)()
    for event, (vk, flags) in zip(
        events,
        ((MASK_VK, 0), (MASK_VK, KEYEVENTF_KEYUP), (win_vk, KEYEVENTF_KEYUP | KEYEVENTF_EXTENDEDKEY)),
        strict=True,
    ):
        event.type = INPUT_KEYBOARD
        event.u.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    sent = user32.SendInput(3, events, ctypes.sizeof(INPUT))
    if sent < 3:  # garantia: nunca deixar o Win "apertado"
        user32.keybd_event(win_vk, 0, KEYEVENTF_KEYUP | KEYEVENTF_EXTENDEDKEY, 0)
