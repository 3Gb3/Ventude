"""Catálogo do Ventude Shell: categorias dos apps, páginas de Configurações do Windows, arquivos recentes,
fixados da barra do Windows, conta do usuário, apps na bandeja e utilitários de abrir.

Só leitura do sistema (pastas, registro e processos); nada aqui altera o Windows.
"""

from __future__ import annotations

import ctypes
import getpass
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000
PROTECTED_EXES = frozenset(
    {
        "explorer.exe",
        "csrss.exe",
        "lsass.exe",
        "services.exe",
        "smss.exe",
        "wininit.exe",
        "winlogon.exe",
        "dwm.exe",
        "sihost.exe",
    }
)

# ---------- categorias (Iniciar > Todos > Exibição: Categoria) ----------
CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    (
        "Inteligência Artificial",
        (
            "claude",
            "chatgpt",
            "openai",
            "gemini",
            "copilot",
            "grok",
            "perplexity",
            "ollama",
            "lm studio",
            "antigravity",
            "codex",
            "elevenlabs",
            "midjourney",
            "jan ",
        ),
    ),
    (
        "Desenvolvimento",
        (
            "visual studio",
            "vs code",
            "code",
            "cursor",
            "pycharm",
            "intellij",
            "webstorm",
            "android studio",
            "git",
            "github",
            "terminal",
            "powershell",
            "prompt de comando",
            "python",
            "node",
            "docker",
            "postman",
            "wsl",
            "ubuntu",
            "debian",
            "java",
            "sublime",
            "notepad++",
            "windsurf",
            "zed",
            "dbeaver",
            "insomnia",
            "putty",
            "filezilla",
            "unity",
            "unreal",
            "godot",
        ),
    ),
    (
        "Navegadores",
        ("firefox", "chrome", "edge", "opera", "brave", "vivaldi", "tor browser", "arc", "zen browser"),
    ),
    (
        "Comunicação",
        (
            "discord",
            "whatsapp",
            "telegram",
            "slack",
            "teams",
            "zoom",
            "skype",
            "signal",
            "messenger",
            "linkedin",
            "outlook",
            "mail",
            "e-mail",
            "thunderbird",
            "email",
        ),
    ),
    (
        "Produtividade",
        (
            "word",
            "excel",
            "powerpoint",
            "onenote",
            "office",
            "microsoft 365",
            "notion",
            "obsidian",
            "acrobat",
            "pdf",
            "calendário",
            "calendar",
            "to do",
            "todo",
            "bloco de notas",
            "notepad",
            "sticky",
            "notas autoadesivas",
            "libreoffice",
            "trello",
            "evernote",
            "anydesk",
            "teamviewer",
        ),
    ),
    (
        "Entretenimento",
        (
            "spotify",
            "netflix",
            "prime video",
            "disney",
            "youtube",
            "vlc",
            "media player",
            "reprodutor",
            "filmes",
            "music",
            "música",
            "twitch",
            "deezer",
            "tidal",
            "plex",
            "stremio",
            "kodi",
            "crunchyroll",
            "hbo",
            "globoplay",
        ),
    ),
    (
        "Jogos",
        (
            "steam",
            "epic games",
            "riot",
            "valorant",
            "league of legends",
            "battle.net",
            "xbox",
            "roblox",
            "minecraft",
            "ea app",
            "ubisoft",
            "gog",
            "game",
            "jogo",
            "playnite",
            "porofessor",
            "blitz",
            "overwolf",
            "fortnite",
            "counter-strike",
            "genshin",
            "rockstar",
            "solitaire",
            "paciência",
        ),
    ),
    (
        "Criatividade",
        (
            "photoshop",
            "illustrator",
            "premiere",
            "after effects",
            "lightroom",
            "figma",
            "blender",
            "gimp",
            "krita",
            "paint",
            "obs",
            "davinci",
            "canva",
            "capcut",
            "clipchamp",
            "audacity",
            "handbrake",
            "fotos",
            "photos",
            "ferramenta de captura",
            "snipping",
            "inkscape",
            "affinity",
            "câmera",
            "camera",
            "gravador",
            "sound recorder",
        ),
    ),
    (
        "Utilitários e Ferramentas",
        (
            "calculadora",
            "calculator",
            "relógio",
            "clock",
            "configurações",
            "settings",
            "explorador",
            "explorer",
            "gerenciador",
            "painel de controle",
            "control panel",
            "store",
            "loja",
            "winrar",
            "7-zip",
            "ccleaner",
            "fan control",
            "fancontrol",
            "wallpaper",
            "powertoys",
            "everything",
            "nvidia",
            "amd",
            "radeon",
            "logitech",
            "razer",
            "corsair",
            "redragon",
            "driver",
            "update",
            "atualiza",
            "vpn",
            "radmin",
            "bitwarden",
            "1password",
            "keepass",
            "segurança",
            "security",
            "defender",
            "backup",
            "onedrive",
            "dropbox",
            "google drive",
            "ventude",
            "mouse",
            "teclado",
            "headset",
            "monitor",
            "msi",
            "asus",
            "gigabyte",
            "intel",
            "ferramentas",
            "tools",
            "utilit",
            "terminal",
            "visualizador",
            "viewer",
            "zip",
            "rar",
            "java",
        ),
    ),
]
OTHER = "Outro"
CATEGORY_ORDER = [name for name, _ in CATEGORIES] + [OTHER]
WORD = re.compile(r"[\w+#.-]+", re.UNICODE)


