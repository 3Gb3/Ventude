"""Cotas do Gemini (e demais modelos) no Google Antigravity.

O Antigravity roda um servidor de linguagem local. Enquanto o app está aberto,
achamos o processo, lemos o token CSRF da linha de comando e perguntamos as cotas
por RPC em 127.0.0.1. Nada sai da sua máquina. Com o app fechado, a ilha mostra o
último valor conhecido e já considera as renovações que passaram.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

from ventude.core import net
from ventude.core.timefmt import parse_instant
from ventude.limits.model import (
    FetchContext,
    ProviderError,
    Report,
    Window,
    as_float,
    find_key,
)

log = logging.getLogger(__name__)

RPC = "/exa.language_server_pb.LanguageServerService/"
METHODS = ("GetUserStatus", "RetrieveUserQuotaSummary", "GetCommandModelConfigs")
BODY = {
    "metadata": {
        "ideName": "antigravity",
        "extensionName": "antigravity",
        "ideVersion": "unknown",
        "locale": "pt-BR",
    }
}
FAMILY_ORDER = ("gemini", "claude", "gpt")
NOT_RUNNING = "Abra o Antigravity e faça login: as cotas aparecem enquanto ele está aberto."

_PS_SCRIPT = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$items = Get-CimInstance Win32_Process -Filter "Name LIKE 'language_server%' OR Name LIKE 'agy%'" |
  ForEach-Object {
    $ports = @(Get-NetTCPConnection -State Listen -OwningProcess $_.ProcessId -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty LocalPort)
    [pscustomobject]@{ pid = $_.ProcessId; cmd = $_.CommandLine; ports = $ports }
  }
ConvertTo-Json -InputObject @($items) -Compress -Depth 3
"""


@dataclass
class ServerProcess:
    pid: int
    csrf: str
    command: str
    ports: list[int] = field(default_factory=list)
    extension_port: int | None = None
    extension_csrf: str = ""


def parse_flag(command: str, name: str) -> str | None:
    match = re.search(rf"--{re.escape(name)}(?:=|\s+)(\"[^\"]*\"|'[^']*'|[^\s\"']+)", command)
    return match.group(1).strip("\"'") if match else None


def parse_process(pid: int, command: str, ports: list[int] | None = None) -> ServerProcess | None:
    lowered = (command or "").lower()
    is_agy = re.search(r"(^|[\\/\s\"])agy(\.exe)?(\"|\s|$)", lowered)
    is_server = re.search(r"language[_-]server", lowered) and "antigravity" in lowered
    if not (is_agy or is_server):
        return None  # ignora servidores de outros editores (ex.: Windsurf)
    csrf = parse_flag(command, "csrf_token")
    if not csrf:
        return None
    ext = parse_flag(command, "extension_server_port")
    return ServerProcess(
        pid=int(pid),
        csrf=csrf,
        command=command,
        ports=sorted({int(p) for p in ports or [] if int(p) > 0}),
        extension_port=int(ext) if ext and ext.isdigit() else None,
        extension_csrf=parse_flag(command, "extension_server_csrf_token") or "",
    )


def parse_netstat(text: str, pid: int) -> list[int]:
    """Portas em escuta de um PID na saída de `netstat -ano` (qualquer idioma)."""
    ports: set[int] = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() != "TCP" or parts[-1] != str(pid):
            continue
        if parts[2] not in ("0.0.0.0:0", "[::]:0", "*:*"):
            continue
        host, _, port = parts[1].rpartition(":")
        if host in ("127.0.0.1", "0.0.0.0", "[::1]", "[::]") and port.isdigit():
            ports.add(int(port))
    return sorted(ports)


def parse_lsof(text: str) -> list[int]:
    return sorted({int(m) for m in re.findall(r":(\d+) \(LISTEN\)", text)})


def _run(args: list[str], timeout: float = 10) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            creationflags=flags,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout or ""


