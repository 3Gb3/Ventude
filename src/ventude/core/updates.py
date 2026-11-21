"""Atualização pelo GitHub: só versões publicadas a partir da main.

Cada push na main gera uma Release (vX.Y.N) com o instalador Ventude-Setup.exe.
Na inicialização, o app empacotado compara a sua versão com a última Release e,
se houver uma mais nova, pergunta se você quer atualizar. Nos Ajustes, o botão "Procurar
atualização" faz a mesma consulta na hora.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ventude.core import net

log = logging.getLogger(__name__)

REPO = "3Gb3/Ventude"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_NAME = "Ventude-Setup.exe"
SETUP_ARGS = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]
_TRUSTED_HOSTS = ("github.com", "githubusercontent.com")


@dataclass(frozen=True)
class Update:
    version: str
    notes: str
    url: str
    size: int
    sha256: str = ""
    page: str = ""


def parse_version(text: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in numbers[:4]) or (0,)


def enabled() -> bool:
    """Só o app instalado se atualiza; rodando do código-fonte, use git pull."""
    if os.environ.get("VENTUDE_NO_UPDATE"):
        return False
    return bool(getattr(sys, "frozen", False)) or bool(os.environ.get("VENTUDE_FORCE_UPDATE_CHECK"))


def parse_release(data: Any, current: str) -> Update | None:
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    if parse_version(tag) <= parse_version(current):
        return None
    asset = next((a for a in data.get("assets") or [] if a.get("name") == ASSET_NAME), None)
    if not asset or not asset.get("browser_download_url"):
        return None
    digest = str(asset.get("digest") or "")
    return Update(
        version=tag.lstrip("vV"),
        notes=str(data.get("body") or "").strip(),
        url=str(asset["browser_download_url"]),
        size=int(asset.get("size") or 0),
        sha256=digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else "",
        page=str(data.get("html_url") or ""),
    )


def latest(current: str) -> Update | None:
    """Consulta a última Release; None quando você já está na mais nova. Falha de rede vira NetError
    (usado pelo botão "Procurar atualização", que mostra o motivo)."""
    data = net.get_json(
        LATEST_URL,
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        timeout=10,
    )
    return parse_release(data, current)


def check(current: str) -> Update | None:
    """Consulta a última Release. Falhas de rede viram None (nunca atrapalham o app)."""
    try:
        return latest(current)
    except net.NetError as exc:
        log.info("Sem informação de atualização agora: %s", exc)
        return None


class Cancelled(Exception):
    pass


def download(
    update: Update,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    folder: Path | None = None,
) -> Path:
    host = (urllib.parse.urlsplit(update.url).hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _TRUSTED_HOSTS):
        raise net.NetError("endereço de download inesperado")
    folder = folder or Path(tempfile.gettempdir())
    for old in folder.glob("Ventude-Setup-*.exe"):
        try:
            old.unlink()
        except OSError:
            pass
    target = folder / f"Ventude-Setup-{update.version}.exe"
    partial = target.with_suffix(".part")
    digest = hashlib.sha256()
    request = urllib.request.Request(update.url, headers={"User-Agent": net.USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response, partial.open("wb") as out:
            total = int(response.headers.get("Content-Length") or update.size or 0)
            done = 0
            while chunk := response.read(256 * 1024):
                if cancel and cancel.is_set():
                    raise Cancelled()
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except Cancelled:
        partial.unlink(missing_ok=True)
        raise
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise net.NetError(str(exc)) from None
    if update.sha256 and digest.hexdigest() != update.sha256:
        partial.unlink(missing_ok=True)
        raise net.NetError("o arquivo baixado não confere com o publicado no GitHub")
    partial.replace(target)
    return target


def launch_installer(path: Path) -> None:
    """Roda o instalador em silêncio; ele fecha o Ventude, atualiza e reabre."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    subprocess.Popen([str(path), *SETUP_ARGS], creationflags=flags, close_fds=True)
