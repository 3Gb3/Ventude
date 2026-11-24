"""Ícones vetoriais de traço único, desenhados em SVG (sem fontes de ícones)."""

from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
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
    "bell-off": '<path d="M10.3 21a1.9 1.9 0 0 0 3.4 0M8.7 3.8A6 6 0 0 1 18 8.8v3.7l1.5 2.5h-1"/>'
    '<path d="M6 10v4.5L4.5 17h10.5M3 3l18 18"/>',
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
    # ---------- Sistema ----------
    "cpu": '<rect x="5" y="5" width="14" height="14" rx="2.5"/><rect x="9" y="9" width="6" height="6" rx="1"/>'
    '<path d="M9.5 2v3M14.5 2v3M9.5 19v3M14.5 19v3M2 9.5h3M2 14.5h3M19 9.5h3M19 14.5h3"/>',
    "memory": '<rect x="2.5" y="6" width="19" height="11" rx="2"/><path d="M7 9.5v4M11 9.5v4M15 9.5v4'
    'M6 17v3M10 17v3M14 17v3M18 17v3"/>',
    "gpu": '<rect x="2" y="6" width="17" height="11" rx="2"/><circle cx="8.5" cy="11.5" r="2.6"/>'
    '<path d="M13.5 9.5h2.5M13.5 13.5h2.5M19 9h3M19 14h3M6 17v2.5M11 17v2.5"/>',
    "hard-drive": '<rect x="3" y="13" width="18" height="7" rx="2"/><path d="M5.6 13 8 5h8l2.4 8"/>'
    '<path d="M7 16.5h.01M10.5 16.5h.01"/>',
    "battery": '<rect x="2.5" y="7" width="17" height="10" rx="2.5"/><path d="M22 10.5v3"/>',
    "battery-low": '<rect x="2.5" y="7" width="17" height="10" rx="2.5"/><path d="M22 10.5v3"/>'
    '<path d="M6.5 10.5v3"/>',
    "battery-charging": '<rect x="2.5" y="7" width="17" height="10" rx="2.5"/><path d="M22 10.5v3"/>'
    '<path d="m11.8 9-2.3 3.2h3l-2.3 3.3"/>',
    "wifi": '<path d="M2.5 9a14 14 0 0 1 19 0M5.5 12.5a9.5 9.5 0 0 1 13 0M8.5 16a5 5 0 0 1 7 0"/>'
    '<path d="M12 19.5h.01" stroke-width="2.6"/>',
    "activity": '<path d="M3 12h4l3-7.5 4 15 3-7.5h4"/>',
    "copy": '<rect x="8.5" y="8.5" width="12.5" height="12.5" rx="2"/>'
    '<path d="M15.5 8.5V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v8.5a2 2 0 0 0 2 2h3.5"/>',
    # ---------- áudio e privacidade ----------
    "volume": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4Z"/><path d="M15.5 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
    "volume-1": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4Z"/><path d="M15.5 9a4 4 0 0 1 0 6"/>',
    "volume-x": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4Z"/><path d="m16 9.5 5 5M21 9.5l-5 5"/>',
    "headphones": '<path d="M4 15.5V12a8 8 0 0 1 16 0v3.5"/><rect x="3" y="14" width="4.5" height="7" rx="1.6"/>'
    '<rect x="16.5" y="14" width="4.5" height="7" rx="1.6"/>',
    "speaker": '<rect x="5" y="2.5" width="14" height="19" rx="2.5"/><circle cx="12" cy="14.5" r="3.5"/>'
    '<path d="M12 6.5h.01" stroke-width="2.4"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M9 21h6"/>',
    "mic-off": '<path d="M15 9.5V6a3 3 0 0 0-5.7-1.3M9 9v2a3 3 0 0 0 4.8 2.4"/>'
    '<path d="M18.5 11a6.5 6.5 0 0 1-1 3.4M5.5 11a6.5 6.5 0 0 0 10.3 5.3M12 17.5V21M9 21h6M3.5 3.5l17 17"/>',
    "camera": '<path d="M3 8.5a2 2 0 0 1 2-2h2.5L9 4.5h6l1.5 2H19a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>'
    '<circle cx="12" cy="13" r="3.5"/>',
    "bluetooth": '<path d="m7 7 10 10-5 4.5V2.5L17 7 7 17"/>',
    # ---------- ferramentas ----------
    "coffee": '<path d="M4 9h12v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5Z"/><path d="M16 10.5h1.5a2.5 2.5 0 0 1 0 5H16"/>'
    '<path d="M8 2.8c-.7.9.7 1.9 0 3M12 2.8c-.7.9.7 1.9 0 3M5 21.5h10"/>',
    "qr": '<rect x="3" y="3" width="7" height="7" rx="1.2"/><rect x="14" y="3" width="7" height="7" rx="1.2"/>'
    '<rect x="3" y="14" width="7" height="7" rx="1.2"/><path d="M14 14h3v3M21 14h.01M14 21h3M17.5 17.5v3.5M21 17v4"/>',
    "calculator": '<rect x="4.5" y="2.5" width="15" height="19" rx="2.5"/><path d="M8 6.5h8M8 11h.01M12 11h.01'
    'M16 11h.01M8 14.5h.01M12 14.5h.01M16 14.5v3.5M8 18h.01M12 18h.01" stroke-linecap="round"/>',
    "terminal": '<rect x="2.5" y="4" width="19" height="16" rx="2.5"/><path d="m7 9.5 3 2.5-3 2.5M12.5 15H17"/>',
    "code": '<path d="m8 7-5 5 5 5M16 7l5 5-5 5M13.5 4l-3 16"/>',
    "window": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 8.5h18"/>',
    "minus": '<path d="M5 12h14"/>',
    "maximize": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "restore": '<rect x="4" y="8" width="12" height="12" rx="2"/><path d="M8 8V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8'
    'a2 2 0 0 1-2 2h-2"/>',
    "dock": '<rect x="2.5" y="15" width="19" height="5.5" rx="2.75"/><path d="M7 17.75h.01M11 17.75h.01'
    'M15 17.75h.01" stroke-width="2.4"/><path d="M5 11V5.5A2.5 2.5 0 0 1 7.5 3h9A2.5 2.5 0 0 1 19 5.5V11"/>',
    "ventude": '<rect x="2.5" y="7.5" width="19" height="9" rx="4.5"/><circle cx="16.5" cy="12" r="2" '
    'fill="currentColor"/><path d="M6.5 12h4.5"/>',
    "switch": '<rect x="2.5" y="7.5" width="19" height="9" rx="4.5"/><circle cx="16.5" cy="12" r="2" '
    'fill="currentColor"/>',
    # ---------- energia ----------
    "lock": '<rect x="4.5" y="10.5" width="15" height="10.5" rx="2.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>'
    '<path d="M12 14.5v2.5"/>',
    "hibernation": '<path d="M19 15A7.8 7.8 0 0 1 9.1 5.1 7.9 7.9 0 1 0 19 15Z"/><path d="M14.5 3h4.5l-4.5 5h4.5"/>',
    "rotate-cw": '<path d="M20.5 12a8.5 8.5 0 1 1-2.5-6"/><path d="M20.5 3.5V8H16"/>',
    "refresh-badge": '<path d="M20 12a8 8 0 1 1-2.3-5.6"/><path d="M20 3.5v3.7h-3.7"/>'
    '<path d="M12 8v6.5M9.3 11.8 12 14.5l2.7-2.7"/>',
    "power-badge": '<path d="M12 2.5v5"/><path d="M6.4 5.6a8 8 0 1 0 11.2 0"/><path d="M12 11v6M9.5 14.5 12 17l2.5-2.5"/>',
    "log-out": '<path d="M9 21H5.5A1.5 1.5 0 0 1 4 19.5v-15A1.5 1.5 0 0 1 5.5 3H9"/>'
    '<path d="m15.5 16.5 4.5-4.5-4.5-4.5M20 12H9"/>',
    # ---------- Ventude Shell (2.0) ----------
    "ethernet": '<rect x="2.5" y="3.5" width="19" height="13" rx="2"/><path d="M8.5 20.5h7M12 16.5v4"/>'
    '<path d="M9 8.5h6v4H9z"/>',
    "wifi-off": '<path d="M2.5 9a14 14 0 0 1 6.5-3.6M14.5 5.3A14 14 0 0 1 21.5 9M5.5 12.5a9.5 9.5 0 0 1 4.3-2.3'
    'M8.5 16a5 5 0 0 1 7 0M3.5 3.5l17 17"/><path d="M12 19.5h.01" stroke-width="2.6"/>',
    "task-view": '<rect x="3" y="7" width="13" height="12" rx="2"/><path d="M8 4h11a2 2 0 0 1 2 2v9.5"/>',
    "grid": '<rect x="3" y="3" width="5" height="5" rx="1.2"/><rect x="9.5" y="3" width="5" height="5" rx="1.2"/>'
    '<rect x="16" y="3" width="5" height="5" rx="1.2"/><rect x="3" y="9.5" width="5" height="5" rx="1.2"/>'
    '<rect x="9.5" y="9.5" width="5" height="5" rx="1.2"/><rect x="16" y="9.5" width="5" height="5" rx="1.2"/>'
    '<rect x="3" y="16" width="5" height="5" rx="1.2"/><rect x="9.5" y="16" width="5" height="5" rx="1.2"/>'
    '<rect x="16" y="16" width="5" height="5" rx="1.2"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/>'
    '<path d="m21 15-5-5L5 21"/>',
    "document": '<path d="M14 2.5H6.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V8Z"/>'
    '<path d="M14 2.5V8h5.5M8.5 13h7M8.5 16.5h5"/>',
    "sliders": '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    "night": '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z"/><path d="M17 3v3M15.5 4.5h3"/>',
    "focus": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.5"/>',
    "back": '<path d="M19 12H5M11 18l-6-6 6-6"/>',
    "x-circle": '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
    "shield": '<path d="M12 21.5s7.5-3.5 7.5-9.5V5.5L12 2.5l-7.5 3V12c0 6 7.5 9.5 7.5 9.5Z"/>',
    "admin": '<path d="M12 21.5s7.5-3.5 7.5-9.5V5.5L12 2.5l-7.5 3V12c0 6 7.5 9.5 7.5 9.5Z"/><path d="m9 12 2 2 4-4"/>',
    "folder-open": '<path d="M3 19.5V6a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2V10"/>'
    '<path d="M3 19.5 6 11h16l-3 8.5Z"/>',
    # ---------- Ventude 3: variantes da aba ativa (duas tintas: corpo translúcido + traço) ----------
    "home-fill": '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1Z" '
    'fill="currentColor" fill-opacity=".22"/>',
    "gauge-fill": '<path d="M3.3 17A9.9 9.9 0 0 1 2 12a10 10 0 0 1 20 0 9.9 9.9 0 0 1-1.3 5Z" '
    'fill="currentColor" fill-opacity=".18"/><path d="M12 14l4-5"/><circle cx="12" cy="14" r="1.8" '
    'fill="currentColor"/>',
    "cpu-fill": '<rect x="5" y="5" width="14" height="14" rx="2.5" fill="currentColor" fill-opacity=".2"/>'
    '<rect x="9" y="9" width="6" height="6" rx="1" fill="currentColor"/>'
    '<path d="M9.5 2v3M14.5 2v3M9.5 19v3M14.5 19v3M2 9.5h3M2 14.5h3M19 9.5h3M19 14.5h3"/>',
    "inbox-fill": '<path d="M3 13.5 5.5 5A2 2 0 0 1 7.4 3.5h9.2A2 2 0 0 1 18.5 5L21 13.5V19a2 2 0 0 1-2 2H5a2 '
    '2 0 0 1-2-2Z" fill="currentColor" fill-opacity=".2"/><path d="M3 13.5h5l1.5 2.5h5l1.5-2.5h5"/>',
    "calendar-fill": '<rect x="3" y="4.5" width="18" height="16.5" rx="3" fill="currentColor" '
    'fill-opacity=".2"/><path d="M8 2.5v4M16 2.5v4M3 10h18"/><path d="M7.5 14h.01M12 14h.01M16.5 14h.01'
    'M7.5 17.5h.01M12 17.5h.01" stroke-width="2.4"/>',
    "sun-fill": '<circle cx="12" cy="12" r="4.4" fill="currentColor"/><path d="M12 2v2.2M12 19.8V22'
    'M4.9 4.9l1.6 1.6M17.5 17.5l1.6 1.6M2 12h2.2M19.8 12H22M4.9 19.1l1.6-1.6M17.5 6.5l1.6-1.6"/>',
    "chart-fill": '<path d="M3 3v18h18"/><path d="m7 15 4-5 3.5 3L20 6v15H7Z" fill="currentColor" '
    'fill-opacity=".2" stroke="none"/><path d="m7 15 4-5 3.5 3L20 6"/>',
    "settings-fill": '<path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 '
    "1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 "
    "0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 "
    "0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 "
    "2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5"
    " 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 "
    '1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" fill="currentColor" fill-opacity=".2"/>'
    '<circle cx="12" cy="12" r="3" fill="currentColor"/>',
    "sliders-fill": '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2.2" '
    'fill="currentColor"/><circle cx="10" cy="17" r="2.2" fill="currentColor"/>',
}


