"""Reconhecimento de texto (OCR) com o próprio Windows (Windows.Media.Ocr), sem dependências extras.

Usa os idiomas de OCR instalados no Windows (Configurações > Hora e idioma > Idioma).
Roda numa thread de trabalho: nunca chame da interface.
"""

from __future__ import annotations

import asyncio
import sys


class OcrError(Exception):
    pass


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winrt.windows.media.ocr  # noqa: F401
    except ImportError:
        return False
    return True


async def _recognize(png: bytes) -> str:  # pragma: no cover - só Windows
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapDecoder, BitmapPixelFormat
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise OcrError("Nenhum idioma de OCR instalado no Windows (Configurações > Idioma).")
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream.get_output_stream_at(0))
    writer.write_bytes(png)
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async(BitmapPixelFormat.BGRA8, BitmapAlphaMode.PREMULTIPLIED)
    result = await engine.recognize_async(bitmap)
    return "\n".join(line.text for line in result.lines).strip()


def recognize(png: bytes) -> str:
    """Texto da imagem (PNG). Levanta OcrError com a explicação em português."""
    if not available():
        raise OcrError("O OCR usa o Windows 10 ou 11: não está disponível neste sistema.")
    try:
        return asyncio.run(_recognize(png))
    except OcrError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise OcrError(f"O Windows não conseguiu ler o texto ({exc}).") from None
