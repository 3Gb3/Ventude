"""Limites do Grok.

Fontes, em ordem:
1. Grok CLI (xAI): lê o login de ~/.grok/auth.json e consulta os créditos do ciclo.
2. grok.com: com o cookie (ou o "Copiar como cURL") colado em Ajustes, consulta as
   consultas restantes por modo com uma conexão igual à do Chrome (o grok.com fica
   atrás do Cloudflare, que barra o Python puro).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from ventude.core import net
from ventude.core.timefmt import parse_instant
from ventude.limits.model import FetchContext, ProviderError, Report, Window, as_float, find_key
from ventude.limits.web import parse_pasted

log = logging.getLogger(__name__)

CLI_BASE = "https://cli-chat-proxy.grok.com/v1"
WEB_LIMITS = "https://grok.com/rest/rate-limits"
WEB_MODELS = [
    ("grok-4-auto", "Auto"),
    ("grok-4-fast", "Rápido"),
    ("grok-3", "Rápido"),
    ("grok-4", "Expert"),
    ("grok-4-heavy", "Heavy"),
]
SETUP_HINT = "Cole o cURL ou o cookie do grok.com em Ajustes (vale para o site, o app e os bots)."


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


def _limit_block(data: dict[str, Any]) -> dict[str, Any]:
    if "remainingQueries" in data or "remainingTokens" in data:
        return data
    for name in ("highEffortRateLimits", "lowEffortRateLimits", "rateLimits", "limits"):
        block = data.get(name)
        if isinstance(block, dict) and ("remainingQueries" in block or "remainingTokens" in block):
            return block
    return data


def parse_rate(data: Any, key: str, label: str, now: float | None = None) -> Window | None:
    if not isinstance(data, dict):
        return None
    main = _limit_block(data)
    unit = "consultas"
    remaining = as_float(main.get("remainingQueries"))
    total = None
    for name in ("totalQueries", "maxQueries", "queryLimit", "limit", "total"):
        total = as_float(main.get(name))
        if total:
            break
    if remaining is None or not total:
        remaining = as_float(main.get("remainingTokens"))
        total = as_float(main.get("totalTokens") or main.get("maxTokens"))
        unit = "tokens"
    if remaining is None or not total:
        return None
    now = time.time() if now is None else now
    wait = as_float(main.get("waitTimeSeconds") or data.get("waitTimeSeconds"))
    window = as_float(main.get("windowSizeSeconds") or data.get("windowSizeSeconds"))
    used = max(0.0, min(100.0, (total - remaining) / total * 100))
    if unit == "consultas":
        detail = f"{remaining:.0f} de {total:.0f} restantes"
    else:
        detail = f"{remaining / max(total, 1) * 100:.0f}% dos tokens restantes"
    if window:
        detail += f" · janela de {_hours(window)}"
    return Window(f"mode:{key}", label, round(used, 1), now + wait if wait else None, detail)


def _web_error(exc: net.NetError) -> ProviderError:
    log.info("grok.com respondeu %s: %s", exc.status or exc, (exc.body or "")[:200])
    if exc.challenge:
        return ProviderError(
            "O grok.com barrou a leitura (Cloudflare). Abra grok.com no navegador, tecle F12 > Rede, "
            "clique numa requisição para grok.com, use 'Copiar como cURL' e cole em Ajustes.",
            transient=False,
        )
    if exc.status in (401, 403):
        return ProviderError(
            "O grok.com recusou o cookie (expirou ou saiu da conta). Copie um novo em Ajustes.",
            transient=False,
        )
    return ProviderError(f"Sem resposta do grok.com agora ({exc}).")


def _from_web(raw: str) -> Report:
    pasted = parse_pasted(raw)
    if not pasted.cookie:
        raise ProviderError("Não achei o cookie no que foi colado em Ajustes.", transient=False)
    if not (pasted.cookie_value("sso") or pasted.cookie_value("sso-rw")):
        raise ProviderError(
            "O cookie colado não tem o login do grok.com (sso). Copie de novo com a conta aberta.",
            transient=False,
        )
    headers = {
        **{k: v for k, v in pasted.headers.items() if k not in ("content-type", "accept")},
        "Cookie": pasted.cookie,
        "Origin": "https://grok.com",
        "Referer": "https://grok.com/",
        "Content-Type": "application/json",
    }
    if pasted.user_agent:
        headers["User-Agent"] = pasted.user_agent
    models = [(m, m) for m in pasted.models if m not in {k for k, _ in WEB_MODELS}] + WEB_MODELS
    windows: list[Window] = []
    errors: list[net.NetError] = []
    for model, label in models:
        try:
            data = net.browser_json(
                "POST",
                WEB_LIMITS,
                headers=headers,
                json_body={"requestKind": "DEFAULT", "modelName": model},
                timeout=12,
            )
        except net.NetError as exc:
            errors.append(exc)
            if exc.challenge or exc.status in (401, 403) or exc.status == 0:
                break  # o resto daria o mesmo erro
            continue
        window = parse_rate(data, model, label)
        if window and not any(w.label == window.label for w in windows):
            if not any(w.used == window.used and w.detail == window.detail for w in windows):
                windows.append(window)
    if windows:
        return Report("grok", "ok", windows, source="grok.com")
    if errors:
        worst = next((e for e in errors if e.challenge), None) or next(
            (e for e in errors if e.status in (401, 403)), errors[0]
        )
        raise _web_error(worst)
    raise ProviderError("O grok.com respondeu, mas sem limites para esta conta.")


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
