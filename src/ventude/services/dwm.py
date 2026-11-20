"""Desktop Window Manager via ctypes: prévias ao vivo das janelas (as mesmas da barra do Windows),
vidro (acrylic) atrás das janelas do Ventude Shell e cantos arredondados do Windows 11.

Tudo é opcional: se o Windows não aceitar, as funções devolvem False/0 e a interface segue sem o efeito.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)

DWM_TNP_RECTDESTINATION, DWM_TNP_RECTSOURCE, DWM_TNP_OPACITY = 0x1, 0x2, 0x4
DWM_TNP_VISIBLE, DWM_TNP_SOURCECLIENTAREAONLY = 0x8, 0x10
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWCP_DONOTROUND, DWMWCP_ROUND, DWMWCP_ROUNDSMALL = 1, 2, 3
WCA_ACCENT_POLICY = 19
ACCENT_DISABLED, ACCENT_ENABLE_BLURBEHIND, ACCENT_ENABLE_ACRYLICBLURBEHIND = 0, 3, 4


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("dwFlags", wintypes.DWORD),
        ("rcDestination", wintypes.RECT),
        ("rcSource", wintypes.RECT),
        ("opacity", ctypes.c_ubyte),
        ("fVisible", wintypes.BOOL),
        ("fSourceClientAreaOnly", wintypes.BOOL),
    ]


class ACCENT_POLICY(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):  # noqa: N801
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]


def _dwm():  # noqa: ANN202
    dwm = ctypes.windll.dwmapi  # type: ignore[attr-defined]
    dwm.DwmRegisterThumbnail.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.POINTER(ctypes.c_void_p)]
    dwm.DwmRegisterThumbnail.restype = ctypes.c_long
    dwm.DwmUnregisterThumbnail.argtypes = [ctypes.c_void_p]
    dwm.DwmUnregisterThumbnail.restype = ctypes.c_long
    dwm.DwmUpdateThumbnailProperties.argtypes = [ctypes.c_void_p, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES)]
    dwm.DwmUpdateThumbnailProperties.restype = ctypes.c_long
    dwm.DwmQueryThumbnailSourceSize.argtypes = [ctypes.c_void_p, ctypes.POINTER(SIZE)]
    dwm.DwmQueryThumbnailSourceSize.restype = ctypes.c_long
    dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwm.DwmSetWindowAttribute.restype = ctypes.c_long
    return dwm


def windows_build() -> int:
    if sys.platform != "win32":
        return 0
    try:
        return int(sys.getwindowsversion().build)  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        return 0


def is_windows_11() -> bool:
    return windows_build() >= 22000


# ---------- prévias ao vivo ----------
def register_thumbnail(destination: int, source: int) -> int:
    """Liga a prévia ao vivo de `source` dentro da janela `destination`. 0 se não deu."""
    if sys.platform != "win32" or not destination or not source:
        return 0
    handle = ctypes.c_void_p()
    try:
        if _dwm().DwmRegisterThumbnail(destination, source, ctypes.byref(handle)) < 0:
            return 0
    except OSError:
        return 0
    return int(handle.value or 0)


def thumbnail_size(handle: int) -> tuple[int, int]:
    size = SIZE()
    if not handle or _dwm().DwmQueryThumbnailSourceSize(handle, ctypes.byref(size)) < 0:
        return 0, 0
    return int(size.cx), int(size.cy)


def place_thumbnail(
    handle: int, rect: tuple[int, int, int, int], visible: bool = True, opacity: int = 255
) -> bool:
    """Posição da prévia em pixels físicos, relativa à área da janela de destino."""
    if not handle:
        return False
    props = DWM_THUMBNAIL_PROPERTIES()
    props.dwFlags = DWM_TNP_RECTDESTINATION | DWM_TNP_VISIBLE | DWM_TNP_OPACITY | DWM_TNP_SOURCECLIENTAREAONLY
    props.rcDestination = wintypes.RECT(*rect)
    props.opacity = max(0, min(255, opacity))
    props.fVisible = bool(visible)
    props.fSourceClientAreaOnly = False
    return _dwm().DwmUpdateThumbnailProperties(handle, ctypes.byref(props)) >= 0


def unregister_thumbnail(handle: int) -> None:
    if handle and sys.platform == "win32":
        try:
            _dwm().DwmUnregisterThumbnail(handle)
        except OSError:
            pass


def fit(source: tuple[int, int], box: tuple[int, int]) -> tuple[int, int]:
    """Tamanho da prévia dentro da caixa, mantendo a proporção da janela."""
    width, height = source
    box_w, box_h = box
    if width <= 0 or height <= 0:
        return box_w, box_h
    scale = min(box_w / width, box_h / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


# ---------- vidro e cantos ----------
def _color_ref(argb: int) -> int:
    """0xAARRGGBB -> 0xAABBGGRR (formato do Windows)."""
    a, r, g, b = (argb >> 24) & 0xFF, (argb >> 16) & 0xFF, (argb >> 8) & 0xFF, argb & 0xFF
    return (a << 24) | (b << 16) | (g << 8) | r


def enable_glass(hwnd: int, tint: int = 0xB0101216) -> bool:
    """Desfoque acrylic atrás da janela (Windows 10 1803+ e 11). `tint` em 0xAARRGGBB."""
    if sys.platform != "win32" or not hwnd or windows_build() < 17134:
        return False
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        apply = user32.SetWindowCompositionAttribute
        apply.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
        apply.restype = wintypes.BOOL
        accent = ACCENT_POLICY(ACCENT_ENABLE_ACRYLICBLURBEHIND, 0, _color_ref(tint), 0)
        data = WINDOWCOMPOSITIONATTRIBDATA(
            WCA_ACCENT_POLICY, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent)
        )
        return bool(apply(hwnd, ctypes.byref(data)))
    except (OSError, AttributeError):
        log.debug("Vidro indisponível", exc_info=True)
        return False


def disable_glass(hwnd: int) -> None:
    if sys.platform != "win32" or not hwnd:
        return
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        accent = ACCENT_POLICY(ACCENT_DISABLED, 0, 0, 0)
        data = WINDOWCOMPOSITIONATTRIBDATA(
            WCA_ACCENT_POLICY, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent)
        )
        user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
    except (OSError, AttributeError):
        pass


def _set_int(hwnd: int, attribute: int, value: int) -> bool:
    if sys.platform != "win32" or not hwnd:
        return False
    data = ctypes.c_int(value)
    try:
        return _dwm().DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data)) >= 0
    except OSError:
        return False


def round_corners(hwnd: int, small: bool = False) -> bool:
    """Cantos arredondados do Windows 11 (no 10 não faz nada)."""
    if not is_windows_11():
        return False
    return _set_int(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUNDSMALL if small else DWMWCP_ROUND)


def square_corners(hwnd: int) -> bool:
    if not is_windows_11():
        return False
    return _set_int(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND)


def border_color(hwnd: int, rgb: int) -> bool:
    """Cor da borda fina que o Windows 11 desenha (0xRRGGBB; 0xFFFFFFFE remove)."""
    if not is_windows_11():
        return False
    value = rgb if rgb in (0xFFFFFFFE, 0xFFFFFFFF) else _color_ref(rgb) & 0xFFFFFF
    return _set_int(hwnd, DWMWA_BORDER_COLOR, value)


def dark_frame(hwnd: int) -> bool:
    return _set_int(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
