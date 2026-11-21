"""Índice instantâneo da busca universal: apps instalados, todas as pastas do PC e os arquivos úteis.

- Apps: atalhos .lnk/.appref-ms do menu Iniciar (do usuário e de todos) e apps da Loja (Get-StartApps).
- Pastas: todas as pastas de todos os discos, sem limite de profundidade (das mais rasas para as mais
  fundas), pulando lixo de build e cache (node_modules, .git, Temp...). Pastas do sistema (AppData,
  Program Files, ProgramData, Windows) e ocultas (.minecraft) entram só alguns níveis, sem arquivos.
- Arquivos: nas pastas do usuário (Área de Trabalho, Downloads, Documentos...) entram todos; no resto
  dos discos entram imagens, vídeos, músicas, documentos e compactados.

O índice fica em memória num formato enxuto (só o caminho e o nome em minúsculas de cada item) e a busca
roda sobre um texto único com `str.find`, então continua instantânea com centenas de milhares de itens.
É refeito a cada 30 minutos, numa thread do pool.

Pastas abrem no Explorer (Enter), no Terminal (Ctrl + Enter) ou no Cursor/VS Code (Shift + Enter).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import string
import subprocess
import sys
import time
from bisect import bisect_right
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)

REFRESH_MS = 30 * 60_000
FOLDER_LIMIT = 200_000
FILE_LIMIT = 200_000
SCAN_BUDGET = 150.0  # segundos no máximo por varredura completa
MAX_CANDIDATES = 4000  # itens pontuados por busca (os que começam com o termo vêm primeiro)
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
    "application data",
    "$recycle.bin",
    "system volume information",
    "windows.old",
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
    "cookies",
    "local settings",
    "nethood",
    "printhood",
    "sendto",
    "start menu",
    "templates",
    "winsxs",
}
# Pastas do sistema que aparecem na busca (e alguns níveis dentro delas), mas sem descer até o fim:
# por dentro são milhares de pastas de cache. Só pastas, nenhum arquivo.
SHALLOW: dict[str, int] = {
    "appdata": 3,  # AppData > Roaming > Discord > ...
    "programdata": 2,
    "program files": 3,
    "program files (x86)": 3,
    "windows": 1,
}
DOT_DEPTH = 2  # pastas ocultas do tipo .minecraft, .ssh, .config
KIND_BONUS = {"app": 25, "uwp": 25, "folder": 8, "file": 0}
APP_KINDS = ("app", "uwp")
# Apelidos e abreviações que não saem das iniciais do nome ("lol" e "vsc" já saem sozinhos:
# League Of Legends, Visual Studio Code). Cada apelido aponta para trechos do nome do app.
ALIASES: dict[str, tuple[str, ...]] = {
    "lol": ("league of legends",),
    "vava": ("valorant",),
    "valo": ("valorant",),
    "disc": ("discord",),
    "dc": ("discord",),
    "cs": ("counter-strike", "counter strike"),
    "cs2": ("counter-strike", "counter strike"),
    "csgo": ("counter-strike", "counter strike"),
    "mine": ("minecraft",),
    "mc": ("minecraft",),
    "zap": ("whatsapp",),
    "zapzap": ("whatsapp",),
    "wpp": ("whatsapp",),
    "wsp": ("whatsapp",),
    "whats": ("whatsapp",),
    "insta": ("instagram",),
    "yt": ("youtube",),
    "ff": ("free fire", "firefox"),
    "r6": ("rainbow six",),
    "ow": ("overwatch",),
    "tft": ("teamfight tactics",),
    "bf": ("battlefield",),
    "pubg": ("pubg", "battlegrounds"),
    "fn": ("fortnite",),
    "gpt": ("chatgpt",),
    "vscode": ("visual studio code",),
    "ps": ("photoshop", "powershell"),
    "pr": ("premiere",),
    "ppt": ("powerpoint",),
    "xls": ("excel",),
    "cmd": ("prompt de comando", "command prompt", "terminal"),
    "tt": ("tiktok",),
    "spot": ("spotify",),
    "tlg": ("telegram",),
    "bnet": ("battle.net",),
    "config": ("configurações", "settings"),
    "calc": ("calculadora", "calculator"),
}
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010

# ---------- tipos de arquivo ----------
IMAGE_EXT = frozenset(
    ".jpg .jpeg .png .gif .webp .bmp .heic .heif .tif .tiff .svg .avif .ico .psd .raw .cr2 .nef .arw .dng"
    .split()
)
VIDEO_EXT = frozenset(".mp4 .mkv .avi .mov .wmv .webm .flv .m4v .mpg .mpeg .3gp .m2ts".split())
AUDIO_EXT = frozenset(".mp3 .wav .flac .aac .ogg .m4a .wma .opus .aiff .aif .mid .midi .amr".split())
DOC_EXT = frozenset(
    ".pdf .doc .docx .xls .xlsx .xlsm .ppt .pptx .odt .ods .odp .txt .md .rtf .csv .epub".split()
)
ARCHIVE_EXT = frozenset(".zip .rar .7z .tar .gz .tgz .bz2 .xz .iso".split())
CATEGORIES: dict[str, frozenset[str]] = {
    "image": IMAGE_EXT,
    "video": VIDEO_EXT,
    "audio": AUDIO_EXT,
    "document": DOC_EXT,
    "archive": ARCHIVE_EXT,
}
# Fora das pastas do usuário (resto dos discos) só estes tipos de arquivo entram no índice.
WIDE_EXT = IMAGE_EXT | VIDEO_EXT | AUDIO_EXT | DOC_EXT | ARCHIVE_EXT

# Filtros digitados na busca: "img: praia", "/v aniversário", "pasta: projetos"...
FILTER_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/p ", "folder"),
    ("pasta:", "folder"),
    ("pastas:", "folder"),
    ("/i ", "image"),
    ("img:", "image"),
    ("imagem:", "image"),
    ("imagens:", "image"),
    ("foto:", "image"),
    ("fotos:", "image"),
    ("/v ", "video"),
    ("video:", "video"),
    ("vídeo:", "video"),
    ("videos:", "video"),
    ("vídeos:", "video"),
    ("/m ", "audio"),
    ("musica:", "audio"),
    ("música:", "audio"),
    ("musicas:", "audio"),
    ("músicas:", "audio"),
    ("audio:", "audio"),
    ("áudio:", "audio"),
    ("/d ", "document"),
    ("doc:", "document"),
    ("docs:", "document"),
    ("documento:", "document"),
    ("documentos:", "document"),
)


def category_of(path: str) -> str:
    """image | video | audio | document | archive | file, pela extensão."""
    ext = os.path.splitext(path)[1].lower()
    for name, exts in CATEGORIES.items():
        if ext in exts:
            return name
    return "file"


@dataclass(frozen=True, slots=True)
class Entry:
    name: str
    path: str  # caminho; nos apps da Loja, o AppID
    kind: str  # app | uwp | folder | file
    key: str = ""  # nome em minúsculas (busca)
    is_project: bool = False
    branch: str = ""

    @property
    def is_folder(self) -> bool:
        return self.kind == "folder"

    @property
    def category(self) -> str:
        """folder, app, image, video, audio, document, archive ou file."""
        if self.kind == "file":
            return category_of(self.path)
        return "app" if self.kind in ("app", "uwp") else self.kind


def entry(
    name: str,
    path: str,
    kind: str,
    *,
    is_project: bool = False,
    branch: str = "",
) -> Entry:
    return Entry(name, path, kind, name.lower(), is_project=is_project, branch=branch)


def read_git_branch(folder: str | Path) -> str:
    """Lê a branch atual (ou hash curto em detached HEAD) a partir de .git/HEAD."""
    git_entry = Path(folder) / ".git"
    try:
        if git_entry.is_file():
            raw_gitdir = git_entry.read_text(encoding="utf-8", errors="ignore").strip()
            if raw_gitdir.lower().startswith("gitdir:"):
                target = raw_gitdir.split(":", 1)[1].strip()
                git_dir = Path(target)
                if not git_dir.is_absolute():
                    git_dir = Path(folder) / git_dir
            else:
                return ""
        elif git_entry.is_dir():
            git_dir = git_entry
        else:
            return ""
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        if ref.startswith("refs/heads/"):
            return ref[len("refs/heads/") :]
        return ref.rsplit("/", 1)[-1]
    return head[:7] if len(head) >= 7 else head


# ---------- índice enxuto ----------
class FileIndex:
    """Pastas e arquivos em listas paralelas (caminho, nome minúsculo, tipo) + texto único para buscar."""

    __slots__ = ("paths", "keys", "folder", "projects", "priority_until", "_blob", "_offsets")

    def __init__(self, entries: Iterable[Entry] = ()) -> None:
        self.paths: list[str] = []
        self.keys: list[str] = []
        self.folder = bytearray()
        self.projects: dict[int, str] = {}  # índice -> branch (pastas com .git)
        self.priority_until = 0  # itens antes deste índice vieram das pastas do usuário
        self._blob = ""
        self._offsets: list[int] = []
        for item in entries:
            self.add(item.path, item.name, item.is_folder, item.is_project, item.branch)

    def __len__(self) -> int:
        return len(self.paths)

    def add(self, path: str, name: str, is_folder: bool, is_project: bool = False, branch: str = "") -> int:
        index = len(self.paths)
        self.paths.append(path)
        self.keys.append(name.lower().replace("\n", " "))
        self.folder.append(1 if is_folder else 0)
        if is_project:
            self.projects[index] = branch
        self._blob = ""
        return index

    def mark_project(self, index: int, branch: str) -> None:
        self.projects[index] = branch

    def count(self, folders: bool) -> int:
        return self.folder.count(1 if folders else 0)

    def finish(self) -> None:
        """Monta o texto de busca (uma vez, no fim da varredura)."""
        self._build()

    def _build(self) -> None:
        offsets: list[int] = []
        pos = 0
        for key in self.keys:
            offsets.append(pos)
            pos += len(key) + 1
        self._offsets = offsets
        self._blob = "\n".join(self.keys) + "\n"

    def entry(self, index: int) -> Entry:
        path = self.paths[index]
        name = os.path.basename(path.rstrip("\\/")) or path
        branch = self.projects.get(index)
        return Entry(
            name,
            path,
            "folder" if self.folder[index] else "file",
            self.keys[index],
            is_project=branch is not None,
            branch=branch or "",
        )

    def __iter__(self) -> Iterator[Entry]:
        for index in range(len(self.paths)):
            yield self.entry(index)

    def project_entries(self) -> list[Entry]:
        return [self.entry(i) for i in sorted(self.projects)]

    def candidates(self, word: str) -> list[int]:
        """Índices cujo nome contém `word`: primeiro os que começam com ele, depois o resto."""
        if not self.keys or not word:
            return []
        if not self._blob:
            self._build()
        blob, offsets = self._blob, self._offsets
        found: list[int] = []
        seen: set[int] = set()
        # 1) nomes que começam com o termo (o texto começa cada nome logo depois de um "\n")
        needle = "\n" + word
        if blob.startswith(word):
            found.append(0)
            seen.add(0)
        start = 0
        while len(found) < MAX_CANDIDATES:
            pos = blob.find(needle, start)
            if pos < 0:
                break
            index = bisect_right(offsets, pos + 1) - 1
            if index not in seen:
                seen.add(index)
                found.append(index)
            start = pos + 1
        # 2) nomes que contêm o termo no meio
        start = 0
        while len(found) < MAX_CANDIDATES:
            pos = blob.find(word, start)
            if pos < 0:
                break
            index = bisect_right(offsets, pos) - 1
            if index not in seen:
                seen.add(index)
                found.append(index)
            nxt = offsets[index + 1] if index + 1 < len(offsets) else len(blob)
            start = max(pos + 1, nxt)
        return found


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


Root = tuple[Path, int] | tuple[Path, int, bool]
DEEP = 64  # "sem limite" de profundidade


def file_roots(home: Path) -> list[tuple[Path, int, bool]]:
    """Onde procurar (caminho, níveis, todos os arquivos?), do mais importante para o menos.

    Pastas do usuário entram com todos os arquivos; o resto dos discos, com todas as pastas e só os
    arquivos de mídia/documentos. Program Files entra raso, só para achar a pasta de cada programa.
    """
    roots: list[tuple[Path, int, bool]] = [
        (home / "Desktop", DEEP, True),
        (home / "Downloads", DEEP, True),
        (home / "Documents", DEEP, True),
        (home / "Pictures", DEEP, True),
        (home / "Videos", DEEP, True),
        (home / "Music", DEEP, True),
    ]
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        roots.append((Path(onedrive), DEEP, True))
    roots.append((home, DEEP, False))
    if sys.platform == "win32":
        drives = os.listdrives() if hasattr(os, "listdrives") else [f"{c}:\\" for c in string.ascii_uppercase]
        for drive in drives:
            root = Path(drive)
            try:
                if root.exists() and shutil.disk_usage(root).total > 0:
                    roots.append((root, DEEP, False))
            except OSError:
                continue
    return roots


def skip_dir(name: str) -> bool:
    lowered = name.lower()
    return lowered in IGNORED or lowered.startswith(("$", "~"))


def shallow_depth(name: str) -> int | None:
    """Quantos níveis descer numa pasta do sistema ou oculta (None: pasta comum, sem limite)."""
    lowered = name.lower()
    if lowered in SHALLOW:
        return SHALLOW[lowered]
    if lowered.startswith("."):
        return DOT_DEPTH
    return None


FILES_ALL, FILES_MEDIA, FILES_NONE = 2, 1, 0


def scan_index(
    roots: Iterable[Root],
    folder_limit: int = FOLDER_LIMIT,
    file_limit: int = FILE_LIMIT,
    budget: float = SCAN_BUDGET,
) -> FileIndex:
    """Varre em largura (pastas rasas primeiro) e devolve o índice enxuto.

    Cada pasta é lida uma vez só (as raízes se sobrepõem: a pasta do usuário contém a Área de Trabalho,
    o disco C: contém a pasta do usuário), então nenhum item entra repetido.
    """
    started = time.monotonic()
    index = FileIndex()
    folders = files = 0
    scanned: set[int] = set()  # hash do caminho em minúsculas de cada pasta já lida
    visited = 0
    priority_open = True
    for root in roots:
        path, depth = root[0], root[1]
        all_files = bool(root[2]) if len(root) > 2 else True  # type: ignore[misc]
        root_mode = FILES_ALL if all_files else FILES_MEDIA
        queue: deque[tuple[str, int, int, int]] = deque([(str(path), depth, -1, root_mode)])
        while queue:
            folder, level, own, mode = queue.popleft()
            marker = hash(folder.lower())
            if marker in scanned:
                continue
            scanned.add(marker)
            if folders >= folder_limit and files >= file_limit:
                index.finish()
                return index
            if time.monotonic() - started > budget:
                log.info("Índice da busca parou pelo tempo: %d pastas, %d arquivos", folders, files)
                index.finish()
                return index
            visited += 1
            if visited % 400 == 0:
                time.sleep(0.002)  # cede a vez para a interface durante varreduras grandes
            try:
                with os.scandir(folder) as it:
                    children = list(it)
            except OSError:
                continue
            if own >= 0 and own not in index.projects and any(c.name.lower() == ".git" for c in children):
                index.mark_project(own, read_git_branch(folder))
            for child in children:
                try:
                    is_dir = child.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    if skip_dir(child.name):
                        continue
                    child_index = -1
                    if folders < folder_limit:
                        proj = False
                        br = ""
                        if level == 1:
                            try:
                                if os.path.exists(os.path.join(child.path, ".git")):
                                    proj = True
                                    br = read_git_branch(child.path)
                            except OSError:
                                pass
                        child_index = index.add(child.path, child.name, True, proj, br)
                        folders += 1
                    next_level, next_mode = level - 1, mode
                    limit = shallow_depth(child.name)
                    if limit is not None:
                        next_level, next_mode = min(next_level, limit), FILES_NONE
                    if next_level >= 1 and hash(child.path.lower()) not in scanned:
                        queue.append((child.path, next_level, child_index, next_mode))
                elif files < file_limit:
                    if mode == FILES_NONE:
                        continue
                    if mode == FILES_MEDIA and os.path.splitext(child.name)[1].lower() not in WIDE_EXT:
                        continue
                    index.add(child.path, child.name, False)
                    files += 1
        if all_files and priority_open:
            index.priority_until = len(index)  # as raízes do usuário vêm primeiro na lista
        else:
            priority_open = False
    index.finish()
    return index


def scan_files(
    roots: Iterable[Root],
    limit: int = FILE_LIMIT,
    budget: float = SCAN_BUDGET,
) -> list[Entry]:
    """Lista de entradas (para testes e listas pequenas). O app usa `scan_index`."""
    return list(scan_index(roots, folder_limit=limit, file_limit=limit, budget=budget))


# ---------- busca ----------
def parse_filter(text: str) -> tuple[str, str]:
    """'img: praia' -> ('praia', 'image'); '/p projetos' -> ('projetos', 'folder'); sem filtro: ''."""
    stripped = text.strip()
    lowered = stripped.lower() + " "
    for prefix, kind in FILTER_PREFIXES:
        if lowered.startswith(prefix):
            return stripped[len(prefix) :].strip(), kind
    return stripped, ""


def parse_query(text: str) -> tuple[str, bool]:
    """'/p projetos' ou 'pasta: projetos' -> ('projetos', só pastas)."""
    query, kind = parse_filter(text)
    if kind == "folder":
        return query, True
    return (text.strip(), False) if kind == "" else (query, False)


def score(item: Entry, query: str, words: list[str]) -> float | None:
    value = _score_key(item.key, item.kind, query, words)
    if item.kind in APP_KINDS:
        nick = nickname_score(item.name, query)
        if nick is not None:
            nick += KIND_BONUS[item.kind] - len(item.key) / 100
            value = nick if value is None else max(value, nick)
    return value


def initials(name: str) -> str:
    """Primeira letra de cada palavra, separando também CamelCase: 'League of Legends' -> 'lol',
    'PowerShell' -> 'ps', 'Grand Theft Auto V' -> 'gtav'."""
    parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-zà-ÿ]+|\d+", name)
    return "".join(p[0] for p in parts).lower()


def _is_subsequence(query: str, text: str) -> bool:
    it = iter(text)
    return all(ch in it for ch in query)


def nickname_score(name: str, query: str) -> float | None:
    """Abreviações dos apps: apelidos (vava, zap), iniciais (lol, vsc) e letras na ordem (vlrnt)."""
    query = query.strip().lower()
    if len(query) < 2:
        return None
    key = name.lower()
    if any(target in key for target in ALIASES.get(query, ())):
        return 90.0  # apelido escolhido de propósito vence quem só começa igual (lol x Lollipop)
    letters = initials(name)
    if len(letters) >= 2:
        if letters == query:
            return 70.0
        if letters.startswith(query):
            return 55.0
    compact = query.replace(" ", "")
    if len(compact) >= 3 and key[:1] == compact[0] and _is_subsequence(compact, key):
        return 20.0
    return None


def _score_key(name: str, kind: str, query: str, words: list[str]) -> float | None:
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
    if kind == "file":
        stem = os.path.splitext(name)[0]
        if stem == query:
            value = max(value, 95.0)  # "foto.jpg" ao buscar "foto"
    return value + KIND_BONUS.get(kind, 0) - len(name) / 100


def _matches_filter(item: Entry, kind_filter: str) -> bool:
    if not kind_filter:
        return True
    if kind_filter == "folder":
        return item.is_folder
    return item.kind == "file" and category_of(item.path) == kind_filter


def search(
    items: list[Entry],
    text: str,
    limit: int = 6,
    folders_only: bool = False,
    kind_filter: str = "",
) -> list[Entry]:
    query = text.strip().lower()
    if not query:
        return []
    if folders_only:
        kind_filter = "folder"
    words = query.split()
    first = words[0]
    ranked: list[tuple[float, Entry]] = []
    for item in items:
        if first not in item.key and item.kind not in APP_KINDS:
            continue  # filtro barato antes da pontuação (apps também casam por apelido e iniciais)
        if not _matches_filter(item, kind_filter):
            continue
        value = score(item, query, words)
        if value is not None:
            ranked.append((value, item))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].key))
    return [item for _, item in ranked[:limit]]


def search_index(index: FileIndex, text: str, limit: int = 6, kind_filter: str = "") -> list[Entry]:
    """Mesma pontuação de `search`, mas sobre o índice enxuto (centenas de milhares de itens)."""
    query = text.strip().lower()
    if not query or not len(index):
        return []
    words = query.split()
    ranked: list[tuple[float, int]] = []
    keys, folder = index.keys, index.folder
    for i in index.candidates(words[0]):
        is_folder = bool(folder[i])
        if kind_filter == "folder" and not is_folder:
            continue
        if kind_filter and kind_filter != "folder":
            if is_folder or category_of(keys[i]) != kind_filter:
                continue
        value = _score_key(keys[i], "folder" if is_folder else "file", query, words)
        if value is not None:
            if i in index.projects:
                value += 4  # projetos (pastas com .git) um pouco na frente
            if i < index.priority_until:
                value += 6  # Área de Trabalho, Downloads, Documentos, Imagens... antes do resto do disco
            ranked.append((value, i))
    ranked.sort(key=lambda pair: (-pair[0], keys[pair[1]]))
    return [index.entry(i) for _, i in ranked[:limit]]


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


def reveal(path: str) -> tuple[bool, str]:
    """Abre o Explorer na pasta do arquivo, com ele selecionado."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer.exe", f"/select,{path}"])
        else:  # pragma: no cover
            subprocess.Popen(["xdg-open", str(Path(path).parent)])
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
    """(nome, executável) do Antigravity, Cursor ou VS Code instalados."""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidates = [
        ("Antigravity", local / "Programs" / "Antigravity" / "Antigravity.exe"),
        ("Antigravity", shutil.which("antigravity") or ""),
        ("Antigravity", shutil.which("agy") or ""),
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


def open_agent(folder: str, command: str = "claude") -> tuple[bool, str]:
    """Abre um agente de IA em terminal diretamente dentro da pasta."""
    cmd = (command or "claude").strip() or "claude"
    wt = shutil.which("wt") or shutil.which("wt.exe")
    try:
        if wt:
            subprocess.Popen([wt, "-d", folder, "--", "cmd", "/k", cmd], cwd=folder)
        else:
            flags = CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
            subprocess.Popen(["cmd.exe", "/k", cmd], cwd=folder, creationflags=flags)
    except OSError as exc:
        return False, str(exc)
    return True, cmd.split()[0]


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
        self.index = FileIndex()
        self._busy = False
        self.timer = QTimer(self, interval=REFRESH_MS)
        self.timer.timeout.connect(self.refresh)

    # Compatível com quem atribui/lê uma lista de entradas (testes e telas antigas).
    @property
    def files(self) -> list[Entry]:
        return list(self.index)

    @files.setter
    def files(self, items: list[Entry]) -> None:
        index = FileIndex(items)
        index.finish()
        self.index = index

    def start(self) -> None:
        self.timer.start()
        self.refresh()

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._run(self._build, done=self._done, fail=self._failed, owner=self)

    def _build(self) -> tuple[list[Entry], FileIndex]:
        apps = scan_apps(start_menu_dirs())
        apps += scan_store_apps({a.key for a in apps})
        apps.sort(key=lambda a: a.key)
        self.apps = apps  # os apps aparecem na busca antes de terminar os arquivos
        self.apps_ready.emit(len(apps))  # sinal enfileirado: chega na thread da interface
        started = time.monotonic()
        index = scan_index(file_roots(self.home))
        log.info(
            "Índice da busca: %d apps, %d pastas, %d arquivos em %.1f s",
            len(apps),
            index.count(True),
            index.count(False),
            time.monotonic() - started,
        )
        return apps, index

    def _done(self, result: tuple[list[Entry], FileIndex]) -> None:
        self._busy = False
        self.apps, self.index = result
        self.ready.emit(len(self.apps) + len(self.index))

    def _failed(self, error: BaseException) -> None:
        self._busy = False
        log.warning("Índice da busca falhou: %r", error)

    @property
    def projects(self) -> list[Entry]:
        return self.index.project_entries()

    def search_files(self, text: str, limit: int = 6, kind_filter: str = "") -> list[Entry]:
        return search_index(self.index, text, limit, kind_filter)

    def search(self, text: str, limit: int = 6) -> list[Entry]:
        query, kind_filter = parse_filter(text)
        if kind_filter:
            return self.search_files(query, limit, kind_filter)
        apps = search(self.apps, query, limit)
        files = self.search_files(query, limit * 2)
        merged = [(score(e, query.lower(), query.lower().split()) or 0.0, n, e) for n, e in enumerate(apps)]
        merged += [
            ((score(e, query.lower(), query.lower().split()) or 0.0) + (4 if e.is_project else 0), 100 + n, e)
            for n, e in enumerate(files)
        ]
        merged.sort(key=lambda t: (-t[0], t[1]))
        return [e for _, _, e in merged[:limit]]
