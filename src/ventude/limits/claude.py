"""Limites do Claude (sessão de 5h, semana e por modelo).

Fontes, em ordem:
1. Claude Code: lê o login de ~/.claude/.credentials.json (no Windows ou numa
   distribuição WSL aberta) e consulta o mesmo endpoint usado por /usage. Quando o
   token vence (ele dura poucas horas), renova como o próprio Claude Code faz e
   grava o token novo no mesmo arquivo, preservando todo o resto.
2. claude.ai: com a sessionKey (ou o "Copiar como cURL") colada em Ajustes,
   consulta o uso da organização com uma conexão igual à do Chrome.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ventude.core import net
from ventude.core.timefmt import parse_instant
from ventude.limits.model import FetchContext, ProviderError, Report, Window, as_float
from ventude.limits.web import parse_pasted

log = logging.getLogger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URLS = (  # o atual primeiro; o antigo fica de reserva
    "https://platform.claude.com/v1/oauth/token",
    "https://console.anthropic.com/v1/oauth/token",
)
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
OAUTH_BETA = "oauth-2025-04-20"
CODE_UA = "claude-code/2.1.0"
WEB_BASE = "https://claude.ai/api/organizations"
REFRESH_MARGIN = 60  # renova se faltar menos de 1 min

WINDOWS = [
    ("five_hour", "session", "Sessão · 5h"),
    ("seven_day", "weekly", "Semana · todos os modelos"),
    ("seven_day_opus", "weekly_opus", "Semana · Opus"),
    ("seven_day_sonnet", "weekly_sonnet", "Semana · Sonnet"),
]
SETUP_HINT = "Cole a sessionKey do claude.ai em Ajustes (vale para o Claude Desktop) ou use o Claude Code."


@dataclass
class Credentials:
    path: Path
    oauth: dict[str, Any]

    @property
    def token(self) -> str:
        return str(self.oauth.get("accessToken") or "")

    @property
    def expires_at(self) -> float | None:
        value = as_float(self.oauth.get("expiresAt"))
        return value / 1000 if value else None

    def expired(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return bool(self.expires_at and self.expires_at < now + REFRESH_MARGIN)


# ---------- onde está o login do Claude Code ----------
_wsl_cache: tuple[float, list[Path]] = (0.0, [])


def _wsl_homes() -> list[Path]:
    """Pastas de usuário das distribuições WSL que já estão abertas (nunca liga o WSL)."""
    global _wsl_cache
    if sys.platform != "win32":
        return []
    checked, homes = _wsl_cache
    if time.time() - checked < 600:
        return homes
    homes = []
    try:
        raw = subprocess.run(
            ["wsl.exe", "--list", "--running", "--quiet"],
            capture_output=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        text = raw.decode("utf-16-le", "ignore") if b"\x00" in raw else raw.decode("utf-8", "ignore")
        for distro in (line.strip().strip("\x00") for line in text.splitlines()):
            if not distro or " " in distro:
                continue
            root = Path(rf"\\wsl.localhost\{distro}")
            homes += [p for p in (root / "home").glob("*") if p.is_dir()]
            homes.append(root / "root")
    except (OSError, subprocess.SubprocessError):
        pass
    _wsl_cache = (time.time(), homes)
    return homes


def credential_paths(home: Path) -> list[Path]:
    paths = []
    if custom := os.environ.get("CLAUDE_CONFIG_DIR"):
        paths.append(Path(custom) / ".credentials.json")
    paths.append(home / ".claude" / ".credentials.json")
    return paths


def _read(paths: list[Path]) -> Credentials | None:
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
        if isinstance(oauth, dict) and oauth.get("accessToken"):
            return Credentials(path, oauth)
    return None


def find_credentials(home: Path, *, wsl: bool = True) -> Credentials | None:
    """Login do Claude Code no Windows; se não houver, no WSL (só distros já abertas)."""
    found = _read(credential_paths(home))
    if found or not wsl:
        return found
    return _read([h / ".claude" / ".credentials.json" for h in _wsl_homes()])


def read_credentials(home: Path) -> dict[str, Any] | None:
    found = find_credentials(home, wsl=False)
    return found.oauth if found else None


# ---------- renovação do login ----------
def _write_back(path: Path, old_token: str, fields: dict[str, Any]) -> None:
    """Grava o token novo no arquivo do Claude Code sem mexer no resto."""
    data = json.loads(path.read_text(encoding="utf-8"))
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        raise OSError("formato inesperado")
    if oauth.get("accessToken") not in (old_token, None):
        return  # o Claude Code renovou ao mesmo tempo: fica o dele
    oauth.update(fields)
    text = json.dumps(data, indent=2)
    tmp = path.with_name(path.name + ".ventude-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # o Claude Code está lendo o arquivo neste instante
            time.sleep(0.1 * (attempt + 1))
    try:
        path.write_text(text, encoding="utf-8")
    finally:
        tmp.unlink(missing_ok=True)


def refresh_login(creds: Credentials) -> Credentials:
    """Renova o token vencido como o Claude Code faz (refresh_token) e salva no arquivo."""
    refresh = str(creds.oauth.get("refreshToken") or "")
    if not refresh:
        raise ProviderError("O login do Claude Code expirou. Abra o Claude Code para renovar.")
    if not os.access(creds.path, os.W_OK):
        raise ProviderError(
            "O login do Claude Code expirou e o arquivo dele é somente leitura. Abra o Claude Code."
        )
    body = {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": CLIENT_ID}
    data: Any = None
    last = ""
    for url in TOKEN_URLS:
        try:
            try:
                data = net.post_json(url, body, headers={"User-Agent": CODE_UA}, timeout=15)
            except net.NetError as exc:
                if not exc.challenge:
                    raise
                data = net.browser_json("POST", url, json_body=body, timeout=15)
            break
        except net.NetError as exc:
            last = str(exc)
            if exc.status in (400, 401) and "invalid_grant" in (exc.body or "invalid_grant"):
                raise ProviderError(
                    "O login do Claude Code não vale mais. Abra o Claude Code e use /login.",
                    transient=False,
                ) from None
    if not isinstance(data, dict) or not data.get("access_token"):
        raise ProviderError(f"Não deu para renovar o login do Claude Code agora ({last or 'sem resposta'}).")
    expires_in = as_float(data.get("expires_in")) or 8 * 3600
    fields: dict[str, Any] = {
        "accessToken": data["access_token"],
        "refreshToken": data.get("refresh_token") or refresh,
        "expiresAt": int((time.time() + expires_in) * 1000),
    }
    if isinstance(data.get("scope"), str) and data["scope"].strip():
        fields["scopes"] = data["scope"].split()
    try:
        _write_back(creds.path, creds.token, fields)
    except (OSError, ValueError):
        log.warning("Token do Claude Code renovado, mas não foi possível gravar em %s", creds.path)
    log.info("Login do Claude Code renovado")
    return Credentials(creds.path, {**creds.oauth, **fields})


# ---------- leitura do uso ----------
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


def _usage(token: str) -> Any:
    return net.get_json(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": OAUTH_BETA,
            "User-Agent": CODE_UA,
        },
        timeout=12,
    )


def _from_code(creds: Credentials, *, allow_refresh: bool = True) -> Report:
    if creds.expired():
        if not allow_refresh:
            raise ProviderError("O login do Claude Code expirou. Abra o Claude Code para renovar.")
        creds = refresh_login(creds)
    try:
        data = _usage(creds.token)
    except net.NetError as exc:
        if exc.status == 401 and allow_refresh and creds.oauth.get("refreshToken"):
            try:
                creds = refresh_login(creds)
                data = _usage(creds.token)
            except net.NetError:
                raise ProviderError("Token recusado. Abra o Claude Code e faça /login.") from None
        elif exc.status == 401:
            raise ProviderError("Token recusado. Abra o Claude Code e faça /login.") from None
        elif exc.status == 403:
            raise ProviderError(
                "Este login não permite ler o uso. Use /login no Claude Code "
                "(tokens do setup-token não servem).",
                transient=False,
            ) from None
        elif exc.status == 429:
            raise ProviderError("Consultas demais agora; tento de novo em instantes.") from None
        else:
            raise ProviderError(f"Sem conexão com a Anthropic ({exc}).") from None
    oauth = creds.oauth
    source = "Claude Code (WSL)" if str(creds.path).startswith("\\\\wsl") else "Claude Code"
    return Report(
        "claude",
        "ok",
        parse_usage(data),
        plan=plan_label(str(oauth.get("subscriptionType") or ""), str(oauth.get("rateLimitTier") or "")),
        source=source,
    )


def normalize_session_key(raw: str) -> str:
    raw = (raw or "").strip()
    if found := re.search(r"sk-ant-[A-Za-z0-9_\-]+", raw):
        return found.group(0)
    pasted = parse_pasted(raw)
    if value := pasted.cookie_value("sessionKey"):
        return value.strip('"')
    if "sessionKey=" in raw:
        raw = raw.split("sessionKey=", 1)[1]
    return raw.split(";", 1)[0].strip().strip('"')


def _web_error(exc: net.NetError, step: str) -> ProviderError:
    log.info("claude.ai (%s) respondeu %s: %s", step, exc.status or exc, (exc.body or "")[:200])
    if exc.challenge:
        return ProviderError(
            "O claude.ai barrou a leitura (Cloudflare). Abra claude.ai no navegador, tecle F12 > Rede, "
            "clique numa requisição para claude.ai, use 'Copiar como cURL' e cole em Ajustes.",
            transient=False,
        )
    if exc.status in (401, 403):
        return ProviderError(
            "A sessionKey do claude.ai expirou ou foi recusada. Copie uma nova em Ajustes.",
            transient=False,
        )
    if exc.status == 429:
        return ProviderError("Consultas demais ao claude.ai agora; tento de novo em instantes.")
    return ProviderError(f"Sem conexão com o claude.ai ({exc}).")


def _from_web(raw: str) -> Report:
    pasted = parse_pasted(raw)
    session_key = normalize_session_key(raw)
    if not session_key:
        raise ProviderError("Não achei a sessionKey no que foi colado em Ajustes.", transient=False)
    cookie = pasted.cookie if "sessionKey=" in pasted.cookie else f"sessionKey={session_key}"
    headers = {
        "Cookie": cookie,
        "Referer": "https://claude.ai/",
        "Origin": "https://claude.ai",
        "anthropic-client-platform": "web_claude_ai",
    }
    if pasted.user_agent:
        headers["User-Agent"] = pasted.user_agent
    try:
        orgs = net.browser_json("GET", WEB_BASE, headers=headers, timeout=15)
    except net.NetError as exc:
        raise _web_error(exc, "organizações") from None
    if not isinstance(orgs, list) or not orgs:
        raise ProviderError("Nenhuma organização encontrada nessa sessão.", transient=False)
    preferred = pasted.cookie_value("lastActiveOrg")
    org = next((o for o in orgs if o.get("uuid") == preferred), None) or next(
        (o for o in orgs if "chat" in (o.get("capabilities") or [])), orgs[0]
    )
    try:
        data = net.browser_json("GET", f"{WEB_BASE}/{org['uuid']}/usage", headers=headers, timeout=15)
    except net.NetError as exc:
        raise _web_error(exc, "uso") from None
    caps = " ".join(str(c) for c in org.get("capabilities") or [])
    return Report("claude", "ok", parse_usage(data), plan=plan_label(caps), source="claude.ai")


def fetch(ctx: FetchContext) -> Report:
    if ctx.mode == "off":
        return Report("claude", "off")
    creds = find_credentials(ctx.home) if ctx.mode in ("auto", "code") else None
    session = ctx.secret("claude_session") if ctx.mode in ("auto", "web") else ""
    allow_refresh = bool(ctx.options.get("claude_refresh", True))
    if creds:
        try:
            return _from_code(creds, allow_refresh=allow_refresh)
        except ProviderError:
            if not session:
                raise
    if session:
        return _from_web(session)
    hint = SETUP_HINT
    if ctx.mode == "code":
        hint = "Claude Code não encontrado. Instale-o e faça login com o comando claude."
    elif ctx.mode == "web":
        hint = "Cole a sessionKey do claude.ai em Ajustes."
    return Report("claude", "setup", message=hint)
