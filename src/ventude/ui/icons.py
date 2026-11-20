"""Ícones vetoriais de traço único, desenhados em SVG (sem fontes de ícones)."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ventude.ui import theme

PATHS: dict[str, str] = {
    "home": '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1Z"/>',
    "gauge": '<path d="M12 14l4-5"/><path d="M3.3 17A9.9 9.9 0 0 1 2 12a10 10 0 0 1 20 0 '
    '9.9 9.9 0 0 1-1.3 5"/><circle cx="12" cy="14" r="1.6"/>',
    "apps": '<rect x="3" y="3" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="3" width="7.5" '
    'height="7.5" rx="2"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="2"/>'
    '<rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2"/>',
    "calendar": '<rect x="3" y="4.5" width="18" height="16.5" rx="3"/><path d="M8 2.5v4M16 2.5v4M3 10h18"/>',
    "check-square": '<rect x="3" y="3" width="18" height="18" rx="4"/><path d="m8 12 3 3 5-6"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7"/>',
    "sun": '<circle cx="12" cy="12" r="4.2"/><path d="M12 2v2.2M12 19.8V22M4.9 4.9l1.6 1.6'
    'M17.5 17.5l1.6 1.6M2 12h2.2M19.8 12H22M4.9 19.1l1.6-1.6M17.5 6.5l1.6-1.6"/>',
    "moon": '<path d="M20.5 14.2A8.5 8.5 0 0 1 9.8 3.5 8.6 8.6 0 1 0 20.5 14.2Z"/>',
    "cloud": '<path d="M7 19a5 5 0 0 1-.6-9.96A6.5 6.5 0 0 1 19 10.5a4.3 4.3 0 0 1-.5 8.5Z"/>',
    "cloud-sun": '<path d="M9.5 4.6a4.2 4.2 0 0 1 4.8 2.6M5.2 6.2l1 1M7.6 2.5v1.4M2.6 8.6'
    'H4"/><path d="M8.5 20a4.5 4.5 0 0 1-.5-8.97 5.8 5.8 0 0 1 11.2 1.47A3.8 3.8 0 0 1 18.8 20Z"/>',
    "cloud-moon": '<path d="M11.8 4.2a4.4 4.4 0 0 0 5 5"/><path d="M8.5 20a4.5 4.5 0 0 1-.5-8.97 '
    '5.8 5.8 0 0 1 11.2 1.47A3.8 3.8 0 0 1 18.8 20Z"/>',
    "rain": '<path d="M7 15.5a4.5 4.5 0 0 1-.6-8.96A6.5 6.5 0 0 1 19 8a4 4 0 0 1-.5 7.5"/>'
    '<path d="m8.5 18-1 2.5M12.5 18l-1 2.5M16.5 18l-1 2.5"/>',
    "snow": '<path d="M7 15.5a4.5 4.5 0 0 1-.6-8.96A6.5 6.5 0 0 1 19 8a4 4 0 0 1-.5 7.5"/>'
    '<path d="M8 19h.01M12 18h.01M16 19h.01M10 21.5h.01M14 21.5h.01" stroke-width="2.6"/>',
    "storm": '<path d="M7 15.5a4.5 4.5 0 0 1-.6-8.96A6.5 6.5 0 0 1 19 8a4 4 0 0 1-.5 7.5"/>'
    '<path d="m12.5 12-3 5h4l-2.5 4.5"/>',
    "fog": '<path d="M4 9h16M3 13h18M5 17h14"/>',
    "chart": '<path d="M3 3v18h18"/><path d="m7 15 4-5 3.5 3L20 6"/>',
    "news": '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M7 8.5h10M7 12.5h4'
    'M14.5 12.5H17M7 16h10"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 '
    "1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 "
    "0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 "
    "0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 "
    "2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5"
    " 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 "
    '1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "close": '<path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m6 15 6-6 6 6"/>',
    "chevron-left": '<path d="m15 6-6 6 6 6"/>',
    "chevron-right": '<path d="m9 6 6 6-6 6"/>',
    "refresh": '<path d="M20 11.5A8 8 0 0 0 6.3 6.3L4 8.5"/><path d="M4 4v4.5h4.5"/>'
    '<path d="M4 12.5a8 8 0 0 0 13.7 5.2l2.3-2.2"/><path d="M20 20v-4.5h-4.5"/>',
    "play": '<path d="M7 4.8v14.4a1 1 0 0 0 1.5.86l12-7.2a1 1 0 0 0 0-1.72l-12-7.2A1 1 0 0 0 7 4.8Z"'
    ' fill="currentColor"/>',
    "pause": '<rect x="6" y="4.5" width="4" height="15" rx="1.2" fill="currentColor"/>'
    '<rect x="14" y="4.5" width="4" height="15" rx="1.2" fill="currentColor"/>',
    "skip-back": '<path d="M18 5.5v13a.8.8 0 0 1-1.2.7L7.5 13.4a1.6 1.6 0 0 1 0-2.8l9.3-5.8A.8.8 0 '
    '0 1 18 5.5Z" fill="currentColor"/><path d="M5 5v14" stroke-width="2.2"/>',
    "skip-forward": '<path d="M6 5.5v13a.8.8 0 0 0 1.2.7l9.3-5.8a1.6 1.6 0 0 0 0-2.8L7.2 4.8A.8.8 0 '
    '0 0 6 5.5Z" fill="currentColor"/><path d="M19 5v14" stroke-width="2.2"/>',
    "music": '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "trash": '<path d="M4 7h16M9.5 7V4.5h5V7M6 7l1 13h10l1-13"/>',
    "pin": '<path d="M12 17v5M8 3h8l-1 6 3 4H6l3-4Z"/>',
    "folder": '<path d="M3 7.5V6a2 2 0 0 1 2-2h4.2l2 2.5H19a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 '
    '0 1-2-2Z"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 '
    '0 0 0 18"/>',
    "external": '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "power": '<path d="M12 3v8M6.4 6.6a8 8 0 1 0 11.2 0"/>',
    "droplet": '<path d="M12 3s6 6.4 6 11a6 6 0 0 1-12 0c0-4.6 6-11 6-11Z"/>',
    "wind": '<path d="M3 8h11a3 3 0 1 0-3-3M3 12h16a3 3 0 1 1-3 3M3 16h8"/>',
    "thermo": '<path d="M14 14.8V5a2 2 0 0 0-4 0v9.8a4 4 0 1 0 4 0Z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "sunrise": '<path d="M12 2.5v6M9 5.5l3-3 3 3"/><path d="M6 18a6 6 0 0 1 12 0"/>'
    '<path d="M2 18h20M4.3 11.3l1.4 1.4M19.7 11.3l-1.4 1.4M6 21.5h12"/>',
    "bell": '<path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15Z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    "edit": '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16Z"/><path d="m13.5 6.5 4 4"/>',
    "alert": '<path d="M12 4 2.8 19.5h18.4Z"/><path d="M12 10v4M12 17h.01"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    "keyboard": '<rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6.5 10h.01M10 10h.01'
    'M14 10h.01M17.5 10h.01M8 14h8"/>',
    "monitor": '<rect x="2.5" y="4" width="19" height="13" rx="2.5"/><path d="M8 21h8M12 17v4"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01" />',
    "spark": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5'
    'M15.5 8.5 18 6"/>',
    "arrow-up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "arrow-down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "download": '<path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/>',
    "inbox": '<path d="M3 13.5 5.5 5A2 2 0 0 1 7.4 3.5h9.2A2 2 0 0 1 18.5 5L21 13.5V19a2 2 0 0 1-2 2H5a2 '
    '2 0 0 1-2-2Z"/><path d="M3 13.5h5l1.5 2.5h5l1.5-2.5h5"/>',
    "scan": '<path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8'
    "M20 16v2.5a1.5 1.5 0 0 1-1.5 "
    '1.5H16M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16"/><path d="M8 9.5h8M8 12.5h8M8 15.5h5"/>',
    "pipette": '<path d="m14 6.5 3.5 3.5"/>'
    '<path d="M16.2 3.8a2.3 2.3 0 0 1 3.3 3.3l-2.3 2.3-3.3-3.3Z"/>'
    '<path d="M14.5 7.5 6 16v2.5h2.5L17 10"/><path d="M4 20.5 6 18.5"/>',
    "dot": '<circle cx="12" cy="12" r="4" fill="currentColor"/>',
}


@lru_cache(maxsize=512)
def pixmap(name: str, color: str | None = None, size: int = 18, stroke: float = 1.8) -> QPixmap:
    color = color or theme.TEXT_2
    body = PATHS.get(name, PATHS["dot"]).replace("currentColor", color)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</svg>'
    )
    scale = 3
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()
    pm.setDevicePixelRatio(scale)
    return pm


def icon(name: str, color: str | None = None, size: int = 18) -> QIcon:
    return QIcon(pixmap(name, color, size))
