"""Modelo lógico dos itens da Doca: agrupamento por app, fixados e filtro por monitor."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from ventude.core.store import Pin
from ventude.services import apps
from ventude.services.taskbar import Rect, WindowInfo

GENERIC_WRAPPERS = frozenset({"update.exe", "launcher.exe", "bootstrap.exe"})
EXPLORER_APP_IDS = frozenset({"microsoft.windows.explorer"})

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


@dataclass(frozen=True)
class _PinSignature:
    kind: str
    exact: str
    resolved_exe: str
    app_id: str
    family: str
    is_explorer: bool
    norm_label: str
    is_aliasable: bool


def _pin_signature(
    kind: str,
    label: str,
    target: str,
    families: dict[str, str] | None = None,
) -> _PinSignature:
    clean_kind = (kind or "app").strip().lower()
    raw_target = (target or "").strip()
    exact = norm(raw_target) if clean_kind in ("app", "folder", "file") else raw_target.lower()
    resolved_exe = ""
    is_wrapper = False
    family = ""
    app_id = raw_target.lower() if clean_kind == "uwp" else ""
    if clean_kind == "app" and raw_target:
        candidate = raw_target
        if raw_target.lower().endswith(".lnk"):
            from ventude.services.winutil import lnk_target

            candidate = lnk_target(raw_target) or ""
        if candidate.lower().endswith(".exe"):
            if os.path.basename(candidate).lower() in GENERIC_WRAPPERS:
                is_wrapper = True
            else:
                resolved_exe = norm(candidate)
        raw_fam = apps.package_family(resolved_exe or raw_target).lower()
        if raw_fam:
            family = raw_fam
            if families and raw_fam in families:
                app_id = families[raw_fam].lower()
    elif clean_kind == "uwp" and app_id and "!" in app_id:
        family = app_id.split("!", 1)[0].lower()
    exe_base = os.path.basename(resolved_exe or exact).lower()
    is_explorer = app_id in EXPLORER_APP_IDS or (clean_kind == "app" and exe_base == "explorer.exe")
    norm_label = (label or "").strip().lower()
    is_aliasable = clean_kind == "uwp" or raw_target.lower().endswith(".lnk") or is_wrapper
    return _PinSignature(
        kind=clean_kind,
        exact=exact,
        resolved_exe=resolved_exe,
        app_id=app_id,
        family=family,
        is_explorer=is_explorer,
        norm_label=norm_label,
        is_aliasable=is_aliasable,
    )


def _signatures_match(cand: _PinSignature, other: _PinSignature) -> bool:
    if cand.exact and cand.exact == other.exact:
        return True
    if cand.is_explorer and other.is_explorer:
        return True
    if cand.resolved_exe and cand.resolved_exe == other.resolved_exe:
        return True
    if (cand.app_id and cand.app_id == other.app_id) or (cand.family and cand.family == other.family):
        return True
    return bool(
        cand.norm_label
        and cand.norm_label == other.norm_label
        and cand.kind in ("app", "uwp")
        and other.kind in ("app", "uwp")
        and (cand.is_aliasable or other.is_aliasable)
    )


def find_matching_pin(
    pins: Sequence[Pin],
    kind: str,
    label: str,
    target: str,
    families: dict[str, str] | None = None,
) -> Pin | None:
    """Encontra fixado equivalente mesmo quando o alvo difere (.lnk vs .exe, UWP vs Explorer etc.)."""
    cand = _pin_signature(kind, label, target, families)
    for pin in pins:
        other = _pin_signature(pin.kind, pin.label, pin.target, families)
        if _signatures_match(cand, other):
            return pin
    return None


def dedupe_pins(store: object, place: str, families: dict[str, str] | None = None) -> int:
    """Remove fixados duplicados preservando o primeiro de cada aplicativo."""
    pins = list(store.pins(place))  # type: ignore[attr-defined]
    kept: list[Pin] = []
    removed = 0
    for pin in pins:
        if find_matching_pin(kept, pin.kind, pin.label, pin.target, families) is not None:
            store.remove_pin(pin.id)  # type: ignore[attr-defined]
            removed += 1
        else:
            kept.append(pin)
    return removed


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
    pins: list[Pin],
    windows: list[WindowInfo],
    families: dict[str, str] | None = None,
    order: Sequence[str] | None = None,
) -> list[TaskItem]:
    """Fixados primeiro (na ordem), depois os apps abertos em ordem estável."""
    from ventude.services.winutil import app_name

    families = families or {}
    pinned_items: list[TaskItem] = []
    unpinned_items: list[TaskItem] = []
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
        pinned_items.append(item)
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
            unpinned_items.append(item)
            if exe:
                by_exe.setdefault(exe, item)
            if app_id:
                by_app[app_id] = item
            if family:
                by_family.setdefault(family, item)
        item.windows.append(window)
    if order is not None:
        rank = {key: idx for idx, key in enumerate(order)}
        unpinned_items.sort(
            key=lambda it: (0, rank[it.key], "")
            if it.key in rank
            else (1, 0, f"{it.label.lower()}\0{it.key}")
        )
    return pinned_items + unpinned_items


def split_by_monitor(items: list[TaskItem], monitor: Rect) -> list[TaskItem]:
    """Itens da Doca de um monitor: todos aparecem em todas as telas.

    O que tem janela neste monitor fica normal; o que só tem janela em outro monitor aparece com o
    indicador vazado ("outra tela") e o clique leva até a janela de lá.
    """
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
        else:
            out.append(replace(item, windows=list(item.windows), other_monitor=True))
    return out
