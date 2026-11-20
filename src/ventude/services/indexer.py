"""Índice instantâneo da busca universal: apps instalados, pastas e arquivos do usuário.

- Apps: atalhos .lnk/.appref-ms do menu Iniciar (do usuário e de todos) e apps da Loja (Get-StartApps).
- Pastas e arquivos: Área de Trabalho, Downloads, Documentos, Imagens, Vídeos, Músicas, a pasta do
  usuário e as raízes dos discos, até 3–4 níveis, pulando caches e builds (node_modules, .git, .venv,
  AppData...). Fica em memória (no máximo 60 mil itens) e é refeito a cada 15 minutos.

Pastas abrem no Explorer (Enter), no Terminal (Ctrl + Enter) ou no Cursor/VS Code (Shift + Enter).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import string
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)

REFRESH_MS = 15 * 60_000
FILE_LIMIT = 60_000
SCAN_BUDGET = 25.0  # segundos no máximo por varredura de arquivos
APP_SUFFIXES = (".lnk", ".appref-ms")
SKIP_APPS = (
    "uninstall",
    "desinstalar",
    "remover ",
    "readme",
    "leia-me",
    "help",
    "ajuda",
    "website",
    "manual",
)
IGNORED = {
    "node_modules",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "appdata",
    "application data",
    "$recycle.bin",
    "system volume information",
    "windows",
    "windows.old",
    "program files",
    "program files (x86)",
    "programdata",
    "recovery",
    "perflogs",
    "msocache",
    "config.msi",
    "$windows.~bt",
    "$windows.~ws",
    "$winreagent",
    ".cache",
    "build",
    "dist",
    "target",
    "out",
    ".idea",
    ".vs",
    ".vscode",
    ".gradle",
    ".m2",
    ".nuget",
    ".npm",
    ".cargo",
    ".rustup",
    "site-packages",
    "onedrivetemp",
    "temp",
    "tmp",
    "cache",
    "caches",
}
KIND_BONUS = {"app": 25, "uwp": 25, "folder": 8, "file": 0}
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010


@dataclass(frozen=True)
class Entry:
    name: str
    path: str  # caminho; nos apps da Loja, o AppID
    kind: str  # app | uwp | folder | file
    key: str = ""  # nome em minúsculas (busca)

    @property
    def is_folder(self) -> bool:
        return self.kind == "folder"


def entry(name: str, path: str, kind: str) -> Entry:
    return Entry(name, path, kind, name.lower())


# ---------- varredura ----------
def start_menu_dirs() -> list[Path]:
    dirs = []
    for var in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(var)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return dirs


def scan_apps(dirs: list[Path]) -> list[Entry]:
    found: dict[str, Entry] = {}
    for folder in dirs:
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if path.suffix.lower() not in APP_SUFFIXES:
                continue
            name = path.stem
            if any(word in name.lower() for word in SKIP_APPS):
                continue
            found.setdefault(name.lower(), entry(name, str(path), "app"))
    return list(found.values())


def scan_store_apps(known: set[str]) -> list[Entry]:
    """Apps da Loja (UWP) pelo Get-StartApps; os que já têm atalho .lnk ficam de fora."""
    if sys.platform != "win32":
        return []
    command = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"
    )
    try:
        raw = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        ).stdout
        data = json.loads(raw.decode("utf-8", "replace") or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        log.debug("Get-StartApps indisponível", exc_info=True)
        return []
    return store_entries(data, known)


def store_entries(data: object, known: set[str]) -> list[Entry]:
    """Linhas do Get-StartApps -> apps abertos por shell:AppsFolder (Loja e os que não têm .lnk, como o
    Explorador de Arquivos). Os que já vieram por atalho .lnk, links da web e desinstaladores ficam
    de fora."""
    rows = data if isinstance(data, list) else [data]
    out = []
    seen = set(known)
    for row in rows:
        if not isinstance(row, dict):
            continue
        name, app_id = str(row.get("Name") or "").strip(), str(row.get("AppID") or "").strip()
        lowered = name.lower()
        if not name or not app_id or lowered in seen or app_id.lower().startswith(("http:", "https:")):
            continue
        if any(word in lowered for word in SKIP_APPS):
            continue
        seen.add(lowered)
        out.append(entry(name, app_id, "uwp"))
    return out


def file_roots(home: Path) -> list[tuple[Path, int]]:
    """Onde procurar pastas e arquivos (e quantos níveis), do mais importante para o menos."""
    roots = [
        (home / "Desktop", 4),
        (home / "Downloads", 3),
        (home / "Documents", 4),
        (home / "Pictures", 3),
        (home / "Videos", 3),
        (home / "Music", 2),
    ]
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        roots.append((Path(onedrive), 4))
    roots.append((home, 2))
    if sys.platform == "win32":
        drives = os.listdrives() if hasattr(os, "listdrives") else [f"{c}:\\" for c in string.ascii_uppercase]
        for drive in drives:
            root = Path(drive)
            try:
                if root.exists() and shutil.disk_usage(root).total > 0:
                    roots.append((root, 3))
            except OSError:
                continue
    return roots


def skip_dir(name: str) -> bool:
    lowered = name.lower()
    return lowered in IGNORED or lowered.startswith((".", "$", "~"))


def scan_files(
    roots: list[tuple[Path, int]], limit: int = FILE_LIMIT, budget: float = SCAN_BUDGET
) -> list[Entry]:
    started = time.monotonic()
    out: list[Entry] = []
    seen: set[str] = set()
    scanned: set[str] = set()
    for root, depth in roots:
        queue: deque[tuple[str, int]] = deque([(str(root), depth)])
        while queue:
            folder, level = queue.popleft()
            if folder.lower() in scanned:
                continue
            scanned.add(folder.lower())
            if len(out) >= limit or time.monotonic() - started > budget:
                return out
            try:
                with os.scandir(folder) as it:
                    children = list(it)
            except OSError:
                continue
            for child in children:
                try:
                    is_dir = child.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir and skip_dir(child.name):
                    continue
                key = child.path.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(entry(child.name, child.path, "folder" if is_dir else "file"))
                if is_dir and level > 1:
                    queue.append((child.path, level - 1))
    return out


# ---------- busca ----------
def parse_query(text: str) -> tuple[str, bool]:
    """'/p projetos' ou 'pasta: projetos' -> ('projetos', só pastas)."""
    stripped = text.strip()
    lowered = stripped.lower()
    for prefix in ("/p ", "pasta:", "pastas:"):
        if lowered.startswith(prefix):
            return stripped[len(prefix) :].strip(), True
    return stripped, False


def score(item: Entry, query: str, words: list[str]) -> float | None:
    name = item.key
    if name == query:
        value = 100.0
    elif name.startswith(query):
        value = 80.0
    elif any(part.startswith(query) for part in name.replace("-", " ").replace("_", " ").split()):
        value = 60.0
    elif query in name:
        value = 40.0
    elif len(words) > 1 and all(w in name for w in words):
        value = 30.0
    else:
        return None
    return value + KIND_BONUS.get(item.kind, 0) - len(name) / 100


def search(items: list[Entry], text: str, limit: int = 6, folders_only: bool = False) -> list[Entry]:
    query = text.strip().lower()
    if not query:
        return []
    words = query.split()
    first = words[0]
    ranked: list[tuple[float, Entry]] = []
    for item in items:
        if folders_only and not item.is_folder:
            continue
        if first not in item.key:
            continue  # filtro barato antes da pontuação
        value = score(item, query, words)
        if value is not None:
            ranked.append((value, item))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].key))
    return [item for _, item in ranked[:limit]]


# ---------- ações ----------
def launch(item: Entry) -> tuple[bool, str]:
    try:
        if item.kind == "uwp":
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{item.path}"])
        elif sys.platform == "win32":
            os.startfile(item.path)  # type: ignore[attr-defined]
        else:  # pragma: no cover
            subprocess.Popen(["xdg-open", item.path])
    except OSError as exc:
        return False, str(exc)
    return True, ""


def open_terminal(folder: str) -> tuple[bool, str]:
    """Windows Terminal na pasta (ou PowerShell, se o Terminal não estiver instalado)."""
    try:
        terminal = shutil.which("wt")
        if terminal:
            subprocess.Popen([terminal, "-d", folder])
        else:
            literal = folder.replace("'", "''")
            subprocess.Popen(
                ["powershell.exe", "-NoExit", "-Command", f"Set-Location -LiteralPath '{literal}'"],
                creationflags=CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
            )
    except OSError as exc:
        return False, str(exc)
    return True, ""


def find_editor() -> tuple[str, str] | None:
    """(nome, executável) do Cursor ou do VS Code instalados."""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidates = [
        ("Cursor", local / "Programs" / "cursor" / "Cursor.exe"),
        ("Cursor", shutil.which("cursor") or ""),
        ("VS Code", local / "Programs" / "Microsoft VS Code" / "Code.exe"),
        ("VS Code", program_files / "Microsoft VS Code" / "Code.exe"),
        ("VS Code", shutil.which("code") or ""),
    ]
    for name, path in candidates:
        if path and Path(path).exists():
            return name, str(path)
    return None


def open_in_editor(folder: str) -> tuple[bool, str]:
    editor = find_editor()
    if editor is None:
        return False, "Cursor ou VS Code não encontrado"
    name, exe = editor
    try:
        quiet = CREATE_NO_WINDOW if exe.lower().endswith((".cmd", ".bat")) and sys.platform == "win32" else 0
        subprocess.Popen([exe, folder], creationflags=quiet)
    except OSError as exc:
        return False, str(exc)
    return True, name


class Indexer(QObject):
    """Mantém o índice em memória; a varredura roda no pool de threads."""

    ready = Signal(int)  # quantos itens
    apps_ready = Signal(int)  # os apps já estão prontos (o Iniciar não espera os arquivos)

    def __init__(self, run: Callable[..., None], home: Path | None = None) -> None:
        super().__init__()
        self._run = run
        self.home = home or Path.home()
        self.apps: list[Entry] = []
        self.files: list[Entry] = []
        self._busy = False
        self.timer = QTimer(self, interval=REFRESH_MS)
        self.timer.timeout.connect(self.refresh)

    def start(self) -> None:
        self.timer.start()
        self.refresh()

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._run(self._build, done=self._done, fail=self._failed, owner=self)

    def _build(self) -> tuple[list[Entry], list[Entry]]:
        apps = scan_apps(start_menu_dirs())
        apps += scan_store_apps({a.key for a in apps})
        apps.sort(key=lambda a: a.key)
        self.apps = apps  # os apps aparecem na busca antes de terminar os arquivos
        self.apps_ready.emit(len(apps))  # sinal enfileirado: chega na thread da interface
        return apps, scan_files(file_roots(self.home))

    def _done(self, result: tuple[list[Entry], list[Entry]]) -> None:
        self._busy = False
        self.apps, self.files = result
        self.ready.emit(len(self.apps) + len(self.files))

    def _failed(self, error: BaseException) -> None:
        self._busy = False
        log.warning("Índice da busca falhou: %r", error)

    def search(self, text: str, limit: int = 6) -> list[Entry]:
        query, folders_only = parse_query(text)
        if folders_only:
            return search(self.files, query, limit, folders_only=True)
        return search(self.apps + self.files, query, limit)