def category(name: str, target: str = "") -> str:
    """Categoria do app pelo nome (e pelo caminho, se o nome não disser nada)."""
    found = _category_of(name)
    if found == OTHER and target:  # nome genérico: tenta pelo .exe (ex.: "App" -> discord.exe)
        found = _category_of(Path(target.replace("!", "\\")).stem)
    return found


def _category_of(name: str) -> str:
    text = f" {name.lower()} "
    words = set(WORD.findall(text))
    for title, keys in CATEGORIES:
        for key in keys:
            if (" " in key or "-" in key or len(key) > 4) and key in text:
                return title
            if key in words:
                return title
    return OTHER


# ---------- Configurações do Windows e itens do sistema ----------
@dataclass(frozen=True)
class SystemItem:
    title: str
    target: str  # ms-settings:..., shell:..., ou um executável do Windows
    icon: str  # ícone de traço do Ventude
    words: tuple[str, ...] = ()
    subtitle: str = "Sistema"


SYSTEM_ITEMS: tuple[SystemItem, ...] = (
    SystemItem("Configurações", "ms-settings:", "settings", ("config", "settings", "ajustes do windows")),
    SystemItem("Wi-Fi", "ms-settings:network-wifi", "wifi", ("wifi", "wi-fi", "rede sem fio", "internet")),
    SystemItem("Rede e Internet", "ms-settings:network", "globe", ("rede", "internet", "ethernet", "ip")),
    SystemItem("Bluetooth e dispositivos", "ms-settings:bluetooth", "bluetooth", ("bluetooth", "fone")),
    SystemItem("Som", "ms-settings:sound", "volume", ("som", "áudio", "audio", "volume", "microfone")),
    SystemItem(
        "Tela", "ms-settings:display", "monitor", ("tela", "monitor", "resolução", "escala", "brilho")
    ),
    SystemItem("Luz noturna", "ms-settings:nightlight", "moon", ("luz noturna", "night light", "noturno")),
    SystemItem("Personalização", "ms-settings:personalization", "edit", ("papel de parede", "tema", "cores")),
    SystemItem("Plano de fundo", "ms-settings:personalization-background", "monitor", ("wallpaper", "fundo")),
    SystemItem("Energia e bateria", "ms-settings:powersleep", "battery", ("energia", "bateria", "suspensão")),
    SystemItem(
        "Armazenamento", "ms-settings:storagesense", "hard-drive", ("armazenamento", "disco", "espaço")
    ),
    SystemItem("Apps instalados", "ms-settings:appsfeatures", "apps", ("apps", "desinstalar", "programas")),
    SystemItem("Aplicativos padrão", "ms-settings:defaultapps", "apps", ("padrão", "abrir com")),
    SystemItem(
        "Inicialização", "ms-settings:startupapps", "power", ("inicialização", "startup", "iniciar com")
    ),
    SystemItem("Contas", "ms-settings:accounts", "home", ("conta", "usuário", "senha", "login")),
    SystemItem("Data e hora", "ms-settings:dateandtime", "clock", ("data", "hora", "fuso", "relógio")),
    SystemItem("Idioma e região", "ms-settings:regionlanguage", "globe", ("idioma", "região", "language")),
    SystemItem("Teclado", "ms-settings:typing", "keyboard", ("teclado", "digitação", "keyboard")),
    SystemItem("Mouse", "ms-settings:mousetouchpad", "dot", ("mouse", "cursor", "touchpad")),
    SystemItem("Notificações", "ms-settings:notifications", "bell", ("notificações", "avisos", "foco")),
    SystemItem("Privacidade e segurança", "ms-settings:privacy", "lock", ("privacidade", "permissões")),
    SystemItem("Segurança do Windows", "windowsdefender:", "lock", ("antivírus", "defender", "segurança")),
    SystemItem("Windows Update", "ms-settings:windowsupdate", "refresh", ("update", "atualizações")),
    SystemItem("Impressoras", "ms-settings:printers", "file", ("impressora", "scanner", "imprimir")),
    SystemItem("Jogos", "ms-settings:gaming-gamebar", "monitor", ("game bar", "modo de jogo", "jogos")),
    SystemItem("Acessibilidade", "ms-settings:easeofaccess", "info", ("acessibilidade", "lupa", "narrador")),
    SystemItem("Sobre o PC", "ms-settings:about", "info", ("sobre", "nome do pc", "especificações")),
    SystemItem("Lixeira", "shell:RecycleBinFolder", "trash", ("lixeira", "recycle", "lixo")),
    SystemItem("Este Computador", "shell:MyComputerFolder", "monitor", ("este computador", "meu computador")),
    SystemItem("Painel de Controle", "control.exe", "settings", ("painel de controle", "control panel")),
    SystemItem(
        "Gerenciador de Tarefas", "taskmgr.exe", "activity", ("gerenciador de tarefas", "task manager")
    ),
    SystemItem("Prompt de Comando", "cmd.exe", "terminal", ("cmd", "prompt", "comando")),
    SystemItem("Executar", "shell:::{2559a1f3-21d7-11d4-bdaf-00c04f60b9f0}", "external", ("executar", "run")),
)


