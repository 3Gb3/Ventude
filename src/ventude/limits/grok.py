"""Limites do Grok.

Fontes, em ordem:
1. Grok CLI (xAI): lê o login de ~/.grok/auth.json e consulta os créditos do ciclo.
2. grok.com: com o cookie colado em Ajustes, consulta as consultas restantes por modo.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ventude.core import net
from ventude.core.timefmt import parse_instant
from ventude.limits.model import FetchContext, ProviderError, Report, Window, as_float, find_key

CLI_BASE = "https://cli-chat-proxy.grok.com/v1"
WEB_LIMITS = "https://grok.com/rest/rate-limits"
WEB_MODELS = [
    ("grok-4-auto", "Auto"),
    ("grok-3", "Rápido"),
    ("grok-4", "Expert"),
    ("grok-4-heavy", "Heavy"),
]
SETUP_HINT = "Faça login no Grok CLI (comando grok) ou cole o cookie do grok.com em Ajustes."


def auth_path(home: Path) -> Path:
    base = os.environ.get("GROK_HOME")
    return (Path(base) if base else home / ".grok") / "auth.json"


def read_cli_token(home: Path) -> tuple[str, float | None, str] | None:
    """(token, expira_em, email) do login do Grok CLI."""
    if env := os.environ.get("GROK_OAUTH_TOKEN"):
        return env.strip(), None, ""
    try:
        data = json.loads(auth_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    entries: list[tuple[int, dict[str, Any]]] = []
    if isinstance(data.get("key"), str):
        entries.append((0, data))
    for scope, entry in data.items():
        if isinstance(entry, dict) and isinstance(entry.get("key"), str):
            rank = 0 if scope.startswith("https://auth.x.ai") else 1 if "accounts.x.ai" in scope else 2
            entries.append((rank, entry))
    if not entries:
        return None
    entry = min(entries, key=lambda e: e[0])[1]
    return entry["key"], parse_instant(entry.get("expires_at")), str(entry.get("email") or "")


def parse_billing(data: Any) -> Window | None:
    config = data.get("config", data) if isinstance(data, dict) else {}
    used = as_float(find_key(config, "creditUsagePercent"))
    if used is None:
        spent = as_float(find_key(config, "onDemandUsed"))
        cap = as_float(find_key(config, "onDemandCap"))
        if spent is not None and cap:
            used = spent / cap * 100
    if used is None:
        return None
    period = config.get("currentPeriod") if isinstance(config, dict) else None
    reset = parse_instant((period or {}).get("end") if isinstance(period, dict) else None) or parse_instant(
        find_key(config, "billingPeriodEnd")
    )
    return Window("credits", "Créditos · ciclo atual", round(used, 1), reset)


def _from_cli(token: str, expires: float | None, email: str) -> Report:
    if expires and expires < time.time():
        raise ProviderError("O login do Grok CLI expirou. Rode o comando grok para renovar.")
    headers = {
        "Authorization": f"Bearer {token}",
        "x-xai-token-auth": "xai-grok-cli",
    }
    try:
        billing = net.get_json(f"{CLI_BASE}/billing", params={"format": "credits"}, headers=headers)
    except net.NetError as exc:
        if exc.status in (401, 403):
            raise ProviderError("Login do Grok CLI recusado. Rode grok e entre de novo.") from None
        raise ProviderError(f"Sem conexão com a xAI ({exc}).") from None
    window = parse_billing(billing)
    if window is None:
        raise ProviderError("A xAI não informou créditos para esta conta.", transient=False)
    plan = ""
    try:
        settings = net.get_json(f"{CLI_BASE}/settings", headers=headers, timeout=6)
        plan = str(find_key(settings, "subscription_tier_display") or "")
    except net.NetError:
        pass
    return Report("grok", "ok", [window], plan=plan, account=email, source="Grok CLI")


def _hours(seconds: float) -> str:
    hours = seconds / 3600
    return f"{hours:.0f}h" if hours >= 1 else f"{seconds / 60:.0f} min"


def parse_rate(data: Any, key: str, label: str, now: float | None = None) -> Window | None:
    if not isinstance(data, dict):
        return None
    main = data
    if "remainingQueries" not in data:
        main = data.get("highEffortRateLimits") or data.get("lowEffortRateLimits") or {}
    remaining = as_float(main.get("remainingQueries"))
    total = None
    for name in ("totalQueries", "maxQueries", "queryLimit", "limit", "total"):
        total = as_float(main.get(name))
        if total:
            break
    if remaining is None or not total:
        return None
    now = time.time() if now is None else now
    wait = as_float(main.get("waitTimeSeconds") or data.get("waitTimeSeconds"))
    window = as_float(main.get("windowSizeSeconds") or data.get("windowSizeSeconds"))
    used = max(0.0, min(100.0, (total - remaining) / total * 100))
    detail = f"{remaining:.0f} de {total:.0f} restantes"
    if window:
        detail += f" · janela de {_hours(window)}"
    return Window(f"mode:{key}", label, round(used, 1), now + wait if wait else None, detail)


def _from_web(cookie: str) -> Report:
    headers = {
        "Cookie": cookie.strip().removeprefix("Cookie:").strip(),
        "User-Agent": net.BROWSER_UA,
        "Origin": "https://grok.com",
        "Referer": "https://grok.com/",
    }
    windows: list[Window] = []
    denied = 0
    for model, label in WEB_MODELS:
        try:
            data = net.post_json(
                WEB_LIMITS, {"requestKind": "DEFAULT", "modelName": model}, headers=headers, timeout=10
            )
        except net.NetError as exc:
            denied += exc.status in (401, 403)
            continue
        window = parse_rate(data, model, label)
        if window and not any(w.used == window.used and w.detail == window.detail for w in windows):
            windows.append(window)
    if windows:
        return Report("grok", "ok", windows, source="grok.com")
    if denied:
        raise ProviderError(
            "O grok.com recusou o cookie (expirou ou foi bloqueado). Copie um novo em Ajustes.",
            transient=False,
        )
    raise ProviderError("Sem resposta do grok.com agora.")


def fetch(ctx: FetchContext) -> Report:
    if ctx.mode == "off":
        return Report("grok", "off")
    cli = read_cli_token(ctx.home) if ctx.mode in ("auto", "cli") else None
    cookie = ctx.secret("grok_cookie") if ctx.mode in ("auto", "web") else ""
    if cli:
        try:
            return _from_cli(*cli)
        except ProviderError:
            if not cookie:
                raise
    if cookie:
        return _from_web(cookie)
    hint = SETUP_HINT
    if ctx.mode == "cli":
        hint = "Grok CLI não encontrado. Instale-o e faça login com o comando grok."
    elif ctx.mode == "web":
        hint = "Cole o cookie do grok.com em Ajustes."
    return Report("grok", "setup", message=hint)
