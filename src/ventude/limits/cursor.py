"""Limites do Cursor (uso incluído no plano, por ciclo de cobrança).

Fontes, em ordem:
1. Cursor instalado: lê o login que o próprio Cursor guarda no PC
   (%APPDATA%\\Cursor\\User\\globalStorage\\state.vscdb, somente leitura).
2. cursor.com: com o cookie WorkosCursorSessionToken (ou o "Copiar como cURL")
   colado em Ajustes.

O Grok, o Claude e os outros modelos usados dentro do Cursor contam aqui, no
limite do Cursor, e não no da conta de cada IA.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ventude.core import net
from ventude.core.timefmt import parse_instant
from ventude.limits.model import FetchContext, ProviderError, Report, Window, as_float
from ventude.limits.web import jwt_claims, parse_pasted

log = logging.getLogger(__name__)

SUMMARY_URL = "https://cursor.com/api/usage-summary"
USAGE_URL = "https://cursor.com/api/usage"
SETUP_HINT = "Abra o Cursor logado neste PC ou cole o cookie do cursor.com em Ajustes."
COOKIE = "WorkosCursorSessionToken"


def state_paths(home: Path) -> list[Path]:
    tail = Path("Cursor") / "User" / "globalStorage" / "state.vscdb"
    paths = []
    if appdata := os.environ.get("APPDATA"):
        paths.append(Path(appdata) / tail)
    paths.append(home / "AppData" / "Roaming" / tail)
    if sys.platform == "darwin":
        paths.append(home / "Library" / "Application Support" / tail)
    paths.append(home / ".config" / tail)
    return list(dict.fromkeys(paths))


def _read_items(path: Path, keys: tuple[str, ...]) -> dict[str, str]:
    query = f"SELECT key, value FROM ItemTable WHERE key IN ({','.join('?' * len(keys))})"

    def read(db: Path) -> dict[str, str]:
        con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute(query, keys).fetchall()
        finally:
            con.close()
        out = {}
        for key, value in rows:
            if isinstance(value, bytes):
                value = value.decode("utf-8", "replace")
            out[str(key)] = str(value or "").strip().strip('"')
        return out

    try:
        return read(path)
    except sqlite3.Error:
        # O Cursor aberto pode travar o arquivo: lê uma cópia.
        with tempfile.TemporaryDirectory(prefix="ventude-cursor-") as folder:
            copy = Path(folder) / "state.vscdb"
            shutil.copy2(path, copy)
            for extra in ("-wal", "-shm"):
                side = path.with_name(path.name + extra)
                if side.exists():
                    shutil.copy2(side, copy.with_name(copy.name + extra))
            return read(copy)


def read_login(home: Path) -> tuple[str, str, str] | None:
    """(token, e-mail, plano) do Cursor instalado."""
    keys = ("cursorAuth/accessToken", "cursorAuth/cachedEmail", "cursorAuth/stripeMembershipType")
    for path in state_paths(home):
        if not path.exists():
            continue
        try:
            items = _read_items(path, keys)
        except (OSError, sqlite3.Error):
            log.debug("Não deu para ler %s", path, exc_info=True)
            continue
        token = items.get(keys[0], "")
        if token:
            return token, items.get(keys[1], ""), items.get(keys[2], "")
    return None


def session_cookie(token: str) -> str:
    """Cookie que o cursor.com espera: <id do usuário>::<token>."""
    sub = str(jwt_claims(token).get("sub") or "")
    user = sub.split("|")[-1] if sub else ""
    if not user:
        raise ProviderError("O login do Cursor tem um formato inesperado. Entre de novo no Cursor.")
    return f"{COOKIE}={quote(f'{user}::{token}', safe='')}"


def _money(cents: float | None) -> str:
    return "—" if cents is None else f"US$ {cents / 100:.2f}".replace(".", ",")


def parse_summary(data: Any) -> list[Window]:
    if not isinstance(data, dict):
        return []
    reset = parse_instant(data.get("billingCycleEnd"))
    individual = data.get("individualUsage") if isinstance(data.get("individualUsage"), dict) else {}
    windows = []
    for name, key, label in (
        ("plan", "plan", "Plano · ciclo"),
        ("onDemand", "on_demand", "Uso extra · ciclo"),
    ):
        block = individual.get(name)
        if not isinstance(block, dict) or block.get("enabled") is False:
            continue
        used, limit = as_float(block.get("used")), as_float(block.get("limit"))
        if used is None or not limit:
            continue
        detail = f"{_money(used)} de {_money(limit)}"
        windows.append(Window(key, label, round(max(0.0, min(100.0, used / limit * 100)), 1), reset, detail))
    return windows


def _next_month(start: float) -> float:
    moment = datetime.fromtimestamp(start)
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return moment.replace(year=year, month=month, day=min(moment.day, 28)).timestamp()


def parse_requests(data: Any) -> list[Window]:
    """Formato antigo: requisições rápidas por modelo no mês."""
    if not isinstance(data, dict):
        return []
    start = parse_instant(data.get("startOfMonth"))
    reset = _next_month(start) if start else None
    windows = []
    for model, block in data.items():
        if not isinstance(block, dict):
            continue
        used, limit = as_float(block.get("numRequests")), as_float(block.get("maxRequestUsage"))
        if used is None or not limit:
            continue
        label = f"{model} · requisições" if model != "gpt-4" else "Requisições rápidas"
        detail = f"{used:.0f} de {limit:.0f} no mês"
        windows.append(
            Window(f"requests:{model}", label, round(min(100.0, used / limit * 100), 1), reset, detail)
        )
    return windows


def _get(url: str, headers: dict[str, str]) -> Any:
    return net.browser_json("GET", url, headers=headers, timeout=15)


def _query(cookie: str, user_agent: str, source: str, plan: str = "", email: str = "") -> Report:
    headers = {"Cookie": cookie, "Origin": "https://cursor.com", "Referer": "https://cursor.com/dashboard"}
    if user_agent:
        headers["User-Agent"] = user_agent
    windows: list[Window] = []
    try:
        summary = _get(SUMMARY_URL, headers)
        windows = parse_summary(summary)
        if isinstance(summary, dict) and not plan:
            plan = str(summary.get("membershipType") or "")
    except net.NetError as exc:
        log.info("cursor.com (resumo) respondeu %s: %s", exc.status or exc, (exc.body or "")[:200])
        if exc.status in (401, 403) and not exc.challenge:
            raise ProviderError(
                "O cursor.com recusou o login. Entre de novo no Cursor ou cole um cookie novo.",
                transient=False,
            ) from None
        if exc.challenge:
            raise ProviderError("O cursor.com barrou a leitura (Cloudflare). Tento de novo depois.") from None
    if not windows:
        user = cookie.split("=", 1)[-1].split("%3A%3A", 1)[0]
        try:
            windows = parse_requests(_get(f"{USAGE_URL}?user={user}", headers))
        except net.NetError as exc:
            raise ProviderError(f"Sem resposta do cursor.com agora ({exc}).") from None
    if not windows:
        raise ProviderError("O Cursor não informou limites para esta conta.", transient=False)
    pretty = plan.replace("_", " ").title() if plan else ""
    return Report("cursor", "ok", windows, plan=pretty, account=email, source=source)


def fetch(ctx: FetchContext) -> Report:
    if ctx.mode == "off":
        return Report("cursor", "off")
    login = read_login(ctx.home)
    pasted = ctx.secret("cursor_cookie")
    if login:
        token, email, plan = login
        try:
            expires = as_float(jwt_claims(token).get("exp"))
            if expires and expires < time.time():
                raise ProviderError("O login do Cursor expirou. Abra o Cursor para renovar.")
            return _query(session_cookie(token), "", "Cursor", plan, email)
        except ProviderError:
            if not pasted:
                raise
    if pasted:
        info = parse_pasted(pasted)
        value = info.cookie_value(COOKIE) or pasted.strip().strip("'\"")
        if "::" in value:
            value = quote(value, safe="")
        if "%3A%3A" not in value:
            raise ProviderError(f"Não achei o cookie {COOKIE} no que foi colado.", transient=False)
        return _query(f"{COOKIE}={value}", info.user_agent, "cursor.com")
    return Report("cursor", "setup", message=SETUP_HINT)
