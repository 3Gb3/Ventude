"""Notificações do Windows (de outros apps) na tela de Notificações do Ventude.

Usa o `UserNotificationListener` do WinRT. O Windows pode pedir permissão na primeira vez
(Configurações > Privacidade > Notificações). O evento de "mudou" não funciona para apps sem pacote da Loja,
então a lista é relida de tempos em tempos numa thread do pool (rápido: só lê o que está na central).
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)

POLL_MS = 6_000
POLL_FAST_MS = 2_000  # com a tela de Notificações aberta
LOGO_SIZE = 32

# AccessStatus do UserNotificationListener
STATUS_NAMES = {0: "unspecified", 1: "allowed", 2: "denied"}


@dataclass(frozen=True)
class WinNotice:
    id: int
    app: str
    aumid: str
    title: str
    text: str
    at: float  # time.time()


@dataclass
class Snapshot:
    access: str  # allowed | denied | unspecified | unavailable
    items: list[WinNotice]
    logos: dict[str, bytes]


def _texts(notification: object) -> tuple[str, str]:
    from winrt.windows.ui.notifications import KnownNotificationBindings

    visual = getattr(notification, "visual", None)
    binding = visual.get_binding(KnownNotificationBindings.toast_generic) if visual is not None else None
    if binding is None:
        return "", ""
    parts = [str(t.text or "").strip() for t in binding.get_text_elements()]
    parts = [t for t in parts if t]
    if not parts:
        return "", ""
    return parts[0], " · ".join(parts[1:])


async def _logo(info: object) -> bytes:
    from winrt.windows.foundation import Size
    from winrt.windows.storage.streams import Buffer, InputStreamOptions

    ref = info.display_info.get_logo(Size(LOGO_SIZE, LOGO_SIZE))  # type: ignore[attr-defined]
    stream = await ref.open_read_async()
    size = int(stream.size)
    if size <= 0 or size > 2_000_000:
        return b""
    buf = Buffer(size)
    await stream.read_async(buf, size, InputStreamOptions.NONE)
    return bytes(buf)


async def _read(known_logos: set[str], ask: bool) -> Snapshot:
    from winrt.windows.ui.notifications import NotificationKinds
    from winrt.windows.ui.notifications.management import UserNotificationListener

    listener = UserNotificationListener.current
    status = int(listener.get_access_status())
    if status != 1 and ask:
        status = int(await listener.request_access_async())
    access = STATUS_NAMES.get(status, "unspecified")
    if access != "allowed":
        return Snapshot(access, [], {})
    items: list[WinNotice] = []
    logos: dict[str, bytes] = {}
    for n in await listener.get_notifications_async(NotificationKinds.TOAST):
        info = n.app_info
        try:
            app = str(info.display_info.display_name) if info is not None else ""
            aumid = str(info.app_user_model_id) if info is not None else ""
        except Exception:  # noqa: BLE001 - app desinstalado depois do aviso
            app, aumid = "", ""
        title, text = _texts(n.notification)
        if not title and not text:
            continue
        try:
            at = n.creation_time.timestamp()
        except Exception:  # noqa: BLE001
            at = 0.0
        items.append(WinNotice(int(n.id), app or "Windows", aumid, title or app, text, at))
        key = aumid or app
        if info is not None and key not in known_logos and key not in logos:
            try:
                logos[key] = await _logo(info)
            except Exception:  # noqa: BLE001 - sem logo: a tela usa um sino
                logos[key] = b""
    return Snapshot(access, items, logos)


def read_snapshot(known_logos: set[str], ask: bool = False) -> Snapshot:
    """Lê a central de notificações do Windows (bloqueia; rode numa thread)."""
    if sys.platform != "win32":
        return Snapshot("unavailable", [], {})
    try:
        return asyncio.run(_read(known_logos, ask))
    except ImportError:
        return Snapshot("unavailable", [], {})
    except Exception as error:  # noqa: BLE001
        log.debug("Notificações do Windows indisponíveis: %s", error)
        return Snapshot("unavailable", [], {})


def remove(ids: list[int]) -> None:
    if sys.platform != "win32" or not ids:
        return
    try:
        from winrt.windows.ui.notifications.management import UserNotificationListener

        listener = UserNotificationListener.current
        for notice_id in ids:
            listener.remove_notification(notice_id)
    except Exception as error:  # noqa: BLE001
        log.debug("Não deu para apagar a notificação do Windows: %s", error)


def open_app(aumid: str) -> bool:
    """Abre o app dono da notificação (apps da Loja e com AppUserModelID)."""
    if sys.platform != "win32" or not aumid:
        return False
    try:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{aumid}"])  # noqa: S603, S607
        return True
    except OSError:
        return False


class WindowsNotifications(QObject):
    """Lista viva das notificações do Windows; `unread` conta as que chegaram e ainda não foram vistas."""

    changed = Signal()

    def __init__(
        self,
        run: Callable[..., None],
        reader: Callable[[set[str], bool], Snapshot] = read_snapshot,
        remover: Callable[[list[int]], None] = remove,
    ) -> None:
        super().__init__()
        self._run, self._reader, self._remover = run, reader, remover
        self.items: list[WinNotice] = []
        self.logos: dict[str, bytes] = {}
        self.access = "unknown"
        self.seen: set[int] = set()
        self.dismissed: set[int] = set()  # apagadas aqui: uma leitura em andamento não traz de volta
        self._busy = False
        self._asked = False
        self.timer = QTimer(self, interval=POLL_MS)
        self.timer.timeout.connect(self.refresh)

    @property
    def unread(self) -> int:
        return sum(1 for n in self.items if n.id not in self.seen)

    @property
    def active(self) -> bool:
        return self.timer.isActive()

    def start(self) -> None:
        if not self.timer.isActive():
            self.timer.start()
        self.refresh()

    def stop(self) -> None:
        self.timer.stop()
        if self.items:
            self.items = []
            self.changed.emit()

    def set_fast(self, fast: bool) -> None:
        self.timer.setInterval(POLL_FAST_MS if fast else POLL_MS)
        if fast:
            self.refresh()

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        ask = not self._asked
        self._asked = True
        self._run(
            lambda: self._reader(set(self.logos), ask),
            done=self._apply,
            fail=self._failed,
            owner=self,
        )

    def _failed(self, _error: BaseException) -> None:
        self._busy = False

    def _apply(self, snap: Snapshot) -> None:
        self._busy = False
        self.logos.update(snap.logos)
        fresh = {n.id for n in snap.items}
        self.dismissed &= fresh  # o Windows já apagou: não precisa mais lembrar
        items = sorted(
            (n for n in snap.items if n.id not in self.dismissed), key=lambda n: (n.at, n.id), reverse=True
        )
        ids = {n.id for n in items}
        self.seen &= ids
        if items != self.items or snap.access != self.access:
            self.items, self.access = items, snap.access
            self.changed.emit()

    def logo(self, notice: WinNotice) -> bytes:
        return self.logos.get(notice.aumid or notice.app, b"")

    def mark_all_seen(self) -> None:
        before = self.unread
        self.seen = {n.id for n in self.items}
        if before:
            self.changed.emit()

    def dismiss(self, ids: list[int]) -> None:
        drop = set(ids)
        if not drop:
            return
        self.dismissed |= drop
        self.items = [n for n in self.items if n.id not in drop]
        self.changed.emit()
        self._run(lambda: self._remover(list(drop)), owner=self)

    def dismiss_app(self, app: str) -> None:
        self.dismiss([n.id for n in self.items if n.app == app])

    def clear(self) -> None:
        self.dismiss([n.id for n in self.items])

    def open(self, notice: WinNotice) -> bool:
        ok = open_app(notice.aumid)
        self.dismiss([notice.id])  # como no Windows: abriu, sai da central
        return ok


def privacy_settings() -> None:
    """Abre Configurações > Privacidade > Notificações (onde se libera o acesso)."""
    if sys.platform == "win32":
        os.startfile("ms-settings:privacy-notifications")  # type: ignore[attr-defined]


__all__ = ["Snapshot", "WinNotice", "WindowsNotifications", "privacy_settings", "read_snapshot"]