def _render_scale() -> int:
    """Densidade em que os ícones são desenhados: a maior tela (no mínimo 2x, para ficar nítido)."""
    app = QGuiApplication.instance()
    ratio = 1.0
    if app is not None:
        ratio = max((s.devicePixelRatio() for s in QGuiApplication.screens()), default=1.0)
    return max(2, min(4, math.ceil(ratio)))


def _render(svg: str, size: int) -> QPixmap:
    scale = _render_scale()
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()
    pm.setDevicePixelRatio(scale)
    return pm


@lru_cache(maxsize=768)
def pixmap(name: str, color: str | None = None, size: int = 18, stroke: float = 1.8) -> QPixmap:
    color = color or theme.TEXT_2
    body = PATHS.get(name, PATHS["dot"]).replace("currentColor", color)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</svg>'
    )
    return _render(svg, size)


def icon(name: str, color: str | None = None, size: int = 18) -> QIcon:
    return QIcon(pixmap(name, color, size))


# ---------- clima em cores (sol dourado, nuvem clara, chuva azul, raio âmbar) ----------
_CLOUD = "#D5DDEB"
_CLOUD_FILL = "#283042"
_MOON = "#DCE3F7"
_CLOUD_BIG = "M7 19a5 5 0 0 1-.6-9.96A6.5 6.5 0 0 1 19 10.5a4.3 4.3 0 0 1-.5 8.5Z"
_CLOUD_HIGH = "M7 15.5a4.5 4.5 0 0 1-.6-8.96A6.5 6.5 0 0 1 19 8a4 4 0 0 1-.5 7.5Z"
_CLOUD_LOW = "M8.5 20a4.5 4.5 0 0 1-.5-8.97 5.8 5.8 0 0 1 11.2 1.47A3.8 3.8 0 0 1 18.8 20Z"
_SUN_RAYS = (
    "M12 2v2.2M12 19.8V22M4.9 4.9l1.6 1.6M17.5 17.5l1.6 1.6M2 12h2.2M19.8 12H22M4.9 19.1l1.6-1.6"
    "M17.5 6.5l1.6-1.6"
)