def match_system(text: str, limit: int = 4) -> list[SystemItem]:
    lowered = text.strip().lower()
    if len(lowered) < 2:
        return []
    ranked: list[tuple[int, SystemItem]] = []
    for item in SYSTEM_ITEMS:
        title = item.title.lower()
        if title.startswith(lowered):
            ranked.append((0, item))
        elif any(word.startswith(lowered) for word in item.words) or lowered in title:
            ranked.append((1, item))
        elif len(lowered) >= 4 and any(lowered in word for word in item.words):
            ranked.append((2, item))
    ranked.sort(key=lambda pair: pair[0])
    return [item for _, item in ranked[:limit]]


def open_system(target: str) -> tuple[bool, str]:
    try:
        if target.lower().endswith(".exe"):
            subprocess.Popen([target])
        elif sys.platform == "win32":
            os.startfile(target)  # type: ignore[attr-defined]
        else:  # pragma: no cover
            subprocess.Popen(["xdg-open", target])
    except OSError as exc:
        return False, str(exc)
    return True, ""


# ---------- abrir e mostrar no Explorer ----------
def open_location(path: str) -> tuple[bool, str]:
    """Abre o Explorer com o arquivo/pasta selecionado."""
    if not path:
        return False, "Sem caminho"
    target = path
    if target.lower().endswith(".lnk"):
        from ventude.services.winutil import lnk_target

        target = lnk_target(target) or target
    try:
        subprocess.Popen(["explorer.exe", f"/select,{os.path.normpath(target)}"])
    except OSError as exc:
        return False, str(exc)
    return True, ""


