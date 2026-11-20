"""Barra de tarefas do Windows: esconder e restaurar com segurança, e listar as janelas abertas (Dock).

Esconder: ShowWindow(SW_HIDE) em Shell_TrayWnd e Shell_SecondaryTrayWnd e a área útil de cada monitor
vira a tela toda (SPI_SETWORKAREA). As áreas originais ficam guardadas na memória e num arquivo
marcador: se o Ventude fechar de qualquer jeito (até travando), a próxima abertura restaura a barra.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from ventude.core.paths import app_data_dir

log = logging.getLogger(__name__)

SW_HIDE, SW_SHOW, SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 0, 5, 3, 6, 9
SPI_GETWORKAREA, SPI_SETWORKAREA = 0x0030, 0x002F
SPIF_SENDCHANGE = 0x0002
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x00000080, 0x00040000, 0x08000000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
MONITOR_DEFAULTTONEAREST = 2
WM_CLOSE, WM_GETICON = 0x0010, 0x007F
ICON_BIG, ICON_SMALL2 = 1, 2
GCLP_HICON = -14
SMTO_ABORTIFHUNG = 0x0002
TASKBAR_CLASSES = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")
SKIP_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow",
    "XamlExplorerHostIslandWindow",
    "Windows.Internal.Shell.TabProxyWindow",
}

Rect = tuple[int, int, int, int]


def marker_path() -> Path:
    return app_data_dir() / "barra-escondida.json"


def _user32():  # noqa: ANN202
    u = ctypes.windll.user32  # type: ignore[attr-defined]
    u.FindWindowW.restype = wintypes.HWND
    u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    u.FindWindowExW.restype = wintypes.HWND
    u.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.IsWindow.argtypes = [wintypes.HWND]
    u.IsIconic.argtypes = [wintypes.HWND]
    u.IsZoomed.argtypes = [wintypes.HWND]
    u.GetWindow.restype = wintypes.HWND
    u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.MonitorFromWindow.restype = wintypes.HMONITOR
    u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo)]
    u.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    u.SendMessageTimeoutW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    get_long = getattr(u, "GetWindowLongPtrW", None) or u.GetWindowLongW
    get_long.argtypes = [wintypes.HWND, ctypes.c_int]
    get_long.restype = ctypes.c_ssize_t
    get_class = getattr(u, "GetClassLongPtrW", None) or u.GetClassLongW
    get_class.argtypes = [wintypes.HWND, ctypes.c_int]
    get_class.restype = ctypes.c_size_t
    return u


# ---------- barra do Windows ----------
def taskbar_windows() -> list[int]:
    if sys.platform != "win32":
        return []
    user32 = _user32()
    found = []
    main = user32.FindWindowW(TASKBAR_CLASSES[0], None)
    if main:
        found.append(int(main))
    previous = None
    while True:
        previous = user32.FindWindowExW(None, previous, TASKBAR_CLASSES[1], None)
        if not previous:
            break
        found.append(int(previous))
    return found


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _rect(r: wintypes.RECT) -> Rect:
    return (r.left, r.top, r.right, r.bottom)


def monitors() -> list[tuple[Rect, Rect]]:
    """(retângulo do monitor, área útil) de cada monitor."""
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    out: list[tuple[Rect, Rect]] = []
    proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )

    def collect(hmonitor: int, _hdc: int, _rect_ptr: object, _data: int) -> bool:
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            out.append((_rect(info.rcMonitor), _rect(info.rcWork)))
        return True

    callback = proc(collect)
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo)]
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, proc, wintypes.LPARAM]
    user32.EnumDisplayMonitors(None, None, callback, 0)
    return out


def set_work_area(rect: Rect) -> bool:
    if sys.platform != "win32":
        return False
    value = wintypes.RECT(*rect)
    return bool(_user32().SystemParametersInfoW(SPI_SETWORKAREA, 0, ctypes.byref(value), SPIF_SENDCHANGE))


def monitor_scale(rect: Rect) -> float:
    """Escala (1.0 = 100%) do monitor que contém o retângulo, para reservar a altura certa em pixels."""
    if sys.platform != "win32":
        return 1.0
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        shcore = ctypes.windll.shcore  # type: ignore[attr-defined]
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        center = wintypes.POINT((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
        monitor = user32.MonitorFromPoint(center, 2)
        dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
        shcore.GetDpiForMonitor.argtypes = [
            wintypes.HMONITOR,
            ctypes.c_int,
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.UINT),
        ]
        if shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0 and dpi_x.value:
            return dpi_x.value / 96
    except (OSError, AttributeError):
        pass
    return 1.0


def work_rect(monitor: Rect, reserve: int) -> Rect:
    """Área útil com `reserve` px livres embaixo (onde fica a barra do Ventude)."""
    left, top, right, bottom = monitor
    return (left, top, right, max(top + 1, bottom - max(0, reserve)))


class TaskbarHider:
    """Esconde e restaura a barra do Windows. restore() é seguro de chamar várias vezes.

    `reserve(monitor)` diz quantos pixels deixar livres embaixo de cada monitor (a altura da barra do
    Ventude), para as janelas maximizadas não ficarem por baixo dela. 0 = tela toda (ocultar automaticamente).
    """

    def __init__(
        self,
        windows: Callable[[], list[int]] = taskbar_windows,
        show: Callable[[int, int], object] | None = None,
        screens: Callable[[], list[tuple[Rect, Rect]]] = monitors,
        work_area: Callable[[Rect], bool] = set_work_area,
        marker: Path | None = None,
        reserve: Callable[[Rect], int] | None = None,
    ) -> None:
        self._windows, self._screens, self._work_area = windows, screens, work_area
        self._show = show or (lambda hwnd, cmd: _user32().ShowWindow(hwnd, cmd))
        self._visible = (
            (lambda hwnd: bool(_user32().IsWindowVisible(hwnd))) if show is None else (lambda _h: True)
        )
        self.marker = marker or marker_path()
        self.reserve: Callable[[Rect], int] = reserve or (lambda _mon: 0)
        self.hidden = False
        self.peeking = False
        self._saved: list[tuple[Rect, Rect]] = []

    def hide(self) -> bool:
        bars = self._windows()
        if not bars:
            return False
        if not self.hidden:
            self._saved = [(mon, work) for mon, work in self._screens() if work != mon]
            try:
                self.marker.write_text(json.dumps(self._saved), encoding="utf-8")
            except OSError:
                log.warning("Não deu para gravar o marcador da barra escondida", exc_info=True)
        for hwnd in bars:
            self._show(hwnd, SW_HIDE)
        self.peeking = False
        self.apply_work_areas()
        self.hidden = True
        return True

    def apply_work_areas(self) -> None:
        """Área útil de cada monitor = tela menos a barra do Ventude (ou a tela toda)."""
        for mon, work in self._screens():
            wanted = work_rect(mon, self.reserve(mon))
            if work != wanted:
                self._work_area(wanted)

    def peek(self, on: bool) -> None:
        """Mostra a barra do Windows por um tempo (ex.: para usar um ícone da bandeja) e esconde de novo."""
        if not self.hidden:
            return
        self.peeking = on
        for hwnd in self._windows():
            self._show(hwnd, SW_SHOW if on else SW_HIDE)

    def reassert(self) -> None:
        """O Explorer às vezes mostra a barra de novo (ex.: ao reiniciar): esconde outra vez."""
        if not self.hidden or self.peeking:
            return
        for hwnd in self._windows():
            if self._visible(hwnd):
                self._show(hwnd, SW_HIDE)
        self.apply_work_areas()

    def restore(self) -> None:
        saved = self._saved
        if not saved and self.marker.exists():  # o Ventude anterior fechou sem restaurar
            try:
                saved = [(tuple(m), tuple(w)) for m, w in json.loads(self.marker.read_text(encoding="utf-8"))]
            except (OSError, ValueError, TypeError):
                saved = []
        for hwnd in self._windows():
            try:
                self._show(hwnd, SW_SHOW)
            except OSError:
                log.exception("Não deu para mostrar a barra %s", hwnd)
        current = {mon for mon, _work in self._screens()}
        for mon, work in saved:
            if tuple(mon) in current:
                self._work_area(tuple(work))  # type: ignore[arg-type]
        self.marker.unlink(missing_ok=True)
        self._saved = []
        self.hidden = False
        self.peeking = False

    def recover(self) -> bool:
        """Na abertura: se um Ventude anterior deixou a barra escondida, mostra de volta."""
        if self.marker.exists():
            self.restore()
            return True
        return False


# ---------- janelas abertas (Dock) ----------
@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    exe: str
    minimized: bool = False
    maximized: bool = False
    foreground: bool = False
    app_id: str = ""  # AppUserModelID (quando o app define), para agrupar como o Windows
    monitor: Rect = (0, 0, 0, 0)  # retângulo físico do monitor onde a janela está


def _class_name(user32: object, hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)  # type: ignore[attr-defined]
    return buf.value


def _pid(user32: object, hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))  # type: ignore[attr-defined]
    return int(pid.value)


def _cloaked(hwnd: int) -> bool:
    value = wintypes.DWORD()
    try:
        dwm = ctypes.windll.dwmapi  # type: ignore[attr-defined]
        dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        if dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)) != 0:
            return False
    except OSError:
        return False
    return bool(value.value)


def _uwp_pid(user32: object, frame: int) -> int:
    """Apps da Loja ficam dentro do ApplicationFrameHost: o processo real é o da janela filha CoreWindow."""
    found: list[int] = []
    frame_pid = _pid(user32, frame)
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def child(hwnd: int, _data: int) -> bool:
        if _class_name(user32, hwnd) == "Windows.UI.Core.CoreWindow":
            pid = _pid(user32, hwnd)
            if pid and pid != frame_pid:
                found.append(pid)
                return False
        return True

    callback = proc(child)
    user32.EnumChildWindows.argtypes = [wintypes.HWND, proc, wintypes.LPARAM]  # type: ignore[attr-defined]
    user32.EnumChildWindows(frame, callback, 0)  # type: ignore[attr-defined]
    return found[0] if found else frame_pid


def is_app_window(
    visible: bool, title: str, owner: int, exstyle: int, cloaked: bool, cls: str, pid: int, own_pid: int
) -> bool:
    """As regras do Alt + Tab para saber se uma janela é "de aplicativo"."""
    if not visible or not title or owner or cloaked or cls in SKIP_CLASSES or pid == own_pid:
        return False
    if exstyle & WS_EX_TOOLWINDOW and not exstyle & WS_EX_APPWINDOW:
        return False
    return not (exstyle & WS_EX_NOACTIVATE and not exstyle & WS_EX_APPWINDOW)


def _window_monitor(user32: object, hwnd: int) -> Rect:
    try:
        hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)  # type: ignore[attr-defined]
        if not hmon:
            return (0, 0, 0, 0)
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):  # type: ignore[attr-defined]
            return _rect(info.rcMonitor)
    except (OSError, AttributeError):
        pass
    return (0, 0, 0, 0)


def _extended_rect(user32: object, hwnd: int) -> Rect:
    rect = wintypes.RECT()
    try:
        dwm = ctypes.windll.dwmapi  # type: ignore[attr-defined]
        dwm.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        hr = dwm.DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
        )
        if hr == 0 and rect.right > rect.left and rect.bottom > rect.top:
            return _rect(rect)
    except OSError:
        pass
    user32.GetWindowRect(hwnd, ctypes.byref(rect))  # type: ignore[attr-defined]
    return _rect(rect)


def list_windows(own_pid: int | None = None) -> list[WindowInfo]:
    """Janelas reais abertas (como no Alt + Tab), da mais recente para a mais antiga."""
    if sys.platform != "win32":
        return []
    from ventude.services.winutil import process_path

    own_pid = os.getpid() if own_pid is None else own_pid
    from ventude.services import com

    try:
        com.init()  # o AppUserModelID vem de uma interface COM
    except OSError:
        pass
    user32 = _user32()
    get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    handles: list[int] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    callback = proc(lambda hwnd, _data: handles.append(int(hwnd)) or True)
    user32.EnumWindows.argtypes = [proc, wintypes.LPARAM]
    user32.EnumWindows(callback, 0)
    foreground = int(user32.GetForegroundWindow() or 0)
    out: list[WindowInfo] = []
    paths: dict[int, str] = {}
    for hwnd in handles:
        visible = bool(user32.IsWindowVisible(hwnd))
        if not visible:
            continue
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            continue
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        cls = _class_name(user32, hwnd)
        pid = _pid(user32, hwnd)
        owner = int(user32.GetWindow(hwnd, GW_OWNER) or 0)
        exstyle = int(get_long(hwnd, GWL_EXSTYLE))
        if not is_app_window(visible, buf.value, owner, exstyle, _cloaked(hwnd), cls, pid, own_pid):
            continue
        if cls == "ApplicationFrameWindow":
            pid = _uwp_pid(user32, hwnd)
        if pid not in paths:
            paths[pid] = process_path(pid)
        out.append(
            WindowInfo(
                hwnd,
                buf.value,
                pid,
                paths[pid],
                bool(user32.IsIconic(hwnd)),
                bool(user32.IsZoomed(hwnd)),
                hwnd == foreground,
                window_app_id(hwnd),
                _window_monitor(user32, hwnd),
            )
        )
    return out


def window_icon(hwnd: int) -> int:
    """HICON da janela (0 se não tiver). Não espera janelas travadas."""
    if sys.platform != "win32":
        return 0
    user32 = _user32()
    result = ctypes.c_size_t()
    for kind in (ICON_BIG, ICON_SMALL2):
        if user32.SendMessageTimeoutW(hwnd, WM_GETICON, kind, 0, SMTO_ABORTIFHUNG, 80, ctypes.byref(result)):
            if result.value:
                return int(result.value)
    get_class = getattr(user32, "GetClassLongPtrW", None) or user32.GetClassLongW
    return int(get_class(hwnd, GCLP_HICON) or 0)


def activate(hwnd: int) -> bool:
    from ventude.services.winutil import force_foreground

    return force_foreground(hwnd)


def minimize(hwnd: int) -> None:
    _user32().ShowWindow(hwnd, SW_MINIMIZE)


def toggle_maximize(hwnd: int) -> None:
    user32 = _user32()
    user32.ShowWindow(hwnd, SW_RESTORE if user32.IsZoomed(hwnd) else SW_MAXIMIZE)


def close(hwnd: int) -> None:
    _user32().PostMessageW(hwnd, WM_CLOSE, 0, 0)


def foreground_rect(own_pid: int | None = None) -> tuple[Rect, Rect] | None:
    """(retângulo visual da janela ativa, retângulo do monitor dela) em pixels físicos."""
    if sys.platform != "win32":
        return None
    user32 = _user32()
    hwnd = int(user32.GetForegroundWindow() or 0)
    if not hwnd or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return None
    if _pid(user32, hwnd) == (os.getpid() if own_pid is None else own_pid):
        return None
    if _class_name(user32, hwnd) in SKIP_CLASSES:
        return None
    mon = _window_monitor(user32, hwnd)
    if mon == (0, 0, 0, 0):
        return None
    return _extended_rect(user32, hwnd), mon


def covering_window(monitor: Rect, own_pid: int | None = None, fullscreen_only: bool = False) -> bool:
    """A janela em primeiro plano está maximizada (ou, com `fullscreen_only`, em tela cheia) neste monitor?"""
    if sys.platform != "win32":
        return False
    user32 = _user32()
    hwnd = user32.GetForegroundWindow()
    if not hwnd or _pid(user32, hwnd) == (os.getpid() if own_pid is None else own_pid):
        return False
    if _class_name(user32, hwnd) in SKIP_CLASSES:
        return False
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    left, top, right, bottom = monitor
    center_x, center_y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
    if not (left <= center_x < right and top <= center_y < bottom):
        return False
    if user32.IsZoomed(hwnd) and not fullscreen_only:
        return True
    return rect.left <= left and rect.top <= top and rect.right >= right and rect.bottom >= bottom


def window_app_id(hwnd: int) -> str:
    """AppUserModelID da janela (o que o Windows usa para agrupar na barra). '' quando o app não define."""
    if sys.platform != "win32" or not hwnd:
        return ""
    from ventude.services import com

    shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
    shell32.SHGetPropertyStoreForWindow.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(com.GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell32.SHGetPropertyStoreForWindow.restype = ctypes.c_long
    out = ctypes.c_void_p()
    iid = com.guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")  # IPropertyStore
    try:
        if (
            shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(iid), ctypes.byref(out)) < 0
            or not out.value
        ):
            return ""
    except OSError:
        return ""
    store = com.ComPtr(out.value, "IPropertyStore")
    key = com.PROPERTYKEY(com.guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5)  # PKEY_AppUserModel_ID
    value = com.PROPVARIANT()
    try:
        store.call(
            5,  # GetValue
            [ctypes.POINTER(com.PROPERTYKEY), ctypes.POINTER(com.PROPVARIANT)],
            ctypes.byref(key),
            ctypes.byref(value),
        )
        return com.propvariant_text(value)
    except OSError:
        return ""
    finally:
        store.release()