def _cloud(d: str) -> tuple[str, str]:
    return (f'<path d="{d}" fill="{_CLOUD_FILL}"/>', _CLOUD)


WEATHER: dict[str, list[tuple[str, str]]] = {
    "sun": [
        (f'<circle cx="12" cy="12" r="4.4" fill="SUN" fill-opacity=".92"/><path d="{_SUN_RAYS}"/>', "SUN")
    ],
    "moon": [
        (
            '<path d="M20.5 14.2A8.5 8.5 0 0 1 9.8 3.5 8.6 8.6 0 1 0 20.5 14.2Z" fill="MOON" '
            'fill-opacity=".28"/>',
            "MOON",
        )
    ],
    "cloud": [_cloud(_CLOUD_BIG)],
    "cloud-sun": [
        (
            '<circle cx="8.8" cy="8.8" r="3.7" fill="SUN" fill-opacity=".92"/>'
            '<path d="M8.8 2.3v1.4M3.8 4l1 1M2.2 8.8h1.4M13.8 4l-1 1"/>',
            "SUN",
        ),
        _cloud(_CLOUD_LOW),
    ],
    "cloud-moon": [
        (
            '<path d="M12.6 3.4a4.6 4.6 0 0 0 5.2 5.4 5 5 0 1 1-5.2-5.4Z" fill="MOON" fill-opacity=".3"/>',
            "MOON",
        ),
        _cloud(_CLOUD_LOW),
    ],
    "rain": [_cloud(_CLOUD_HIGH), ('<path d="m8.5 18-1 2.5M12.5 18l-1 2.5M16.5 18l-1 2.5"/>', "SKY")],
    "snow": [
        _cloud(_CLOUD_HIGH),
        ('<path d="M8 19h.01M12 18h.01M16 19h.01M10 21.5h.01M14 21.5h.01" stroke-width="2.8"/>', "SNOW"),
    ],
    "storm": [
        _cloud(_CLOUD_HIGH),
        ('<path d="m12.5 12-3 5h4l-2.5 4.5" fill="none"/>', "BOLT"),
    ],
    "fog": [('<path d="M4 9h16M3 13h18M5 17h14"/>', "CLOUD")],
}