def run_as_admin(path: str) -> tuple[bool, str]:
    """Pede para abrir como administrador (o Windows mostra o aviso do UAC)."""
    if sys.platform != "win32":
        return False, "Só funciona no Windows."
    shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
    shell32.ShellExecuteW.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_int,
    ]
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    result = shell32.ShellExecuteW(None, "runas", path, None, None, 1) or 0
    return (True, "") if result > 32 else (False, "O Windows não abriu como administrador.")


def send_keys(*vks: int) -> bool:
    """Aperta e solta uma combinação (ex.: Win + Tab). Teclas injetadas passam pelo hook do Ventude."""
    if sys.platform != "win32" or not vks:
        return False
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    extended = {0x5B, 0x5C}
    try:
        for vk in vks:
            user32.keybd_event(vk, 0, 0x0001 if vk in extended else 0, 0)
        for vk in reversed(vks):
            user32.keybd_event(vk, 0, 0x0002 | (0x0001 if vk in extended else 0), 0)
    except OSError:
        return False
    return True


def task_view() -> bool:
    return send_keys(0x5B, 0x09)  # Win + Tab


def show_desktop() -> bool:
    return send_keys(0x5B, 0x44)  # Win + D


# ---------- recentes ----------
@dataclass(frozen=True)
class RecentFile:
    name: str
    path: str
    ts: float
    is_dir: bool


SKIP_RECENT = ("ms-screenclip", "ms-settings", "::{")


def recent_folder() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"


