"""Proteção de segredos com DPAPI do Windows (só o seu usuário consegue ler)."""

from __future__ import annotations

import base64
import sys

_PLAIN = b"plain:"


def protect(text: str) -> bytes:
    data = text.encode("utf-8")
    if sys.platform == "win32":
        return _dpapi(data, encrypt=True)
    return _PLAIN + base64.b64encode(data)


def unprotect(blob: bytes) -> str:
    if blob.startswith(_PLAIN):
        return base64.b64decode(blob[len(_PLAIN) :]).decode("utf-8")
    if sys.platform == "win32":
        return _dpapi(blob, encrypt=False).decode("utf-8")
    raise ValueError("segredo ilegível neste sistema")


def _dpapi(data: bytes, *, encrypt: bool) -> bytes:  # pragma: no cover - só Windows
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    ok = fn(
        ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out)
    )  # CRYPTPROTECT_UI_FORBIDDEN
    if not ok:
        raise OSError("DPAPI falhou")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)
