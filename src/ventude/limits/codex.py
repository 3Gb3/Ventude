"""Limites do Codex / ChatGPT (janela de 5h e semanal do plano).

Fontes, em ordem:
1. Codex CLI: lê o login de ~/.codex/auth.json (somente leitura) e consulta o
   mesmo endpoint que o Codex usa para mostrar o uso do plano.
2. chatgpt.com: com o "Copiar como cURL" (ou o cookie) colado em Ajustes, pega o
   token da sessão do site e consulta o mesmo endpoint.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from ventude.core import net
from ventude.core.timefmt import duration, parse_instant
from ventude.limits.model import FetchContext, ProviderError, Report, Window, as_float, find_key
from ventude.limits.web import jwt_claims, parse_pasted

log = logging.getLogger(__name__)

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
SESSION_URL = "https://chatgpt.com/api/auth/session"
SETUP_HINT = "Faça login no Codex (comando codex) ou cole o cURL do chatgpt.com em Ajustes."
PLANS = {"plus": "Plus", "pro": "Pro", "team": "Team", "business": "Business", "enterprise": "Enterprise"}


def auth_path(home: Path) -> Path:
    base = os.environ.get("CODEX_HOME")
    return (Path(base) if base else home / ".codex") / "auth.json"


def read_login(home: Path) -> tuple[str, str] | None:
    """(access_token, account_id) do Codex CLI."""
    try:
        data = json.loads(auth_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        return None
    account = str(tokens.get("account_id") or "")
    if not account:
        claims = jwt_claims(str(tokens.get("id_token") or ""))
        auth = claims.get("https://api.openai.com/auth") if isinstance(claims, dict) else None
        account = str((auth or {}).get("chatgpt_account_id") or "")
    return str(tokens["access_token"]), account


def _window_name(seconds: float | None) -> tuple[str, str]:
    if seconds and abs(seconds - 5 * 3600) <= 600:
        return "session", "Sessão · 5h"
    if seconds and abs(seconds - 7 * 86400) <= 3600:
        return "weekly", "Semana"
    if seconds:
        text = duration(seconds).replace(" 00min", "")
        return f"window_{int(seconds)}", f"Janela de {text}"
    return "window", "Limite"


def parse_usage(data: Any, now: float | None = None) -> tuple[list[Window], str]:
    if not isinstance(data, dict):
        raise ProviderError("Resposta inesperada do ChatGPT.")
    now = time.time() if now is None else now
    limits = data.get("rate_limit") if isinstance(data.get("rate_limit"), dict) else data
    windows: list[Window] = []
    for name in ("primary_window", "secondary_window", "primary", "secondary"):
        block = limits.get(name) if isinstance(limits, dict) else None
        if not isinstance(block, dict):
            continue
        used = as_float(block.get("used_percent"))
        if used is None:
            continue
        seconds = as_float(block.get("limit_window_seconds"))
        if seconds is None and as_float(block.get("window_minutes")):
            seconds = as_float(block.get("window_minutes")) * 60
        reset = parse_instant(block.get("reset_at") or block.get("resets_at"))
        after = as_float(block.get("reset_after_seconds") or block.get("resets_in_seconds"))
        if reset is None and after is not None:
            reset = now + after
        key, label = _window_name(seconds)
        if any(w.key == key for w in windows):
            key = f"{key}_{name}"
        windows.append(Window(key, label, round(max(0.0, min(100.0, used)), 1), reset, period=seconds))
    plan = str(find_key(data, "plan_type") or "")
    return windows, PLANS.get(plan.lower(), plan.capitalize())


def _query(token: str, account: str, *, browser: bool) -> Report:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if account:
        headers["ChatGPT-Account-Id"] = account
    try:
        data = net.browser_json("GET", USAGE_URL, headers=headers, timeout=15)
    except net.NetError as exc:
        log.info("chatgpt.com (uso) respondeu %s: %s", exc.status or exc, (exc.body or "")[:200])
        if exc.challenge:
            raise ProviderError(
                "O chatgpt.com barrou a leitura (Cloudflare). Tente de novo mais tarde.", transient=True
            ) from None
        if exc.status in (401, 403):
            hint = "Cole um cURL novo do chatgpt.com em Ajustes." if browser else "Abra o Codex e faça login."
            raise ProviderError(
                f"O login do ChatGPT expirou ou foi recusado. {hint}", transient=False
            ) from None
        raise ProviderError(f"Sem conexão com o chatgpt.com ({exc}).") from None
    windows, plan = parse_usage(data)
    if not windows:
        raise ProviderError("O ChatGPT não informou limites do Codex para esta conta.", transient=False)
    return Report("codex", "ok", windows, plan=plan, source="chatgpt.com" if browser else "Codex CLI")


def _from_web(raw: str) -> Report:
    pasted = parse_pasted(raw)
    auth = pasted.headers.get("authorization", "")
    token = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else ""
    account = pasted.headers.get("chatgpt-account-id", "")
    if not token:
        if not pasted.cookie:
            raise ProviderError("Não achei o cookie do chatgpt.com no que foi colado.", transient=False)
        headers = {"Cookie": pasted.cookie, "Referer": "https://chatgpt.com/"}
        if pasted.user_agent:
            headers["User-Agent"] = pasted.user_agent
        try:
            session = net.browser_json("GET", SESSION_URL, headers=headers, timeout=15)
        except net.NetError as exc:
            raise ProviderError(f"O chatgpt.com não devolveu a sessão ({exc}).") from None
        session = session if isinstance(session, dict) else {}
        token = str(session.get("accessToken") or "")
        if not account and isinstance(session.get("account"), dict):
            account = str(session["account"].get("id") or "")
        if not token:
            raise ProviderError("O cookie do chatgpt.com não tem login. Copie de novo com a conta aberta.")
    return _query(token, account, browser=True)


def fetch(ctx: FetchContext) -> Report:
    if ctx.mode == "off":
        return Report("codex", "off")
    login = read_login(ctx.home)
    pasted = ctx.secret("codex_session")
    if login:
        token, account = login
        expires = as_float(jwt_claims(token).get("exp"))
        try:
            if expires and expires < time.time():
                raise ProviderError("O login do Codex expirou. Abra o Codex para renovar.")
            return _query(token, account, browser=False)
        except ProviderError:
            if not pasted:
                raise
    if pasted:
        return _from_web(pasted)
    return Report("codex", "setup", message=SETUP_HINT)
