"""Modelo lógico dos itens da Doca: agrupamento por app, fixados e filtro por monitor."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from ventude.core.store import Pin
from ventude.services import apps
from ventude.services.taskbar import Rect, WindowInfo

AI_APPS: dict[str, tuple[str, ...]] = {
    "claude": ("claude",),
    "chatgpt": ("chatgpt", "openai"),
    "gemini": ("gemini", "bard"),
    "copilot": ("copilot",),
    "codex": ("codex",),
    "cursor": ("cursor",),
}


def norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)) if path else ""


@dataclass
class TaskItem:
    key: str
    label: str
    kind: str  # app | uwp | folder | file | url | system
    target: str  # o que abrir quando não há janela (ou para abrir outra)
    icon_kind: str
    icon_target: str
    pin_id: int = 0
    exe: str = ""
    app_id: str = ""
    windows: list[WindowInfo] = field(default_factory=list)
    other_monitor: bool = False

    @property
    def pinned(self) -> bool:
        return self.pin_id > 0

    @property
    def active(self) -> bool:
        return any(w.foreground for w in self.windows) and not self.other_monitor

    @property
    def launchable(self) -> bool:
        return bool(self.target)


def pin_identity(pin: Pin) -> tuple[str, str]:
    """(.exe normalizado, AppID) do que o fixado abre, para casar com as janelas abertas."""
    if pin.kind == "uwp":
        return "", pin.target
    if pin.kind == "app":
        exe = pin.target
        if exe.lower().endswith(".lnk"):
            from ventude.services.winutil import lnk_target

            exe = lnk_target(exe) or ""
        return (norm(exe) if exe.lower().endswith(".exe") else ""), ""
    return "", ""


HELPER_WORDS = ("helper", "host", "broker", "runtime", "renderer", "launcher")


def friendly_label(exe_label: str, title: str) -> str:
    """Nome do app na barra: 'Steam Client WebHelper' vira o título da janela ('Steam')."""
    if not exe_label:
        return title
    if title and any(word in exe_label.lower() for word in HELPER_WORDS):
        return title.split(" - ")[-1].strip()[:40] or exe_label
    return exe_label


def store_families(entries: list[object]) -> dict[str, str]:
    """Família do pacote (em minúsculas) -> AppID, dos apps da Loja do índice."""
    out: dict[str, str] = {}
    for entry in entries:
        if getattr(entry, "kind", "") == "uwp" and "!" in entry.path:  # type: ignore[attr-defined]
            out.setdefault(entry.path.split("!", 1)[0].lower(), entry.path)  # type: ignore[attr-defined]
    return out


def provider_for_app(label: str, target: str) -> str:
    """Detecta se o app pertence a algum provedor de IA conhecido."""
    stem = Path(target).stem.lower() if target else ""
    haystack = f"{label.lower()} {stem}"
    for provider, tokens in AI_APPS.items():
        if any(tok in haystack for tok in tokens):
            return provider
    return ""


def build_items(
    pins: list[Pin], windows: list[WindowInfo], families: dict[str, str] | None = None
) -> list[TaskItem]:
    """Fixados primeiro (na ordem), depois os apps abertos, juntando as janelas do mesmo app."""
    from ventude.services.winutil import app_name

    families = families or {}
    items: list[TaskItem] = []
    by_exe: dict[str, TaskItem] = {}
    by_app: dict[str, TaskItem] = {}
    by_family: dict[str, TaskItem] = {}
    for pin in pins:
        exe, app_id = pin_identity(pin)
        item = TaskItem(
            key=f"pin:{pin.id}",
            label=pin.label,
            kind=pin.kind,
            target=pin.target,
            icon_kind=pin.kind,
            icon_target=pin.target,
            pin_id=pin.id,
            exe=exe,
            app_id=app_id,
        )
        items.append(item)
        if exe:
            by_exe.setdefault(exe, item)
        if app_id:
            by_app.setdefault(app_id.lower(), item)
            by_family.setdefault(app_id.split("!", 1)[0].lower(), item)
    for window in windows:
        exe = norm(window.exe)
        family = apps.package_family(window.exe).lower()
        app_id = window.app_id.lower()
        item = (
            (by_app.get(app_id) if app_id else None)
            or (by_family.get(family) if family else None)
            or (by_exe.get(exe) if exe else None)
        )
        if item is None:
            store_id = families.get(family, "") if family else ""
            if store_id:
                kind, target = "uwp", store_id
            else:
                kind, target = "app", window.exe
            key = f"app:{app_id}" if app_id else (f"exe:{exe}" if exe else f"hwnd:{window.hwnd}")
            item = TaskItem(
                key=key,
                label=friendly_label(app_name(window.exe), window.title),
                kind=kind,
                target=target,
                icon_kind=kind,
                icon_target=target,
                exe=exe,
                app_id=window.app_id,
            )
            items.append(item)
            if exe:
                by_exe.setdefault(exe, item)
            if app_id:
                by_app[app_id] = item
            if family:
                by_family.setdefault(family, item)
        item.windows.append(window)
    return items


def split_by_monitor(items: list[TaskItem], monitor: Rect) -> list[TaskItem]:
    """Filtra os itens para um monitor: fixados sempre aparecem; abertos só onde têm janela."""
    if monitor == (0, 0, 0, 0):
        return list(items)
    out: list[TaskItem] = []
    for item in items:
        if not item.windows:
            if item.pinned:
                out.append(replace(item, other_monitor=False))
            continue
        on_here = [w for w in item.windows if w.monitor in (monitor, (0, 0, 0, 0))]
        if on_here:
            out.append(replace(item, windows=on_here, other_monitor=False))
        elif item.pinned:
            out.append(replace(item, windows=list(item.windows), other_monitor=True))
    return out
