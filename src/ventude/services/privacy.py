"""Quem está usando o microfone e a câmera agora (o mesmo registro que o Windows usa no ícone da bandeja).

Em HKCU\\...\\CapabilityAccessManager\\ConsentStore\\{microphone,webcam}, cada app tem
LastUsedTimeStart/LastUsedTimeStop; enquanto está usando, LastUsedTimeStop fica 0.
Apps "de desktop" ficam em NonPackaged, com o caminho do .exe usando # no lugar de \\.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)

STORE = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
CAPABILITIES = {"mic": "microphone", "cam": "webcam"}
INTERVAL_MS = 2000


@dataclass(frozen=True)
class PrivacyState:
    mic: tuple[str, ...] = ()  # nomes dos apps usando o microfone
    cam: tuple[str, ...] = ()

    def text(self, kind: str) -> str:
        apps = self.mic if kind == "mic" else self.cam
        what = "Microfone" if kind == "mic" else "Câmera"
        if not apps:
            return ""
        names = ", ".join(apps[:3]) + (f" e mais {len(apps) - 3}" if len(apps) > 3 else "")
        return f"{what} em uso por {names}"


def packaged_name(key: str) -> str:
    """'Microsoft.WindowsCamera_8wekyb3d8bbwe' -> 'Windows Camera'."""
    core = key.split("_", 1)[0]
    core = core.split(".", 1)[1] if "." in core else core
    spaced = "".join(
        f" {c}" if c.isupper() and i and not core[i - 1].isupper() else c for i, c in enumerate(core)
    )
    return spaced.strip() or key


def desktop_name(key: str) -> str:
    """'C:#Program Files#Discord#Discord.exe' -> 'Discord' (descrição do .exe quando existe)."""
    from ventude.services.winutil import app_name

    return app_name(key.replace("#", "\\"))


def in_use(values: dict[str, int]) -> bool:
    return (
        int(values.get("LastUsedTimeStop", 1) or 0) == 0 and int(values.get("LastUsedTimeStart", 0) or 0) > 0
    )


def apps_in_use(entries: Iterator[tuple[str, bool, dict[str, int]]]) -> tuple[str, ...]:
    """entries: (nome da chave, é NonPackaged?, valores). Devolve os nomes amigáveis, sem repetir."""
    names: list[str] = []
    for key, desktop, values in entries:
        if in_use(values):
            name = desktop_name(key) if desktop else packaged_name(key)
            if name and name not in names:
                names.append(name)
    return tuple(names)


def _registry_entries(capability: str) -> Iterator[tuple[str, bool, dict[str, int]]]:
    import winreg

    def values_of(key: object) -> dict[str, int]:
        out: dict[str, int] = {}
        for name in ("LastUsedTimeStart", "LastUsedTimeStop"):
            try:
                out[name] = int(winreg.QueryValueEx(key, name)[0])  # type: ignore[arg-type]
            except (OSError, ValueError, TypeError):
                pass
        return out

    def children(key: object) -> Iterator[str]:
        index = 0
        while True:
            try:
                yield winreg.EnumKey(key, index)  # type: ignore[arg-type]
            except OSError:
                return
            index += 1

    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{STORE}\\{capability}")
    except OSError:
        return
    with root:
        for name in list(children(root)):
            try:
                with winreg.OpenKey(root, name) as sub:
                    if name == "NonPackaged":
                        for exe in list(children(sub)):
                            try:
                                with winreg.OpenKey(sub, exe) as app:
                                    yield exe, True, values_of(app)
                            except OSError:
                                continue
                    else:
                        yield name, False, values_of(sub)
            except OSError:
                continue


def read_state(
    entries: Callable[[str], Iterator[tuple[str, bool, dict[str, int]]]] | None = None,
) -> PrivacyState:
    if entries is None:
        if sys.platform != "win32":
            return PrivacyState()
        entries = _registry_entries
    return PrivacyState(
        mic=apps_in_use(entries(CAPABILITIES["mic"])),
        cam=apps_in_use(entries(CAPABILITIES["cam"])),
    )


class PrivacyMonitor(QObject):
    """Confere o registro a cada 2 s fora da thread da interface e avisa só quando muda."""

    changed = Signal(object)  # PrivacyState

    def __init__(self, run: Callable[..., None]) -> None:
        super().__init__()
        self._run = run
        self.state = PrivacyState()
        self._busy = False
        self.timer = QTimer(self, interval=INTERVAL_MS)
        self.timer.timeout.connect(self.check)

    def start(self) -> None:
        if sys.platform == "win32":
            self.timer.start()
            self.check()

    def stop(self) -> None:
        self.timer.stop()

    def check(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._run(read_state, done=self._done, fail=self._failed, owner=self)

    def _failed(self, error: BaseException) -> None:
        self._busy = False
        log.debug("Leitura de privacidade falhou: %r", error)

    def _done(self, state: PrivacyState) -> None:
        self._busy = False
        self.apply(state)

    def apply(self, state: PrivacyState) -> None:
        if state != self.state:
            self.state = state
            self.changed.emit(state)
