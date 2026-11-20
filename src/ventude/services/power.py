"""Central de Energia: bloquear, suspender, hibernar, reiniciar, desligar, atualizar e sair da sessão.

Reiniciar, desligar, sair e as versões "atualizar e..." são destrutivas: a interface sempre pede
um segundo clique (ou Enter) antes de chamar estas funções.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)

PENDING_KEYS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
)
SHUTDOWN_FORCE_OTHERS = 0x00000001
SHUTDOWN_RESTART = 0x00000004
SHUTDOWN_POWEROFF = 0x00000008
SHUTDOWN_INSTALL_UPDATES = 0x00000040
# Sistema operacional · atualização · planejado (aparece certinho no log de eventos do Windows).
SHTDN_REASON = 0x00020000 | 0x00000003 | 0x80000000
EWX_LOGOFF = 0x00000000
CREATE_NO_WINDOW = 0x08000000
CHECK_EVERY_MS = 30 * 60_000


@dataclass(frozen=True)
class PowerAction:
    key: str
    title: str
    icon: str
    destructive: bool
    words: tuple[str, ...]
    needs_update: bool = False


ACTIONS: tuple[PowerAction, ...] = (
    PowerAction("lock", "Bloquear o PC", "lock", False, ("bloquear", "lock", "travar")),
    PowerAction("sleep", "Suspender", "moon", False, ("suspender", "dormir", "sleep", "suspensão")),
    PowerAction("hibernate", "Hibernar", "hibernation", False, ("hibernar", "hibernate", "hibernação")),
    PowerAction("restart", "Reiniciar", "rotate-cw", True, ("reiniciar", "restart", "reboot")),
    PowerAction("shutdown", "Desligar", "power", True, ("desligar", "shutdown", "power off")),
    PowerAction(
        "update_restart",
        "Atualizar e reiniciar",
        "refresh-badge",
        True,
        ("atualizar e reiniciar", "update"),
        True,
    ),
    PowerAction(
        "update_shutdown",
        "Atualizar e desligar",
        "power-badge",
        True,
        ("atualizar e desligar", "update"),
        True,
    ),
    PowerAction("logoff", "Sair da sessão", "log-out", True, ("sair da sessão", "logoff", "trocar usuário")),
)
BY_KEY = {a.key: a for a in ACTIONS}


def update_pending(exists: Callable[[str], bool] | None = None) -> bool:
    """O Windows Update está esperando reiniciar para terminar de instalar?"""
    if exists is None:
        if sys.platform != "win32":
            return False
        exists = _hklm_key_exists
    return any(exists(key) for key in PENDING_KEYS)


def _hklm_key_exists(path: str) -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            return True
    except OSError:
        return False


def matching(text: str) -> list[PowerAction]:
    """Ações da Central de Energia que combinam com o que foi digitado na busca."""
    lowered = text.strip().lower()
    if len(lowered) < 3:
        return []
    found = []
    for action in ACTIONS:
        names = (action.title.lower(), *action.words)
        if any(name.startswith(lowered) or lowered in name for name in names):
            found.append(action)
    if lowered in ("energia", "power", "desligar pc"):
        found = list(ACTIONS)
    return found


# ---------- execução (só Windows) ----------
def _run(args: list[str]) -> None:
    subprocess.Popen(args, creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)


def _enable_shutdown_privilege() -> bool:
    """InitiateShutdownW e ExitWindowsEx precisam do privilégio SeShutdownPrivilege ligado."""

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class TOKEN_PRIVILEGES(ctypes.Structure):  # noqa: N801
        _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Luid", LUID), ("Attributes", wintypes.DWORD)]

    advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(LUID)]
    advapi32.AdjustTokenPrivileges.argtypes = [
        wintypes.HANDLE,
        wintypes.BOOL,
        ctypes.POINTER(TOKEN_PRIVILEGES),
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0020 | 0x0008, ctypes.byref(token)):
        return False
    try:
        privileges = TOKEN_PRIVILEGES(1, LUID(), 0x00000002)  # SE_PRIVILEGE_ENABLED
        if not advapi32.LookupPrivilegeValueW(None, "SeShutdownPrivilege", ctypes.byref(privileges.Luid)):
            return False
        return bool(advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(privileges), 0, None, None))
    finally:
        kernel32.CloseHandle(token)


def lock() -> bool:
    return bool(ctypes.windll.user32.LockWorkStation())  # type: ignore[attr-defined]


def sleep() -> bool:
    return bool(ctypes.windll.powrprof.SetSuspendState(0, 0, 0))  # type: ignore[attr-defined]


def hibernate() -> bool:
    """Falha (False) quando a hibernação está desligada no Windows (powercfg /hibernate off)."""
    return bool(ctypes.windll.powrprof.SetSuspendState(1, 0, 0))  # type: ignore[attr-defined]


def restart() -> bool:
    _run(["shutdown", "/r", "/t", "0"])
    return True


def shutdown() -> bool:
    _run(["shutdown", "/s", "/t", "0"])
    return True


def _install_updates_then(flags: int) -> bool:
    """Pede ao Windows Update para instalar o que já baixou e reinicia/desliga instalando."""
    uso = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "UsoClient.exe"
    if uso.exists():
        try:
            subprocess.run(
                [str(uso), "StartInstall"], timeout=15, creationflags=CREATE_NO_WINDOW, check=False
            )
        except (OSError, subprocess.SubprocessError):
            log.info("UsoClient StartInstall não rodou", exc_info=True)
    _enable_shutdown_privilege()
    advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]
    advapi32.InitiateShutdownW.argtypes = [
        wintypes.LPWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi32.InitiateShutdownW.restype = wintypes.DWORD
    result = advapi32.InitiateShutdownW(None, None, 0, SHUTDOWN_INSTALL_UPDATES | flags, SHTDN_REASON)
    if result != 0:
        log.warning("InitiateShutdownW falhou (%s); seguindo sem instalar", result)
        _run(["shutdown", "/r" if flags & SHUTDOWN_RESTART else "/s", "/t", "0"])
    return True


def update_and_restart() -> bool:
    return _install_updates_then(SHUTDOWN_RESTART)


def update_and_shutdown() -> bool:
    return _install_updates_then(SHUTDOWN_POWEROFF)


def logoff() -> bool:
    _enable_shutdown_privilege()
    return bool(ctypes.windll.user32.ExitWindowsEx(EWX_LOGOFF, SHTDN_REASON))  # type: ignore[attr-defined]


RUNNERS: dict[str, Callable[[], bool]] = {
    "lock": lock,
    "sleep": sleep,
    "hibernate": hibernate,
    "restart": restart,
    "shutdown": shutdown,
    "update_restart": update_and_restart,
    "update_shutdown": update_and_shutdown,
    "logoff": logoff,
}


def run(key: str) -> tuple[bool, str]:
    """Executa a ação. Devolve (deu certo, mensagem)."""
    if sys.platform != "win32":
        return False, "Só funciona no Windows."
    runner = RUNNERS.get(key)
    if runner is None:
        return False, "Ação desconhecida."
    try:
        ok = runner()
    except OSError as exc:
        return False, str(exc)
    if not ok and key == "hibernate":
        return False, "A hibernação está desligada neste PC (powercfg /hibernate on liga)."
    return ok, "" if ok else "O Windows recusou o pedido."


class PowerService(QObject):
    """Acompanha se há atualização do Windows esperando reiniciar (selo no botão de Energia)."""

    changed = Signal(bool)

    def __init__(self, check: Callable[[], bool] = update_pending) -> None:
        super().__init__()
        self._check = check
        self.pending = False
        self.timer = QTimer(self, interval=CHECK_EVERY_MS)
        self.timer.timeout.connect(self.refresh)

    def start(self) -> None:
        self.timer.start()
        self.refresh()

    def refresh(self) -> None:
        try:
            pending = bool(self._check())
        except OSError:
            pending = False
        if pending != self.pending:
            self.pending = pending
            self.changed.emit(pending)

    def available(self) -> list[PowerAction]:
        """As ações "atualizar e..." só aparecem com atualização pendente."""
        return [a for a in ACTIONS if not a.needs_update or self.pending]
