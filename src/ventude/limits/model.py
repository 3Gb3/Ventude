from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# state: ok | stale (mostrando último valor) | setup (falta configurar) | error | off
PROVIDERS: dict[str, str] = {"claude": "Claude", "antigravity": "Gemini", "grok": "Grok"}


@dataclass
class Window:
    key: str
    label: str
    used: float | None  # 0..100 (% usado)
    resets_at: float | None = None  # epoch (s)
    detail: str = ""

    def used_at(self, now: float | None = None) -> float | None:
        """Uso considerando renovações que já passaram desde a última leitura."""
        now = time.time() if now is None else now
        if self.resets_at and self.resets_at <= now:
            return 0.0
        if self.used is None:
            return None
        return max(0.0, min(100.0, self.used))


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

    @property
    def has_data(self) -> bool:
        return bool(self.windows) and self.state in ("ok", "stale")

    def headline(self, now: float | None = None) -> Window | None:
        """A janela mais apertada: é ela que decide se dá para continuar usando."""
        known = [w for w in self.windows if w.used_at(now) is not None]
        if not known:
            return None
        if self.provider == "antigravity":  # o anel do "Gemini" segue os modelos Gemini
            known = [w for w in known if "gemini" in w.label.lower()] or known
        return max(known, key=lambda w: (w.used_at(now) or 0.0, -(w.resets_at or 0)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Report:
        windows = [Window(**w) for w in data.get("windows") or []]
        values = {k: v for k, v in data.items() if k != "windows" and k in cls.__dataclass_fields__}
        return cls(windows=windows, **values)


@dataclass
class FetchContext:
    """O que um provedor precisa para consultar, sem tocar na interface."""

    mode: str
    home: Path
    secret: Callable[[str], str]
    previous: Report | None = None


class ProviderError(Exception):
    """Falha esperada e explicável (mensagem já em português)."""

    def __init__(self, message: str, *, transient: bool = True) -> None:
        super().__init__(message)
        self.transient = transient


def severity(used: float | None) -> str:
    if used is None:
        return "unknown"
    if used >= 90:
        return "critical"
    if used >= 70:
        return "warning"
    return "normal"


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