def recent_files(limit: int = 12, folder: Path | None = None) -> list[RecentFile]:
    """Arquivos e pastas abertos recentemente no Windows (atalhos da pasta Recent)."""
    from ventude.services.winutil import lnk_target

    folder = folder or recent_folder()
    try:
        links = sorted(
            (entry for entry in os.scandir(folder) if entry.name.lower().endswith(".lnk")),
            key=lambda entry: entry.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []
    out: list[RecentFile] = []
    seen: set[str] = set()
    for link in links:
        if len(out) >= limit:
            break
        if link.name.lower().startswith(SKIP_RECENT):
            continue
        target = lnk_target(link.path)
        if not target or target.lower() in seen:
            continue
        path = Path(target)
        try:
            exists, is_dir = path.exists(), path.is_dir()
        except OSError:
            continue
        if not exists:
            continue
        seen.add(target.lower())
        out.append(RecentFile(path.name or target, target, link.stat().st_mtime, is_dir))
    return out


def relative_time(ts: float, now: float | None = None) -> str:
    """Como o Windows: 'agora', 'há 5 minutos', 'há 16 horas', 'Ontem às 10:12', 'há 9 dias'."""
    now = time.time() if now is None else now
    delta = max(0.0, now - ts)
    moment, today = datetime.fromtimestamp(ts), datetime.fromtimestamp(now)
    if delta < 60:
        return "agora"
    if delta < 3600:
        minutes = int(delta // 60)
        return f"há {minutes} minuto" + ("s" if minutes > 1 else "")
    if moment.date() == today.date() or delta < 6 * 3600:
        hours = int(delta // 3600)
        return f"há {hours} hora" + ("s" if hours > 1 else "")
    days = (today.date() - moment.date()).days
    if days == 1:
        return f"Ontem às {moment:%H:%M}"
    return f"há {days} dias"


# ---------- fixados da barra do Windows (para começar igual) ----------
def windows_taskbar_pins(folder: Path | None = None) -> list[tuple[str, str]]:
    """(nome, atalho .lnk) dos apps fixados na barra do Windows, na ordem alfabética do Explorer."""
    folder = folder or (
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft"
        / "Internet Explorer"
        / "Quick Launch"
        / "User Pinned"
        / "TaskBar"
    )
    try:
        links = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".lnk")
    except OSError:
        return []
    return [(link.stem, str(link)) for link in links]


# Nomes procurados para os fixados iniciais do Iniciar (o primeiro que existir de cada linha).
START_SEEDS: tuple[tuple[str, ...], ...] = (
    ("Microsoft Edge", "Google Chrome", "Firefox", "Mozilla Firefox"),
    ("Explorador de Arquivos", "File Explorer"),
    ("Configurações", "Settings"),
    ("Microsoft Store",),
    ("Fotos", "Photos"),
    ("Calculadora", "Calculator"),
    ("Bloco de Notas", "Notepad"),
    ("Ferramenta de Captura", "Snipping Tool"),
    ("Spotify",),
    ("Discord",),
    ("WhatsApp",),
    ("Steam",),
    ("Visual Studio Code", "Cursor"),
    ("Terminal", "Windows PowerShell"),
    ("Paint",),
    ("Relógio", "Clock"),
    ("Outlook", "Outlook (new)", "Outlook (novo)"),
    ("Xbox",),
)


def pick_start_seeds(names: Iterable[str], limit: int = 12) -> list[str]:
    """Quais apps instalados entram nos fixados iniciais do Iniciar (nomes como estão no índice)."""
    available = {name.lower(): name for name in names}
    chosen: list[str] = []
    for options in START_SEEDS:
        for option in options:
            if option.lower() in available:
                chosen.append(available[option.lower()])
                break
        if len(chosen) >= limit:
            break
    return chosen


# ---------- conta do usuário ----------
def display_name() -> str:
    """Nome que aparece no Iniciar (nome completo da conta, ou o usuário)."""
    if sys.platform == "win32":
        try:
            secur32 = ctypes.windll.secur32  # type: ignore[attr-defined]
            size = wintypes.ULONG(256)
            buf = ctypes.create_unicode_buffer(size.value)
            if secur32.GetUserNameExW(3, buf, ctypes.byref(size)) and buf.value.strip():  # NameDisplay
                return buf.value.strip()
        except (OSError, AttributeError):
            pass
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "Você"


def first_name(name: str) -> str:
    return (name.split() or ["Você"])[0]


def user_sid() -> str:
    if sys.platform != "win32":
        return ""
    try:
        advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]
        sid = ctypes.create_string_buffer(256)
        sid_size, domain_size = wintypes.DWORD(256), wintypes.DWORD(256)
        domain = ctypes.create_unicode_buffer(256)
        use = wintypes.DWORD()
        if not advapi32.LookupAccountNameW(
            None,
            getpass.getuser(),
            sid,
            ctypes.byref(sid_size),
            domain,
            ctypes.byref(domain_size),
            ctypes.byref(use),
        ):
            return ""
        text = ctypes.c_wchar_p()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            return ""
        try:
            return text.value or ""
        finally:
            ctypes.windll.kernel32.LocalFree(text)  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return ""


def account_picture() -> str:
    """Foto da conta do Windows (a maior disponível) ou ''."""
    folders = []
    sid = user_sid()
    if sid:
        folders.append(Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "AccountPictures" / sid)
    folders.append(Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "AccountPictures")
    best: tuple[int, str] = (0, "")
    for folder in folders:
        try:
            for item in folder.iterdir():
                if item.suffix.lower() not in (".jpg", ".jpeg", ".png", ".bmp"):
                    continue
                match = re.search(r"(\d+)\D*$", item.stem)
                size = int(match.group(1)) if match else 1
                if size > best[0]:
                    best = (size, str(item))
        except OSError:
            continue
    return best[1]


# ---------- apps na bandeja (ícones ocultos) ----------
KNOWN_FOLDERS = {
    "{6D809377-6AF0-444B-8957-A3773F02200E}": os.environ.get("ProgramW6432")
    or os.environ.get("ProgramFiles", r"C:\Program Files"),
    "{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}": os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    "{F38BF404-1D43-42F2-9305-67DE0B28FC23}": os.environ.get("SystemRoot", r"C:\Windows"),
    "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}": os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32"
    ),
    "{D65231B0-B2F1-4857-A4CE-A8E7C6EA7D27}": os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "SysWOW64"
    ),
}
HIDDEN_TRAY = (
    "explorer.exe",
    "securityhealthsystray.exe",
    "monotificationux.exe",
    "python.exe",
    "pythonw.exe",
)


