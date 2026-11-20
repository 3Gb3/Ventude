"""Limites do GitHub Copilot (requisições premium, chat e autocompletar do mês).

Fontes, em ordem:
1. Copilot instalado (VS Code, Visual Studio, JetBrains...): lê o token que a
   extensão guarda em %LOCALAPPDATA%\\github-copilot (somente leitura).
2. Token colado em Ajustes (ghu_… ou gho_…).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ventude.core import net
from ventude.core.timefmt import parse_instant
from ventude.limits.model import FetchContext, ProviderError, Report, Window, as_float

log = logging.getLogger(__name__)

USER_URL = "https://api.github.com/copilot_internal/user"
SETUP_HINT = "Entre no Copilot no seu editor (VS Code, por exemplo) ou cole o token em Ajustes."
QUOTAS = {
    "premium_interactions": ("premium", "Requisições premium · mês"),
    "chat": ("chat", "Chat · mês"),
    "completions": ("completions", "Autocompletar · mês"),
}
PLANS = {
    "individual": "Pro",
    "individual_pro": "Pro+",
    "business": "Business",
    "enterprise": "Enterprise",
    "free": "Free",
}


def token_paths(home: Path) -> list[Path]:
    folders = []
    if local := os.environ.get("LOCALAPPDATA"):
        folders.append(Path(local) / "github-copilot")
    folders += [home / "AppData" / "Local" / "github-copilot", home / ".config" / "github-copilot"]
    return [folder / name for folder in dict.fromkeys(folders) for name in ("apps.json", "hosts.json")]


def read_token(home: Path) -> tuple[str, str] | None:
    """(token, usuário) guardado pela extensão do Copilot."""
    for path in token_paths(home):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for host, entry in data.items() if isinstance(data, dict) else []:
            if str(host).startswith("github.com") and isinstance(entry, dict) and entry.get("oauth_token"):
                return str(entry["oauth_token"]), str(entry.get("user") or "")
    return None


def parse_user(data: Any) -> tuple[list[Window], str]:
    if not isinstance(data, dict):
        raise ProviderError("Resposta inesperada do GitHub.")
    reset = parse_instant(data.get("quota_reset_date") or data.get("limited_user_reset_date"))
    windows: list[Window] = []
    snapshots = data.get("quota_snapshots")
    if isinstance(snapshots, dict):
        for name, (key, label) in QUOTAS.items():
            block = snapshots.get(name)
            if not isinstance(block, dict) or block.get("unlimited"):
                continue
            left = as_float(block.get("percent_remaining"))
            total, remaining = as_float(block.get("entitlement")), as_float(block.get("remaining"))
            if left is None and total and remaining is not None:
                left = remaining / total * 100
            if left is None:
                continue
            detail = f"{remaining:.0f} de {total:.0f} restantes" if total and remaining is not None else ""
            used = round(max(0.0, min(100.0, 100 - left)), 1)
            windows.append(Window(key, label, used, reset, detail, period=30 * 86400))
    monthly, left_quota = data.get("monthly_quotas"), data.get("limited_user_quotas")
    if not windows and isinstance(monthly, dict) and isinstance(left_quota, dict):  # plano Free
        for name in ("chat", "completions"):
            total, remaining = as_float(monthly.get(name)), as_float(left_quota.get(name))
            if not total or remaining is None:
                continue
            key, label = QUOTAS[name]
            used = round(max(0.0, min(100.0, (total - remaining) / total * 100)), 1)
            detail = f"{remaining:.0f} de {total:.0f} restantes"
            windows.append(Window(key, label, used, reset, detail, period=30 * 86400))
    plan = str(data.get("copilot_plan") or data.get("access_type_sku") or "")
    return windows, PLANS.get(plan.lower(), plan.replace("_", " ").title())


def fetch(ctx: FetchContext) -> Report:
    if ctx.mode == "off":
        return Report("copilot", "off")
    found = read_token(ctx.home)
    pasted = ctx.secret("copilot_token").strip()
    if not found and not pasted:
        return Report("copilot", "setup", message=SETUP_HINT)
    token, user = found if found else (pasted.removeprefix("token ").strip(), "")
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/json",
        "Editor-Version": "vscode/1.99.0",
        "Editor-Plugin-Version": "copilot-chat/0.26.0",
        "User-Agent": "GitHubCopilotChat/0.26.0",
        "X-Github-Api-Version": "2025-04-01",
    }
    try:
        data = net.get_json(USER_URL, headers=headers, timeout=12)
    except net.NetError as exc:
        log.info("Copilot respondeu %s: %s", exc.status or exc, (exc.body or "")[:200])
        if exc.status in (401, 403, 404):
            raise ProviderError(
                "O GitHub recusou o token do Copilot. Entre de novo no Copilot ou cole outro token.",
                transient=False,
            ) from None
        raise ProviderError(f"Sem conexão com o GitHub ({exc}).") from None
    windows, plan = parse_user(data)
    if not windows:
        return Report(
            "copilot", "ok", [], plan=plan, account=user, source="GitHub", message="Sem limite no plano"
        )
    return Report("copilot", "ok", windows, plan=plan, account=user, source="GitHub")
