"""Limites do Claude (sessão de 5h, semana e por modelo).

Fontes, em ordem:
1. Claude Code: lê o token OAuth de ~/.claude/.credentials.json (somente leitura,
   nunca renova nem altera o arquivo) e consulta o mesmo endpoint usado por /usage.
2. claude.ai: com a sessionKey colada em Ajustes, consulta o uso da organização.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ventude.core import net
from ventude.core.timefmt import parse_instant
from ventude.limits.model import FetchContext, ProviderError, Report, Window, as_float

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
CODE_UA = "claude-code/2.1.0"
WEB_BASE = "https://claude.ai/api/organizations"

WINDOWS = [
    ("five_hour", "session", "Sessão · 5h"),
    ("seven_day", "weekly", "Semana · todos os modelos"),
    ("seven_day_opus", "weekly_opus", "Semana · Opus"),
    ("seven_day_sonnet", "weekly_sonnet", "Semana · Sonnet"),
]
SETUP_HINT = "Faça login no Claude Code (comando claude) ou cole a sessionKey do claude.ai em Ajustes."


def credential_paths(home: Path) -> list[Path]:
    paths = []
    if custom := os.environ.get("CLAUDE_CONFIG_DIR"):
        paths.append(Path(custom) / ".credentials.json")
    paths.append(home / ".claude" / ".credentials.json")
    return paths


def read_credentials(home: Path) -> dict[str, Any] | None:
    for path in credential_paths(home):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
        if isinstance(oauth, dict) and oauth.get("accessToken"):
            return oauth
    return None


def plan_label(subscription: str, tier: str = "") -> str:
    text = f"{subscription} {tier}".lower()
    if "max" in text:
        if "20x" in text:
            return "Max 20x"
        if "5x" in text:
            return "Max 5x"
        return "Max"
    for key, label in (("enterprise", "Enterprise"), ("team", "Team"), ("pro", "Pro")):
        if key in text:
            return label
    return "Free" if "free" in text else ""


def parse_usage(data: Any) -> list[Window]:
    if not isinstance(data, dict):
        raise ProviderError("Resposta inesperada do Claude.")
    windows: list[Window] = []
    for field_name, key, label in WINDOWS:
        block = data.get(field_name)
        if field_name == "seven_day" and not isinstance(block, dict):
            block = data.get("seven_day_oauth_apps")
        if not isinstance(block, dict):
            continue
        used = as_float(block.get("utilization"))
        resets = parse_instant(block.get("resets_at"))
        if used is None:
            continue
        if key.startswith("weekly_") and used <= 0 and not resets:
            continue  # janela por modelo sem uso nenhum: só polui a tela
        windows.append(Window(key, label, used, resets))
    extra = data.get("extra_usage")
    if isinstance(extra, dict) and extra.get("is_enabled"):
        used = as_float(extra.get("utilization"))
        if used is not None:
            windows.append(Window("extra", "Uso extra · mês", used, None))
    return windows


def _from_code(oauth: dict[str, Any]) -> Report:
    expires = as_float(oauth.get("expiresAt"))
    if expires and expires / 1000 < time.time() + 30:
        raise ProviderError("O login do Claude Code expirou. Abra o Claude Code para renovar.")
    try:
        data = net.get_json(
            USAGE_URL,
            headers={
                "Authorization": f"Bearer {oauth['accessToken']}",
                "anthropic-beta": OAUTH_BETA,
                "User-Agent": CODE_UA,
            },
            timeout=12,
        )
    except net.NetError as exc:
        if exc.status == 401:
            raise ProviderError("Token recusado. Abra o Claude Code e faça /login.") from None
        if exc.status == 403:
            raise ProviderError(
                "Este login não permite ler o uso. Use /login no Claude Code "
                "(tokens do setup-token não servem).",
                transient=False,
            ) from None
        if exc.status == 429:
            raise ProviderError("Consultas demais agora; tento de novo em instantes.") from None
        raise ProviderError(f"Sem conexão com a Anthropic ({exc}).") from None
    return Report(
        "claude",
        "ok",
        parse_usage(data),
        plan=plan_label(str(oauth.get("subscriptionType") or ""), str(oauth.get("rateLimitTier") or "")),
        source="Claude Code",
    )


def normalize_session_key(raw: str) -> str:
    raw = raw.strip()
    if "sessionKey=" in raw:
        raw = raw.split("sessionKey=", 1)[1]
    return raw.split(";", 1)[0].strip().strip('"')


def _from_web(session_key: str) -> Report:
    headers = {
        "Cookie": f"sessionKey={normalize_session_key(session_key)}",
        "User-Agent": net.BROWSER_UA,
        "Referer": "https://claude.ai/",
        "anthropic-client-platform": "web_claude_ai",
    }
    try:
        orgs = net.get_json(WEB_BASE, headers=headers, timeout=12)
        if not isinstance(orgs, list) or not orgs:
            raise ProviderError("Nenhuma organização encontrada nessa sessão.", transient=False)
        org = next((o for o in orgs if "chat" in (o.get("capabilities") or [])), orgs[0])
        data = net.get_json(f"{WEB_BASE}/{org['uuid']}/usage", headers=headers, timeout=12)
    except net.NetError as exc:
        if exc.status in (401, 403):
            raise ProviderError(
                "A sessionKey do claude.ai expirou ou foi bloqueada. Copie uma nova em Ajustes.",
                transient=False,
            ) from None
        raise ProviderError(f"Sem conexão com o claude.ai ({exc}).") from None
    caps = " ".join(str(c) for c in org.get("capabilities") or [])
    return Report("claude", "ok", parse_usage(data), plan=plan_label(caps), source="claude.ai")


def fetch(ctx: FetchContext) -> Report:
    if ctx.mode == "off":
        return Report("claude", "off")
    oauth = read_credentials(ctx.home) if ctx.mode in ("auto", "code") else None
    session_key = ctx.secret("claude_session") if ctx.mode in ("auto", "web") else ""
    if oauth:
        try:
            return _from_code(oauth)
        except ProviderError:
            if not session_key:
                raise
    if session_key:
        return _from_web(session_key)
    hint = SETUP_HINT
    if ctx.mode == "code":
        hint = "Claude Code não encontrado. Instale-o e faça login com o comando claude."
    elif ctx.mode == "web":
        hint = "Cole a sessionKey do claude.ai em Ajustes."
    return Report("claude", "setup", message=hint)