def _tone(key: str) -> str:
    return {
        "SUN": theme.SUN,
        "SKY": theme.SKY,
        "MOON": _MOON,
        "CLOUD": _CLOUD,
        "SNOW": "#E6F1FF",
        "BOLT": theme.WARN,
    }.get(key, key)


@lru_cache(maxsize=128)
def weather(name: str, size: int = 18, stroke: float = 1.8) -> QPixmap:
    """Ícone do clima em cores; nomes desconhecidos caem no ícone de traço comum."""
    layers = WEATHER.get(name)
    if layers is None:
        return pixmap(name, theme.SKY, size, stroke)
    groups = []
    for body, tone in layers:
        c = _tone(tone)
        for key in ("SUN", "MOON", "SKY"):
            body = body.replace(f'"{key}"', f'"{_tone(key)}"')
        groups.append(f'<g stroke="{c}">{body}</g>')
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{"".join(groups)}</svg>'
    )
    return _render(svg, size)


# ---------- monograma: o "ícone" de um app enquanto o ícone real carrega (ou quando não existe) ----------
_MONO_COLORS = (
    ("#5EE6C1", "#2F9F8C"),
    ("#42B8F6", "#2A6FD1"),
    ("#B79EFF", "#7258D8"),
    ("#F592BC", "#C4527F"),
    ("#F7C76A", "#D08A2E"),
    ("#E8916D", "#B5583A"),
    ("#7CA8FF", "#4B63D6"),
)


