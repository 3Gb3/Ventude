from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# state: ok | stale (mostrando último valor) | setup (falta configurar) | error | off
PROVIDERS: dict[str, str] = {
    "claude": "Claude",
    "antigravity": "Gemini",
    "grok": "Grok",
    "codex": "Codex",
    "cursor": "Cursor",
    "copilot": "Copilot",
}


@dataclass
class Window:
    key: str
    label: str
    used: float | None  # 0..100 (% usado)
    resets_at: float | None = None  # epoch (s)
    detail: str = ""
    group: str = ""  # Antigravity: família do modelo (Gemini, Claude, GPT, Outros)
    period: float | None = None  # duração da janela em segundos (5h, 7 dias...), se conhecida

    def used_at(self, now: float | None = None) -> float | None:
        """Uso considerando renovações que já passaram desde a última leitura."""
        now = time.time() if now is None else now
        if self.resets_at and self.resets_at <= now:
            return 0.0
        if self.used is None:
            return None
        return max(0.0, min(100.0, self.used))

    def exhausts_in(
        self, now: float | None = None, history: list[tuple[float, float]] | None = None
    ) -> float | None:
        """Segundos até chegar a 100% no ritmo atual, se isso acontecer antes de renovar."""
        now = time.time() if now is None else now
        used = self.used_at(now)
        if used is None or used < 5 or used >= 100:
            return None
        left = (self.resets_at - now) if self.resets_at else None
        if left is not None and left <= 0:
            return None
        rate = None  # % por segundo
        if self.period and left is not None:
            elapsed = self.period - left
            if elapsed >= max(600.0, self.period * 0.05):
                rate = used / elapsed
        if rate is None and history:
            recent = [(t, u) for t, u in history if now - t <= 3600]
            if len(recent) >= 2 and recent[-1][0] - recent[0][0] >= 600 and recent[-1][1] > recent[0][1]:
                rate = (recent[-1][1] - recent[0][1]) / (recent[-1][0] - recent[0][0])
        if not rate or rate <= 0:
            return None
        seconds = (100 - used) / rate
        if left is not None and seconds >= left:
            return None
        return seconds if left is not None or seconds < 6 * 3600 else None


@dataclass
class Report:
    provider: str
    state: str
    windows: list[Window] = field(default_factory=list)
    plan: str = ""
    account: str = ""
    source: str = ""
    message: str = ""
    fetched_at: float = 0.0

    @property
    def name(self) -> str:
        return PROVIDERS.get(self.provider, self.provider)

    def headline(self, now: float | None = None, pinned: str = "") -> Window | None:
        """A janela que vai no anel: a fixada pelo usuário ou a mais apertada.

        No Antigravity o anel do "Gemini" segue só os modelos Gemini.
        """
        known = [w for w in self.windows if w.used_at(now) is not None]
        if pinned:
            chosen = next((w for w in known if w.key == pinned), None)
            if chosen:
                return chosen
        if not known:
            return None
        if self.provider == "antigravity":
            known = [w for w in known if is_gemini(w)] or known
        return max(known, key=lambda w: (w.used_at(now) or 0.0, -(w.resets_at or 0)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Report:
        names = set(Window.__dataclass_fields__)
        windows = [Window(**{k: v for k, v in w.items() if k in names}) for w in data.get("windows") or []]
        values = {k: v for k, v in data.items() if k != "windows" and k in cls.__dataclass_fields__}
        return cls(windows=windows, **values)


@dataclass
class FetchContext:
    """O que um provedor precisa para consultar, sem tocar na interface."""

    mode: str
    home: Path
    secret: Callable[[str], str]
    previous: Report | None = None
    options: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """Falha esperada e explicável (mensagem já em português)."""

    def __init__(self, message: str, *, transient: bool = True) -> None:
        super().__init__(message)
        self.transient = transient


def is_gemini(window: Window) -> bool:
    return window.group == "Gemini" or (not window.group and "gemini" in window.label.lower())


def find_key(node: Any, *names: str) -> Any:
    """Busca em profundidade a primeira chave com um dos nomes dados."""
    stack = [node]
    while stack:
        current = stack.pop(0)
        if isinstance(current, dict):
            for name in names:
                if name in current and current[name] not in (None, ""):
                    return current[name]
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return None


def as_float(value: Any) -> float | None:
    if isinstance(value, dict):  # {"val": "123"} usado por algumas APIs
        value = value.get("val", value.get("value"))
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
