"""Pequenos utilitários do Windows via ctypes: processos, nomes de apps, atalhos .lnk e foco de janelas."""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
SW_RESTORE, SW_SHOW = 9, 5
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010


def is_windows() -> bool:
    return sys.platform == "win32"


def _kernel32():  # noqa: ANN202
    k = ctypes.windll.kernel32  # type: ignore[attr-defined]
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    return k


def open_process(pid: int, access: int = PROCESS_QUERY_LIMITED_INFORMATION) -> int:
    """Handle do processo (0 se não deu). Feche com close_handle()."""
    if not is_windows() or pid <= 0:
        return 0
    return int(_kernel32().OpenProcess(access, False, pid) or 0)


def close_handle(handle: int) -> None:
    if handle:
        _kernel32().CloseHandle(handle)


def handle_path(handle: int) -> str:
    size = wintypes.DWORD(1024)
    buf = ctypes.create_unicode_buffer(size.value)
    if handle and _kernel32().QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
        return buf.value
    return ""


def process_path(pid: int) -> str:
    """Caminho do .exe do processo ('' se o Windows não deixar ver)."""
    handle = open_process(pid)
    try:
        return handle_path(handle)
    finally:
        close_handle(handle)


@lru_cache(maxsize=256)
def exe_description(path: str) -> str:
    """'Descrição do arquivo' do .exe (ex.: 'Google Chrome'), lida dos recursos de versão."""
    if not is_windows() or not path:
        return ""
    try:
        version = ctypes.windll.version  # type: ignore[attr-defined]
        version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        version.GetFileVersionInfoW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        version.VerQueryValueW.argtypes = [
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.UINT),
        ]
        size = version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ""
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path, 0, size, data):
            return ""
        pointer, length = ctypes.c_void_p(), wintypes.UINT()
        langs = ["040904B0", "040904E4", "041604B0", "000004B0"]
        if version.VerQueryValueW(
            data, "\\VarFileInfo\\Translation", ctypes.byref(pointer), ctypes.byref(length)
        ):
            if length.value >= 4:
                raw = ctypes.string_at(pointer.value, 4)
                lang, codepage = int.from_bytes(raw[:2], "little"), int.from_bytes(raw[2:], "little")
                langs.insert(0, f"{lang:04X}{codepage:04X}")
        for code in langs:
            query = f"\\StringFileInfo\\{code}\\FileDescription"
            if version.VerQueryValueW(data, query, ctypes.byref(pointer), ctypes.byref(length)):
                text = ctypes.wstring_at(pointer.value, length.value).strip("\x00 ").strip()
                if text:
                    return text
    except (OSError, AttributeError, ValueError):
        log.debug("Descrição de %s indisponível", path, exc_info=True)
    return ""


GENERIC_DESCRIPTIONS = {"", "application", "aplicativo", "app"}


def app_name(path: str) -> str:
    """Nome amigável do app pelo caminho do .exe: descrição do arquivo ou o nome sem '.exe'."""
    if not path:
        return ""
    stem = Path(path.replace("#", "\\")).stem
    description = exe_description(path) if Path(path).exists() else ""
    if description.lower() in GENERIC_DESCRIPTIONS or len(description) > 40:
        description = ""
    return description or (stem[:1].upper() + stem[1:])


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def _cstring(data: bytes, offset: int, wide: bool) -> str:
    if wide:
        end = offset
        while end + 1 < len(data) and data[end : end + 2] != b"\x00\x00":
            end += 2
        return data[offset:end].decode("utf-16-le", "replace")
    end = data.find(b"\x00", offset)
    return data[offset : end if end >= 0 else len(data)].decode(
        "mbcs" if is_windows() else "latin-1", "replace"
    )


def lnk_target(path: str | Path) -> str:
    """Para onde aponta um atalho .lnk (caminho local), lendo o formato do arquivo direto. '' se não der."""
    try:
        data = Path(path).read_bytes()[:65536]
    except OSError:
        return ""
    if len(data) < 0x4C or data[:4] != b"\x4c\x00\x00\x00":
        return ""
    flags = _u32(data, 0x14)
    pos = 0x4C
    if flags & 0x1:  # HasLinkTargetIDList
        pos += 2 + int.from_bytes(data[pos : pos + 2], "little")
    if not flags & 0x2:  # HasLinkInfo
        return ""
    info = pos
    if info + 28 > len(data):
        return ""
    header_size, info_flags = _u32(data, info + 4), _u32(data, info + 8)
    if not info_flags & 0x1:  # VolumeIDAndLocalBasePath
        return ""
    if header_size >= 0x24 and info + 32 <= len(data):
        base = _cstring(data, info + _u32(data, info + 28), wide=True)
    else:
        base = _cstring(data, info + _u32(data, info + 16), wide=False)
    suffix = _cstring(data, info + _u32(data, info + 24), wide=False) if _u32(data, info + 24) else ""
    return base + suffix


def _user32():  # noqa: ANN202
    u = ctypes.windll.user32  # type: ignore[attr-defined]
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.BringWindowToTop.argtypes = [wintypes.HWND]
    u.IsIconic.argtypes = [wintypes.HWND]
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    u.SwitchToThisWindow.restype = None
    u.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    u.SetWindowPos.restype = wintypes.BOOL
    return u


def keep_topmost(hwnd: int) -> bool:
    """Reafirma HWND_TOPMOST sem roubar o foco da janela ativa."""
    if not is_windows() or not hwnd:
        return False
    try:
        return bool(
            _user32().SetWindowPos(
                hwnd,
                ctypes.c_void_p(HWND_TOPMOST),
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        )
    except (OSError, AttributeError):
        return False


def foreground_window() -> int:
    if not is_windows():
        return 0
    return int(_user32().GetForegroundWindow() or 0)


def force_foreground(hwnd: int) -> bool:
    """Traz a janela para a frente mesmo quando outro app está em foco (trava de primeiro plano)."""
    if not is_windows() or not hwnd:
        return False
    try:
        user32 = _user32()
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        foreground = user32.GetForegroundWindow()
        if foreground and int(foreground) == int(hwnd):
            return True
        mine = kernel32.GetCurrentThreadId()
        theirs = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        attached = bool(theirs and theirs != mine and user32.AttachThreadInput(theirs, mine, True))
        try:
            user32.BringWindowToTop(hwnd)
            ok = bool(user32.SetForegroundWindow(hwnd))
        finally:
            if attached:
                user32.AttachThreadInput(theirs, mine, False)
        if not ok:
            user32.SwitchToThisWindow(hwnd, True)
            ok = int(user32.GetForegroundWindow() or 0) == int(hwnd)
        return ok
    except (OSError, AttributeError):
        log.debug("Não deu para trazer a janela para a frente", exc_info=True)
        return False