def monogram_letter(target: str) -> str:
    """Letra que representa o app: o nome do .exe/.lnk, ou o nome do pacote da Loja."""
    text = (target or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if "!" in text or "_" in text:  # Pacote.Nome_hash!App
        text = text.split("_", 1)[0].split("!", 1)[0].rsplit(".", 1)[-1]
    else:
        text = text.rsplit(".", 1)[0] if "." in text else text
    letter = next((ch for ch in text if ch.isalnum()), "")
    return letter.upper() or "•"


@lru_cache(maxsize=256)
def monogram(target: str, size: int = 24) -> QPixmap:
    """Quadrado arredondado com o gradiente de uma cor estável (pelo nome) e a inicial do app."""
    from PySide6.QtGui import QColor, QFont, QLinearGradient

    letter = monogram_letter(target)
    top, bottom = _MONO_COLORS[sum(map(ord, target or "")) % len(_MONO_COLORS)]
    scale = _render_scale()
    side = size * scale
    pm = QPixmap(side, side)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    grad = QLinearGradient(0, 0, side, side)
    grad.setColorAt(0.0, QColor(top))
    grad.setColorAt(1.0, QColor(bottom))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(QRectF(side * 0.04, side * 0.04, side * 0.92, side * 0.92), side * 0.26, side * 0.26)
    font = QFont(theme.ui_family())
    font.setPixelSize(round(side * 0.5))
    font.setWeight(QFont.Weight.Bold)
    p.setFont(font)
    p.setPen(QColor(theme.ACCENT_TEXT if top in ("#5EE6C1", "#F7C76A") else "#FFFFFF"))
    p.drawText(QRectF(0, 0, side, side), Qt.AlignmentFlag.AlignCenter, letter)
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm
