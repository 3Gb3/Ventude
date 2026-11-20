"""COM mínimo via ctypes (sem pywin32/comtypes): GUIDs, CoCreateInstance e chamadas pela vtable.

Usado pelo áudio (Core Audio). Cada thread que chama COM precisa de `init()` antes.
"""

from __future__ import annotations

import ctypes
import sys
import uuid
from ctypes import wintypes
from typing import Any

HRESULT = ctypes.c_long
CLSCTX_ALL = 0x17
COINIT_MULTITHREADED = 0x0
S_OK, S_FALSE = 0, 1
RPC_E_CHANGED_MODE = -2147417850  # 0x80010106: a thread já é STA (ex.: a da interface do Qt)


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def guid(text: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class PROPVARIANT(ctypes.Structure):
    """Só o necessário para ler textos (VT_LPWSTR). 24 bytes no Windows de 64 bits."""

    _fields_ = [
        ("vt", wintypes.USHORT),
        ("r1", wintypes.USHORT),
        ("r2", wintypes.USHORT),
        ("r3", wintypes.USHORT),
        ("ptr", ctypes.c_void_p),
        ("extra", ctypes.c_void_p),
    ]


VT_LPWSTR = 31


class ComError(OSError):
    def __init__(self, hr: int, what: str) -> None:
        super().__init__(f"{what} falhou (0x{hr & 0xFFFFFFFF:08X})")
        self.hr = hr


def available() -> bool:
    return sys.platform == "win32"


def _ole32() -> Any:
    ole = ctypes.windll.ole32  # type: ignore[attr-defined]
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = HRESULT
    ole.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole.CoCreateInstance.restype = HRESULT
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    ole.PropVariantClear.argtypes = [ctypes.POINTER(PROPVARIANT)]
    ole.PropVariantClear.restype = HRESULT
    return ole


def init() -> None:
    """Inicializa o COM nesta thread (chamadas repetidas são inofensivas)."""
    hr = _ole32().CoInitializeEx(None, COINIT_MULTITHREADED)
    if hr not in (S_OK, S_FALSE, RPC_E_CHANGED_MODE):
        raise ComError(hr, "CoInitializeEx")


class ComPtr:
    """Ponteiro para uma interface COM. Libere com release() (ou use com `with`)."""

    def __init__(self, pointer: int | None, name: str = "") -> None:
        self.ptr = ctypes.c_void_p(pointer)
        self.name = name

    def __bool__(self) -> bool:
        return bool(self.ptr.value)

    def __enter__(self) -> ComPtr:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()

    def call(self, index: int, argtypes: list[Any], *args: Any, what: str = "") -> int:
        """Chama o método `index` da vtable; levanta ComError se o HRESULT for de falha."""
        if not self.ptr.value:
            raise ComError(-1, what or f"{self.name}[{index}]")
        vtable = ctypes.cast(self.ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        prototype = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *argtypes)
        hr = prototype(vtable[index])(self.ptr, *args)
        if hr < 0:
            raise ComError(hr, what or f"{self.name}[{index}]")
        return hr

    def out_ptr(self, index: int, argtypes: list[Any], *args: Any, name: str = "", what: str = "") -> ComPtr:
        """Chama um método cujo último argumento é `void**` e devolve a interface recebida."""
        out = ctypes.c_void_p()
        self.call(index, [*argtypes, ctypes.POINTER(ctypes.c_void_p)], *args, ctypes.byref(out), what=what)
        return ComPtr(out.value, name)

    def query(self, iid: GUID, name: str = "") -> ComPtr:
        return self.out_ptr(0, [ctypes.POINTER(GUID)], ctypes.byref(iid), name=name, what="QueryInterface")

    def release(self) -> None:
        if self.ptr.value:
            vtable = ctypes.cast(self.ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtable[2])(self.ptr)
            self.ptr = ctypes.c_void_p()


def create(clsid: str, iid: str, name: str = "") -> ComPtr:
    out = ctypes.c_void_p()
    hr = _ole32().CoCreateInstance(
        ctypes.byref(guid(clsid)), None, CLSCTX_ALL, ctypes.byref(guid(iid)), ctypes.byref(out)
    )
    if hr < 0:
        raise ComError(hr, f"CoCreateInstance({name or clsid})")
    return ComPtr(out.value, name)


def take_wstr(pointer: ctypes.c_void_p | int | None) -> str:
    """Lê um LPWSTR alocado pelo COM e libera a memória."""
    value = pointer.value if isinstance(pointer, ctypes.c_void_p) else pointer
    if not value:
        return ""
    try:
        return ctypes.wstring_at(value)
    finally:
        _ole32().CoTaskMemFree(value)


def propvariant_text(value: PROPVARIANT) -> str:
    try:
        return ctypes.wstring_at(value.ptr) if value.vt == VT_LPWSTR and value.ptr else ""
    finally:
        _ole32().PropVariantClear(ctypes.byref(value))
