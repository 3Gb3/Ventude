"""Gera o ícone do Ventude (tornadinho) e a imagem de compartilhamento do GitHub.

uv run python scripts/make_icon.py

- packaging/ventude.ico: ícone do .exe e do instalador (16 a 256 px, PNG dentro do ICO)
- docs/icon.png: 256 px, usado no topo do README
- docs/social-preview.png: 1280×640 para Settings > Social preview do GitHub
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":  # sem janela, o Qt só acha as fontes do Windows se apontarmos a pasta
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QRadialGradient  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ventude.ui import brand, theme  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    brand.paint_tornado(p, QRectF(0, 0, size, size))
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data.data())


def write_ico(path: Path) -> None:
    images = [png_bytes(render(size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, blob in zip(SIZES, images, strict=True):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset + len(blobs))
        blobs += blob
    path.write_bytes(header + entries + blobs)


def social_preview(path: Path) -> None:
    w, h = 1280, 640
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.fillRect(0, 0, w, h, QColor(theme.BG))
    glow = QRadialGradient(330, 320, 420)
    glow.setColorAt(0.0, QColor(110, 231, 183, 46))
    glow.setColorAt(1.0, QColor(110, 231, 183, 0))
    p.fillRect(0, 0, w, h, glow)
    brand.paint_tornado(p, QRectF(150, 140, 360, 360))

    def text(value: str, size: int, color: str, y: int, weight: QFont.Weight) -> None:
        font = QFont("Segoe UI", 1)
        font.setPixelSize(size)
        font.setWeight(weight)
        p.setFont(font)
        p.setPen(QColor(color))
        p.drawText(
            QRectF(590, y, 640, size * 1.4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value
        )

    text("Ventude", 104, theme.TEXT, 170, QFont.Weight.Bold)
    text("Dynamic Island e shell para Windows", 36, theme.TEXT_2, 310, QFont.Weight.Normal)
    text("Limites de Claude, Gemini, Grok, Codex,", 30, theme.TEXT_2, 380, QFont.Weight.Normal)
    text("Cursor e Copilot sempre à vista", 30, theme.TEXT_2, 422, QFont.Weight.Normal)
    text("github.com/3Gb3/Ventude_Notch", 26, theme.ACCENT, 490, QFont.Weight.DemiBold)
    p.end()
    img.save(str(path), "PNG")


def main() -> None:
    app = QApplication(sys.argv[:1])  # noqa: F841 - o Qt precisa da aplicação para fontes
    write_ico(ROOT / "packaging" / "ventude.ico")
    render(256).save(str(ROOT / "docs" / "icon.png"))
    social_preview(ROOT / "docs" / "social-preview.png")
    sheet = QImage(sum(SIZES) + 10 * len(SIZES) + 10, 276, QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor("#2B2F36"))
    p = QPainter(sheet)
    x = 10
    for size in SIZES:
        p.drawImage(x, 10, render(size))
        x += size + 10
    p.end()
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if out:
        sheet.save(str(out / "icon-sheet.png"))
    print("ventude.ico e social-preview.png gerados")


if __name__ == "__main__":
    main()