def resolve_known_folder(path: str) -> str:
    """'{GUID}\\pasta\\app.exe' (como o Windows guarda) -> caminho de verdade."""
    match = re.match(r"^(\{[0-9A-Fa-f-]{36}\})\\(.*)$", path)
    if not match:
        return path
    base = KNOWN_FOLDERS.get(match.group(1).upper())
    return os.path.join(base, match.group(2)) if base else path


@dataclass(frozen=True)
class TrayApp:
    name: str
    exe: str
    icon: bytes  # PNG guardado pelo Windows (pode vir vazio)


def tray_apps(running: set[str] | None = None) -> list[TrayApp]:
    """Apps com ícone na bandeja que estão abertos agora (registro NotifyIconSettings do Windows 11)."""
    if sys.platform != "win32":
        return []
    import winreg

    from ventude.services.winutil import app_name

    running = running if running is not None else running_exes()
    out: list[TrayApp] = []
    seen: set[str] = set()
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\NotifyIconSettings")
    except OSError:
        return []
    with root:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(root, name) as key:
                    exe = resolve_known_folder(str(_value(key, "ExecutablePath") or ""))
                    tip = str(_value(key, "InitialTooltip") or "").strip(" \u202a\u202c")
                    icon = _value(key, "IconSnapshot") or b""
            except OSError:
                continue
            lowered = exe.lower()
            if (
                not exe
                or lowered in seen
                or os.path.basename(lowered) in HIDDEN_TRAY
                or lowered not in running
            ):
                continue
            seen.add(lowered)
            label = tip.splitlines()[0] if tip else app_name(exe)
            out.append(TrayApp(label[:48], exe, bytes(icon) if isinstance(icon, bytes | bytearray) else b""))
    out.sort(key=lambda app: app.name.lower())
    return out


def _value(key: object, name: str) -> object:
    import winreg

    try:
        return winreg.QueryValueEx(key, name)[0]  # type: ignore[arg-type]
    except OSError:
        return None


def _enum_process_paths(*, skip_own: bool = False) -> list[tuple[int, str]]:
    if sys.platform != "win32":
        return []
    from ventude.services.winutil import process_path

    psapi_pids = (wintypes.DWORD * 4096)()
    needed = wintypes.DWORD()
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    if not kernel32.K32EnumProcesses(psapi_pids, ctypes.sizeof(psapi_pids), ctypes.byref(needed)):
        return []
    count = needed.value // ctypes.sizeof(wintypes.DWORD)
    own_pid = os.getpid() if skip_own else -1
    out: list[tuple[int, str]] = []
    for raw_pid in psapi_pids[:count]:
        pid = int(raw_pid)
        if pid in (0, 4) or pid == own_pid:
            continue
        path = process_path(pid)
        if path:
            out.append((pid, path))
    return out


def running_exes() -> set[str]:
    """Caminhos (em minúsculas) dos .exe abertos agora."""
    return {path.lower() for _pid, path in _enum_process_paths()}


