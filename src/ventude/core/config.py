from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from typing import Any

# Módulos opcionais (abas). "home" e "settings" estão sempre ligados.
MODULES: list[tuple[str, str, str]] = [
    ("home", "Início", "home"),
    ("limits", "IAs", "gauge"),
    ("hardware", "Sistema", "cpu"),
    ("audio", "Áudio", "volume"),
    ("shortcuts", "Atalhos", "apps"),
    ("shelf", "Estante", "inbox"),
    ("agenda", "Agenda", "calendar"),
    ("tasks", "Tarefas", "check-square"),
    ("weather", "Clima", "sun"),
    ("market", "Mercado", "chart"),
    ("news", "Notícias", "news"),
    ("settings", "Ajustes", "settings"),
]
ALWAYS_ON = {"home", "settings"}
DEFAULT_MODULES = "limits,hardware,audio,shortcuts,shelf,agenda,tasks,weather,market,news"
LAYOUT_VERSION = 9  # 5: Sistema/Áudio · 6: Shell 2.0 · 7: Shell 3.0 · 8: dedupe · 9: some em tela cheia
DEFAULT_SYMBOLS = "^BVSP:Ibovespa,BRL=X:Dólar,BTC-USD:Bitcoin,PETR4.SA:Petrobras,VALE3.SA:Vale,AAPL:Apple"
DEFAULT_NEWS = "G1,BBC Brasil,Tecnoblog"
ANIMATION_SPEEDS = {"nenhuma": 0, "rapida": 170, "normal": 260, "suave": 380}
DOCK_MODES = ("esquivar", "sempre", "oculta")
LOCK_PRESETS_MIN = (1, 2, 3, 5, 10, 15, 30, 60)  # opções prontas do "Bloquear depois de"
LOCK_MAX_MIN = 24 * 60
METRIC_KEYS = ("cpu", "gpu", "gpu_temp", "ram", "net_down", "net_up", "ping", "battery")


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
    hide_fullscreen: bool = True  # some em jogos/vídeos em tela cheia (janela maximizada não conta)
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
    zone_open: bool = False  # passar o mouse nos anéis/clima abre direto nessas abas (clique sempre abre)
    pill_progress: bool = True  # linha de andamento da música na ilha fechada
    island_toasts: bool = True  # avisos na própria ilha (senão, na bandeja)
    toast_music: bool = True  # mostra a música nova quando a faixa troca com a ilha fechada
    agenda_reminders: bool = True  # lembra 10 e 5 min antes dos compromissos com horário
    hotkey_command: bool = True  # o atalho global abre a barra de comandos
    pinned_metrics_json: str = ""  # ["cpu", "ram", ...]: métricas ao vivo na pílula fechada
    hardware_alerts: bool = True  # avisa de GPU/CPU acima de 85 °C e RAM acima de 92%
    pill_privacy: bool = True  # pontinhos de microfone (laranja) e câmera (verde) em uso
    clipboard_history: bool = True  # o que você copia (Ctrl + C) vai para a Estante
    lyrics_enabled: bool = True  # letra sincronizada (lrclib.net) no player do Início
    replace_taskbar: bool = False  # esconde a barra do Windows e mostra a barra de tarefas do Ventude
    replace_start_menu: bool = False  # tecla Windows e Win + S abrem a busca do Ventude
    # ---------- Ventude Shell (2.0 e 3.0) ----------
    dock_mode: str = "esquivar"  # esquivar | sempre | oculta
    island_echo: bool = True  # hora nos outros monitores (Eco)
    hotkey_controls: bool = True  # Win + A abre Controles na ilha
    hotkey_dock_numbers: bool = True  # Win + 1..9 e Win + Shift + 1..9 acionam a Doca
    quiet_mode: bool = False  # Silêncio: só deixa passar timer, agenda e limite >= 95%
    agent_command: str = "claude"  # comando executado por "Agente aqui"
    taskbar_autohide: bool = False  # legado (usado na migração v6->v7)
    taskbar_align: str = "center"  # legado
    taskbar_weather: bool = True  # legado
    taskbar_all_monitors: bool = True  # uma Doca em cada monitor
    shell_glass: bool = True  # legado
    power_animation: bool = True  # "Até logo!": escurece as telas antes de desligar, reiniciar...
    shell_greeting: bool = True  # "Bom dia!" ao ligar o PC
    start_view: str = "category"  # legado
    pill_date: bool = True  # data na ilha fechada (única data no Shell 3.0)
    lock_after_min: int = 5  # tela de bloqueio do Ventude depois de N min parado (0 = nunca)
    lock_require_password: bool = True  # mexer na tela de bloqueio bloqueia o Windows (pede a senha)
    win_triple_hide: bool = True  # tecla Windows 3 vezes seguidas esconde/mostra a ilha
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

    def pinned_metrics(self) -> list[str]:
        """Métricas fixadas na pílula, na ordem em que foram fixadas."""
        try:
            data = json.loads(self.pinned_metrics_json) if self.pinned_metrics_json else []
        except ValueError:
            return []
        if not isinstance(data, list):
            return []
        return list(dict.fromkeys(str(m) for m in data if m in METRIC_KEYS))

    def with_metric(self, metric: str, on: bool) -> str:
        """JSON novo de pinned_metrics_json com a métrica fixada (ou solta)."""
        metrics = [m for m in self.pinned_metrics() if m != metric]
        if on and metric in METRIC_KEYS:
            metrics.append(metric)
        return json.dumps(metrics) if metrics else ""

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
        s.lock_after_min = max(0, min(s.lock_after_min, LOCK_MAX_MIN))
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
        if s.dock_mode not in DOCK_MODES:
            s.dock_mode = "esquivar"
        if not s.agent_command.strip():
            s.agent_command = "claude"
        if s.taskbar_align not in ("center", "left"):
            s.taskbar_align = "center"
        if s.start_view not in ("category", "grid", "list"):
            s.start_view = "category"
        if "layout_version" not in data or int(data.get("layout_version") or 0) < 3:
            # Versões antigas tinham outro tamanho de ilha e uma IA própria.
            s.layout_version = 3
            s.modules = DEFAULT_MODULES
        return s
