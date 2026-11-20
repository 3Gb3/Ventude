from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from typing import Any

# Módulos opcionais (abas). "home" e "settings" estão sempre ligados.
MODULES: list[tuple[str, str, str]] = [
    ("home", "Início", "home"),
    ("limits", "IAs", "gauge"),
    ("shortcuts", "Atalhos", "apps"),
    ("agenda", "Agenda", "calendar"),
    ("tasks", "Tarefas", "check-square"),
    ("weather", "Clima", "sun"),
    ("market", "Mercado", "chart"),
    ("news", "Notícias", "news"),
    ("settings", "Ajustes", "settings"),
]
ALWAYS_ON = {"home", "settings"}
DEFAULT_MODULES = "limits,shortcuts,agenda,tasks,weather,market,news"
DEFAULT_SYMBOLS = "^BVSP:Ibovespa,BRL=X:Dólar,BTC-USD:Bitcoin,PETR4.SA:Petrobras,VALE3.SA:Vale,AAPL:Apple"
DEFAULT_NEWS = "G1,BBC Brasil,Tecnoblog"
ANIMATION_SPEEDS = {"rapida": 170, "normal": 260, "suave": 380}


@dataclass
class Settings:
    monitor_index: int = 0
    collapse_delay_ms: int = 500
    animation: str = "normal"
    clock_24h: bool = True
    island_position_json: str = ""
    weather_city_json: str = ""
    start_with_windows: bool = False
    hotkey_enabled: bool = True
    hover_open: bool = True
    hide_fullscreen: bool = True
    modules: str = DEFAULT_MODULES
    pill_rings: bool = True
    limits_notify: bool = True
    limits_interval_min: int = 5
    claude_mode: str = "auto"  # auto | code | web | off
    claude_refresh_login: bool = True  # renova o token vencido do Claude Code
    antigravity_mode: str = "auto"  # auto | off
    grok_mode: str = "auto"  # auto | cli | web | off
    codex_mode: str = "off"  # auto | off
    cursor_mode: str = "off"  # auto | off
    copilot_mode: str = "off"  # auto | off
    ring_providers: str = "claude,antigravity,grok"  # IAs com anel na ilha e no Início
    pill_weather: bool = True
    weather_auto_locate: bool = True  # sem cidade escolhida, estima pelo IP
    pinned_limits_json: str = ""  # {"provedor": "chave da janela"} mostrada no anel
    media_source: str = "spotify"  # spotify | any
    hover_delay_ms: int = 0
    zone_open: bool = True  # passar o mouse nos anéis/clima abre direto nessas abas
    pill_progress: bool = True  # linha de andamento da música na ilha fechada
    market_symbols: str = DEFAULT_SYMBOLS
    news_sources: str = DEFAULT_NEWS
    layout_version: int = 3

    @property
    def animation_ms(self) -> int:
        return ANIMATION_SPEEDS.get(self.animation, 260)

    def module_enabled(self, module: str) -> bool:
        return module in ALWAYS_ON or module in self.enabled_modules()

    def enabled_modules(self) -> set[str]:
        return {m.strip() for m in self.modules.split(",") if m.strip()}

    def provider_mode(self, provider: str) -> str:
        return str(getattr(self, f"{provider}_mode", "off"))

    def ring_enabled(self, provider: str) -> bool:
        """A IA tem anel na ilha e no Início (e está ligada)."""
        rings = {p.strip() for p in self.ring_providers.split(",") if p.strip()}
        return provider in rings and self.provider_mode(provider) != "off"

    def with_ring(self, provider: str, on: bool) -> str:
        rings = [p.strip() for p in self.ring_providers.split(",") if p.strip() and p.strip() != provider]
        if on:
            rings.append(provider)
        return ",".join(rings)

    def pinned_limits(self) -> dict[str, str]:
        try:
            data = json.loads(self.pinned_limits_json) if self.pinned_limits_json else {}
        except ValueError:
            return {}
        return {str(k): str(v) for k, v in data.items() if v} if isinstance(data, dict) else {}

    def pinned_limit(self, provider: str) -> str:
        return self.pinned_limits().get(provider, "")

    def with_pinned(self, provider: str, key: str) -> str:
        """JSON novo de pinned_limits_json com a janela fixada (ou solta, se key vazio)."""
        pins = self.pinned_limits()
        if key:
            pins[provider] = key
        else:
            pins.pop(provider, None)
        return json.dumps(pins, sort_keys=True) if pins else ""

    def copy(self, **changes: Any) -> Settings:
        data = self.to_dict()
        data.update(changes)
        return Settings.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        s = cls()
        for f in fields(cls):
            if f.name not in data:
                continue
            default = getattr(s, f.name)
            try:
                value = data[f.name]
                if isinstance(default, bool):
                    value = value if isinstance(value, bool) else str(value).lower() in ("1", "true")
                setattr(s, f.name, type(default)(value))
            except (TypeError, ValueError):
                pass
        s.monitor_index = max(0, s.monitor_index)
        s.collapse_delay_ms = max(0, min(s.collapse_delay_ms, 10_000))
        s.limits_interval_min = max(2, min(s.limits_interval_min, 60))
        s.hover_delay_ms = max(0, min(s.hover_delay_ms, 2000))
        if s.media_source not in ("spotify", "any"):
            s.media_source = "spotify"
        if s.animation not in ANIMATION_SPEEDS:
            s.animation = "normal"
        if s.claude_mode not in ("auto", "code", "web", "off"):
            s.claude_mode = "auto"
        if s.antigravity_mode not in ("auto", "off"):
            s.antigravity_mode = "auto"
        if s.grok_mode not in ("auto", "cli", "web", "off"):
            s.grok_mode = "auto"
        for name in ("codex_mode", "cursor_mode", "copilot_mode"):
            if getattr(s, name) not in ("auto", "off"):
                setattr(s, name, "off")
        if "layout_version" not in data or int(data.get("layout_version") or 0) < 3:
            # Versões antigas tinham outro tamanho de ilha e uma IA própria.
            s.layout_version = 3
            s.modules = DEFAULT_MODULES
        return s
