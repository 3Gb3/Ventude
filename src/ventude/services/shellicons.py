"""Ícones reais do Windows (os mesmos do Explorer e do Menu Iniciar) para apps, apps da Loja, pastas e
arquivos, via IShellItemImageFactory. Funcionam com transparência e em qualquer tamanho.

A leitura roda numa thread própria (COM em STA) e o resultado volta para a interface por sinal. Na
interface, `IconCache.pixmap()` devolve o ícone já carregado ou pede para carregar e avisa por
`IconCache.changed` quando chegar.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QPixmap

log = logging.getLogger(__name__)

IID_IShellItemImageFactory = "bcc18b79-ba16-442f-80c4-8a59c30c463b"
SIIGBF_BIGGERSIZEOK, SIIGBF_ICONONLY = 0x1, 0x4
COINIT_APARTMENTTHREADED = 0x2
MAX_PIXELS = 256


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", ctypes.c_long),
        ("bmWidth", ctypes.c_long),
        ("bmHeight", ctypes.c_long),
        ("bmWidthBytes", ctypes.c_long),
        ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD),
        ("bmBits", ctypes.c_void_p),
    ]


def parsing_name(kind: str, target: str) -> str:
    """Nome que o shell entende: caminho, ou shell:AppsFolder\\AppID para apps da Loja ('' = sem ícone)."""
    if not target:
        return ""
    if kind == "uwp":
        return f"shell:AppsFolder\\{target}"
    if kind in ("url", "web", "ventude", "calc", "power", "command"):
        return ""
    if kind == "system":
        return target if target.lower().startswith(("shell:", "c:", "\\")) else ""
    return target


def load_image(name: str, size: int) -> QImage | None:
    """Ícone de `name` com `size` px (precisa do COM iniciado nesta thread). None se não der."""
    if sys.platform != "win32" or not name:
        return None
    from ventude.services import com

    shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
    gdi32 = ctypes.windll.gdi32  # type: ignore[attr-defined]
    shell32.SHCreateItemFromParsingName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(com.GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
    gdi32.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    out = ctypes.c_void_p()
    iid = com.guid(IID_IShellItemImageFactory)
    if shell32.SHCreateItemFromParsingName(name, None, ctypes.byref(iid), ctypes.byref(out)) < 0:
        return None
    factory = com.ComPtr(out.value, "IShellItemImageFactory")
    bitmap = ctypes.c_void_p()
    try:
        factory.call(
            3,  # GetImage(SIZE, SIIGBF, HBITMAP*)
            [SIZE, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
            SIZE(size, size),
            SIIGBF_BIGGERSIZEOK | SIIGBF_ICONONLY,
            ctypes.byref(bitmap),
        )
    except OSError:
        return None
    finally:
        factory.release()
    if not bitmap.value:
        return None
    try:
        info = BITMAP()
        gdi32.GetObjectW(bitmap, ctypes.sizeof(BITMAP), ctypes.byref(info))
        width, height = info.bmWidth, abs(info.bmHeight)
        if not info.bmBits or info.bmBitsPixel != 32 or width <= 0 or height <= 0:
            return None
        data = ctypes.string_at(info.bmBits, info.bmWidthBytes * height)
        image = QImage(
            data, width, height, info.bmWidthBytes, QImage.Format.Format_ARGB32_Premultiplied
        ).copy()
    finally:
        gdi32.DeleteObject(bitmap)
    if info.bmHeight > 0:  # DIB de baixo para cima
        image = image.flipped(Qt.Orientation.Vertical)
    if image.width() != size and image.width() > 0:
        image = image.scaled(
            size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
    return image


class IconLoader(QObject):
    """Fila de ícones numa thread própria (COM em STA, como o Explorer)."""

    loaded = Signal(str, int, object)  # nome, tamanho, QImage | None

    def __init__(self) -> None:
        super().__init__()
        self._queue: queue.Queue[tuple[str, int] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def request(self, name: str, size: int) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="ventude-icons", daemon=True)
                self._thread.start()
        self._queue.put((name, size))

    def stop(self) -> None:
        self._queue.put(None)

    def _run(self) -> None:
        if sys.platform == "win32":
            try:
                ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)  # type: ignore[attr-defined]
            except OSError:
                log.debug("COM indisponível para os ícones", exc_info=True)
        while True:
            job = self._queue.get()
            if job is None:
                return
            name, size = job
            try:
                image = load_image(name, size)
            except Exception:  # noqa: BLE001 - um ícone ruim não derruba a fila
                log.debug("Ícone de %s falhou", name, exc_info=True)
                image = None
            self.loaded.emit(name, size, image)


class IconCache(QObject):
    """Ícones já carregados por (nome, tamanho em px). `changed(nome)` avisa quando um chega."""

    changed = Signal(str)

    def __init__(self, loader: IconLoader | None = None) -> None:
        super().__init__()
        self.loader = loader or IconLoader()
        self.loader.loaded.connect(self._loaded)
        self._pixmaps: dict[tuple[str, int], QPixmap | None] = {}
        self._pending: set[tuple[str, int]] = set()
        self.enabled = sys.platform == "win32"

    def pixmap(self, name: str, size: int) -> QPixmap | None:
        """Ícone pronto (com `size` px lógicos, nítido em telas com escala) ou None enquanto carrega."""
        if not name or not self.enabled:
            return None
        pixels = min(MAX_PIXELS, size * 2)
        key = (name.lower(), pixels)
        if key in self._pixmaps:
            return self._pixmaps[key]
        if key not in self._pending:
            self._pending.add(key)
            self.loader.request(name, pixels)
        return None

    def ready(self, name: str, size: int) -> bool:
        return (name.lower(), min(MAX_PIXELS, size * 2)) in self._pixmaps

    def _loaded(self, name: str, size: int, image: object) -> None:
        key = (name.lower(), size)
        self._pending.discard(key)
        pixmap = None
        if isinstance(image, QImage) and not image.isNull():
            pixmap = QPixmap.fromImage(image)
        self._pixmaps[key] = pixmap
        self.changed.emit(name)

    def put(self, name: str, size: int, pixmap: QPixmap | None) -> None:
        """Prévias e testes: ícone pronto sem passar pelo Windows."""
        self._pixmaps[(name.lower(), min(MAX_PIXELS, size * 2))] = pixmap
        self.changed.emit(name)