def pids_for_exe(exe: str) -> list[int]:
    """PIDs em execução cujo caminho do .exe corresponde a `exe` (exceto o próprio Ventude)."""
    target = os.path.normcase(os.path.normpath(exe)) if exe else ""
    if sys.platform != "win32" or not target:
        return []
    return [
        pid
        for pid, path in _enum_process_paths(skip_own=True)
        if os.path.normcase(os.path.normpath(path)) == target
    ]


def _post_close_pids(pids: set[int]) -> int:
    if sys.platform != "win32" or not pids:
        return 0
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    handles: list[int] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    callback = proc(lambda hwnd, _data: handles.append(int(hwnd)) or True)
    user32.EnumWindows.argtypes = [proc, wintypes.LPARAM]
    user32.EnumWindows(callback, 0)
    sent = 0
    pid_buf = wintypes.DWORD()
    for hwnd in handles:
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_buf))
        if int(pid_buf.value) in pids:
            if user32.PostMessageW(hwnd, 0x0010, 0, 0):  # WM_CLOSE
                sent += 1
    return sent


def _terminate_pids(pids: list[int]) -> tuple[int, int]:
    if sys.platform != "win32" or not pids:
        return 0, 0
    from ventude.services.winutil import close_handle, open_process

    process_terminate = 0x0001
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    terminated = 0
    denied = 0
    for pid in pids:
        handle = open_process(pid, process_terminate)
        if not handle:
            denied += 1
            continue
        try:
            if kernel32.TerminateProcess(handle, 0):
                terminated += 1
            else:
                denied += 1
        finally:
            close_handle(handle)
    return terminated, denied


def close_background_app(
    exe: str,
    *,
    force: bool = False,
    pids_fn: Callable[[str], list[int]] | None = None,
    post_close_fn: Callable[[set[int]], int] | None = None,
    terminate_fn: Callable[[list[int]], tuple[int, int]] | None = None,
) -> tuple[bool, str]:
    """Fecha um app em segundo plano (WM_CLOSE gentil primeiro, TerminateProcess se force ou sem janela)."""
    clean = (exe or "").strip()
    if not clean:
        return False, "Executável inválido."
    if os.path.basename(clean).lower() in PROTECTED_EXES:
        return False, "Processo do sistema protegido."
    if pids_fn is None and sys.platform != "win32":
        return False, "Só funciona no Windows."
    pids = (pids_fn or pids_for_exe)(clean)
    if not pids:
        return False, "Aplicativo já está fechado."
    if not force:
        sent = (post_close_fn or _post_close_pids)(set(pids))
        if sent > 0:
            return True, ""
    terminated, denied = (terminate_fn or _terminate_pids)(pids)
    if terminated > 0:
        return True, ""
    if denied > 0:
        return False, "Sem permissão para encerrar (processo elevado)."
    return False, "Não foi possível encerrar o processo."


# ---------- apps da Loja (pacotes MSIX) ----------
def package_family(exe: str) -> str:
    """'...\\WindowsApps\\Claude_6.6395.0.0_x64__pzs8sxrjxfjjc\\app\\claude.exe' -> 'Claude_pzs8sxrjxfjjc'."""
    parts = Path(exe).parts
    lowered = [p.lower() for p in parts]
    if "windowsapps" not in lowered:
        return ""
    index = lowered.index("windowsapps")
    if index + 1 >= len(parts):
        return ""
    full = parts[index + 1]
    if "__" not in full:
        return ""
    name = full.split("_", 1)[0]
    publisher = full.rsplit("__", 1)[1]
    return f"{name}_{publisher}" if name and publisher else ""


# ---------- rede (ícone da barra) ----------
def wifi_name() -> str:
    """Nome da rede Wi-Fi conectada ('' se cabo/desconectado)."""
    if sys.platform != "win32":
        return ""
    try:
        output = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True,
            timeout=4,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in output.splitlines():
        key, _, value = line.partition(":")
        if key.strip().upper() == "SSID" and value.strip():
            return value.strip()
    return ""