def find_processes() -> list[ServerProcess]:
    found: list[ServerProcess] = []
    if sys.platform == "win32":
        # tasklist é barato; o PowerShell (linha de comando + portas) só roda se precisar.
        running = _run(["tasklist", "/FO", "CSV", "/NH"]).lower()
        if "language_server" not in running and '"agy.exe"' not in running:
            return found
        script = base64.b64encode(_PS_SCRIPT.encode("utf-16-le")).decode()
        raw = _run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", script], 15)
        try:
            rows = json.loads(raw or "[]")
        except json.JSONDecodeError:
            rows = []
        for row in rows if isinstance(rows, list) else [rows]:
            if not isinstance(row, dict):
                continue
            ports = row.get("ports") or []
            proc = parse_process(row.get("pid") or 0, row.get("cmd") or "", ports)
            if proc and not proc.ports:
                proc.ports = parse_netstat(_run(["netstat", "-ano", "-p", "TCP"]), proc.pid)
            if proc:
                found.append(proc)
    else:
        for line in _run(["ps", "-ax", "-o", "pid=,command="]).splitlines():
            pid, _, command = line.strip().partition(" ")
            if not pid.isdigit():
                continue
            proc = parse_process(int(pid), command)
            if proc:
                proc.ports = parse_lsof(_run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", pid]))
                found.append(proc)
    return found


def _family(label: str) -> int:
    lowered = label.lower()
    for index, family in enumerate(FAMILY_ORDER):
        if family in lowered:
            return index
    return len(FAMILY_ORDER)


def merge_labels(labels: list[str]) -> str:
    unique = list(dict.fromkeys(labels))
    if len(unique) == 1:
        return unique[0]
    bases = list(dict.fromkeys(label.split(" (")[0].strip() for label in unique))
    if len(bases) == 1:
        return bases[0]
    families = {label.split()[0] for label in unique if label.split()}
    if len(families) == 1:
        return f"{families.pop()} · {len(unique)} modelos"
    return f"{unique[0]} +{len(unique) - 1}"


def extract(data: Any) -> tuple[list[Window], str, str]:
    """Extrai cotas de qualquer formato conhecido (GetUserStatus ou resumo novo)."""
    entries: list[tuple[str, float, float | None]] = []

    def add(label: str, quota: dict[str, Any], node: dict[str, Any]) -> None:
        fraction = as_float(quota.get("remainingFraction"))
        reset = parse_instant(quota.get("resetTime") or node.get("resetTime"))
        if fraction is None:
            if reset is None:
                return
            fraction = 0.0  # proto3 omite zero: sem fração + com renovação = esgotado
        used = round(max(0.0, min(1.0, 1.0 - fraction)) * 100, 1)
        entries.append((label or "Modelo", used, reset))

    def visit(node: Any, inherited: str = "") -> None:
        if isinstance(node, dict):
            label = next(
                (
                    str(node[k]).strip()
                    for k in ("label", "displayName", "modelName", "bucketId")
                    if isinstance(node.get(k), str) and node[k].strip()
                ),
                "",
            )
            quota = node.get("quotaInfo")
            if isinstance(quota, dict) and quota:
                add(label or inherited, quota, node)
            remaining = node.get("remaining")
            if isinstance(remaining, dict):
                add(label or inherited, remaining, node)
            for key, value in node.items():
                if key not in ("quotaInfo", "remaining"):
                    visit(value, label or inherited)
        elif isinstance(node, list):
            for value in node:
                visit(value, inherited)

    visit(data)
    groups: dict[tuple[float, int], list[str]] = {}
    for label, used, reset in entries:
        groups.setdefault((used, int(reset // 60) if reset else 0), []).append(label)
    windows = []
    for (used, reset_min), labels in groups.items():
        name = merge_labels(labels)
        detail = ", ".join(dict.fromkeys(labels)) if len(labels) > 1 else ""
        windows.append(
            Window(
                "model:" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
                name,
                used,
                reset_min * 60 if reset_min else None,
                detail,
            )
        )
    windows.sort(key=lambda w: (_family(w.label), w.label))

    monthly = as_float(find_key(data, "monthlyPromptCredits"))
    available = as_float(find_key(data, "availablePromptCredits"))
    if monthly and monthly > 0 and available is not None and available >= 0:
        used = max(0.0, min(100.0, (1 - available / monthly) * 100))
        windows.append(
            Window("credits", "Créditos de prompt", round(used, 1), None, f"{available:.0f} de {monthly:.0f}")
        )

    plan = find_key(data, "planName", "tierName", "subscriptionTier")
    if isinstance(plan, dict):
        plan = plan.get("name") or plan.get("displayName") or ""
    account = find_key(data, "email")
    return windows, str(plan or ""), str(account or "")


def _call(scheme: str, port: int, csrf: str, method: str) -> Any:
    return net.post_json(
        f"{scheme}://127.0.0.1:{port}{RPC}{method}",
        BODY,
        headers={"X-Codeium-Csrf-Token": csrf, "Connect-Protocol-Version": "1"},
        timeout=4,
        insecure=True,
    )


def _candidates(proc: ServerProcess) -> list[tuple[str, int, str]]:
    out = [(scheme, port, proc.csrf) for port in proc.ports for scheme in ("https", "http")]
    if proc.extension_port:
        out.append(("http", proc.extension_port, proc.extension_csrf or proc.csrf))
        out.append(("https", proc.extension_port + 1, proc.csrf))
    return list(dict.fromkeys(out))


class AntigravityProvider:
    def __init__(self) -> None:
        self._endpoint: tuple[str, int, str] | None = None

    def _query(self, endpoint: tuple[str, int, str]) -> Report | None:
        scheme, port, csrf = endpoint
        plan = account = ""
        for method in METHODS:
            try:
                data = _call(scheme, port, csrf, method)
            except net.NetError as exc:
                if method == METHODS[0] and exc.status == 0:
                    return None  # porta errada ou protocolo errado
                continue
            windows, found_plan, found_account = extract(data)
            plan, account = plan or found_plan, account or found_account
            if windows:
                return Report("antigravity", "ok", windows, plan, account, "Antigravity")
        if plan or account:
            raise ProviderError("O Antigravity respondeu, mas sem cotas. Use um modelo uma vez e atualize.")
        return None

    def fetch(self, ctx: FetchContext) -> Report:
        if ctx.mode == "off":
            return Report("antigravity", "off")
        if self._endpoint:
            report = self._try(self._endpoint)
            if report:
                return report
            self._endpoint = None
        processes = find_processes()
        for proc in processes:
            for endpoint in _candidates(proc):
                report = self._try(endpoint)
                if report:
                    self._endpoint = endpoint
                    return report
        if ctx.previous and ctx.previous.windows:
            raise ProviderError("Antigravity fechado. Mostrando o último valor conhecido.")
        if processes:
            raise ProviderError("Antigravity aberto, mas não respondeu. Tento de novo em breve.")
        return Report("antigravity", "setup", message=NOT_RUNNING)

    def _try(self, endpoint: tuple[str, int, str]) -> Report | None:
        try:
            return self._query(endpoint)
        except ProviderError:
            raise
        except Exception:  # noqa: BLE001
            log.debug("Antigravity em %s falhou", endpoint[:2], exc_info=True)
            return None


_provider = AntigravityProvider()


def fetch(ctx: FetchContext) -> Report:
    return _provider.fetch(ctx)
