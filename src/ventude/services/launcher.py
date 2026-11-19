"""Abrir apps, pastas, arquivos e sites fixados."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

APP_SUFFIXES = {".exe", ".lnk", ".url", ".bat", ".cmd", ".appref-ms", ".msc"}


def normalize_url(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or "." not in (parts.hostname or ""):
        return ""
    return raw


def kind_for(path: str) -> str:
    p = Path(path)
    if p.is_dir():
        return "folder"
    return "app" if p.suffix.lower() in APP_SUFFIXES else "file"


def default_label(target: str, kind: str) -> str:
    if kind == "url":
        host = urlsplit(target).hostname or target
        return host.removeprefix("www.").split(".")[0].capitalize()
    p = Path(target)
    return (p.stem if p.suffix else p.name) or str(p)


def is_valid(kind: str, target: str) -> bool:
    if kind == "url":
        return bool(normalize_url(target))
    return Path(target).exists()


def open_target(kind: str, target: str) -> tuple[bool, str]:
    try:
        if kind == "url":
            webbrowser.open(target)
            return True, ""
        if not Path(target).exists():
            return False, "O caminho não existe mais."
        if sys.platform == "win32":
            os.startfile(target)  # type: ignore[attr-defined]
        else:  # pragma: no cover
            subprocess.Popen(["xdg-open", target])
    except OSError as exc:
        log.exception("Falha ao abrir %s", target)
        return False, str(exc)
    return True, ""


def open_url(url: str) -> None:
    webbrowser.open(url)
