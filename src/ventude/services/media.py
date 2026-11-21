"""Mídia do Windows (GlobalSystemMediaTransportControls), sem polling.

O código WinRT roda num loop asyncio em thread própria; os resultados chegam à
interface por sinais Qt. Fora do Windows o serviço simplesmente fica vazio.

Por padrão só o Spotify é mostrado e controlado (vídeos e abas do navegador ficam
de fora). Em Ajustes dá para voltar a seguir qualquer player.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

_STATUS = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}
TICKS_PER_SECOND = 10_000_000


@dataclass(frozen=True)
class MediaState:
    title: str
    artist: str
    album: str
    status: str
    position_s: float
    duration_s: float
    updated_at: float  # time.monotonic() em que position_s valia
    can_play_pause: bool
    can_next: bool
    can_previous: bool
    can_seek: bool
    source_app: str = ""

    @property
    def is_playing(self) -> bool:
        return self.status == "playing"

    def position_at(self, now: float | None = None) -> float:
        pos = self.position_s
        if self.is_playing:
            pos += max(0.0, (time.monotonic() if now is None else now) - self.updated_at)
        if self.duration_s > 0:
            pos = min(pos, self.duration_s)
        return max(0.0, pos)


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _seconds(value: Any) -> float:
    if isinstance(value, timedelta):
        return value.total_seconds()
    try:
        return float(value) / TICKS_PER_SECOND
    except (TypeError, ValueError):
        return 0.0


def build_state(props: Any, playback: Any, timeline: Any, source_app: str = "") -> MediaState | None:
    title = (getattr(props, "title", "") or "").strip()
    if not title:
        return None
    status = _STATUS.get(int(getattr(playback, "playback_status", 0)), "closed")
    controls = getattr(playback, "controls", None)
    start = _seconds(getattr(timeline, "start_time", 0))
    end = _seconds(getattr(timeline, "end_time", 0))
    pos = _seconds(getattr(timeline, "position", 0)) - start
    duration = max(0.0, end - start)
    last = getattr(timeline, "last_updated_time", None)
    if status == "playing" and isinstance(last, datetime) and duration > 0:
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        pos += max(0.0, (datetime.now(UTC) - last).total_seconds())
    if duration > 0:
        pos = min(pos, duration)
    return MediaState(
        title=title,
        artist=(getattr(props, "artist", "") or "").strip(),
        album=(getattr(props, "album_title", "") or "").strip(),
        status=status,
        position_s=max(0.0, pos),
        duration_s=duration,
        updated_at=time.monotonic(),
        can_play_pause=bool(getattr(controls, "is_play_pause_toggle_enabled", False)),
        can_next=bool(getattr(controls, "is_next_enabled", False)),
        can_previous=bool(getattr(controls, "is_previous_enabled", False)),
        can_seek=bool(getattr(controls, "is_playback_position_enabled", False)) and duration > 0,
        source_app=source_app,
    )


async def read_thumbnail(ref: Any) -> bytes:  # pragma: no cover - só Windows
    if ref is None:
        return b""
    from winrt.windows.storage.streams import Buffer, InputStreamOptions

    stream = await ref.open_read_async()
    size = int(stream.size)
    if size <= 0:
        return b""
    buf = await stream.read_async(Buffer(size), size, InputStreamOptions.READ_AHEAD)
    return bytes(buf)


async def default_manager() -> Any:  # pragma: no cover - só Windows
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager,
    )

    return await Manager.request_async()


SOURCES = {"spotify": "spotify", "any": ""}


def app_id(session: Any) -> str:
    return str(getattr(session, "source_app_user_model_id", "") or "")


def matches(session: Any, source: str) -> bool:
    """O player da sessão é o escolhido? (Spotify da loja ou do site: AUMID com 'spotify')."""
    needle = SOURCES.get(source, "")
    return session is not None and (not needle or needle in app_id(session).lower())


class MediaService(QObject):
    state_changed = Signal(object)  # MediaState | None
    thumbnail_changed = Signal(bytes)

    def __init__(
        self,
        manager_factory: Callable[[], Awaitable[Any]] = default_manager,
        thumbnail_reader: Callable[[Any], Awaitable[bytes]] = read_thumbnail,
        source: str = "spotify",
    ) -> None:
        super().__init__()
        self._manager_factory = manager_factory
        self._thumbnail_reader = thumbnail_reader
        self._source = source if source in SOURCES else "spotify"
        self._sessions_token: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._manager: Any = None
        self._session: Any = None
        self._tokens: list[tuple[Callable[[Any], None], Any]] = []
        self._manager_token: Any = None
        self._pending: asyncio.Future[None] | None = None
        self._track_key: tuple[str, str, str] | None = None
        self.state: MediaState | None = None
        self.thumbnail = b""

    # ---------- ciclo de vida ----------
    def start(self) -> None:
        if self._thread:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="ventude-media", daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        assert self._loop
        asyncio.set_event_loop(self._loop)
        self._loop.create_task(self.connect())
        self._loop.run_forever()
        self._loop.close()

    def stop(self) -> None:
        loop, thread = self._loop, self._thread
        if not loop or not thread:
            return
        future = asyncio.run_coroutine_threadsafe(self._detach_all(), loop)
        try:
            future.result(timeout=0.5)
        except Exception:  # noqa: BLE001
            pass
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        self._thread = self._loop = None

    async def _detach_all(self) -> None:
        self._detach_session()
        if self._manager is None:
            return
        for remove, token in (
            ("remove_current_session_changed", self._manager_token),
            ("remove_sessions_changed", self._sessions_token),
        ):
            if token is None:
                continue
            try:
                getattr(self._manager, remove)(token)
            except Exception:  # noqa: BLE001
                log.debug("%s falhou", remove, exc_info=True)

    @property
    def source(self) -> str:
        return self._source

    def set_source(self, source: str) -> None:
        """Troca entre "spotify" e "any" (qualquer player) sem reiniciar nada."""
        source = source if source in SOURCES else "spotify"
        if source == self._source:
            return
        self._source = source
        self._from_winrt("session")

    # ---------- eventos ----------
    async def connect(self) -> None:
        try:
            self._manager = await self._manager_factory()
        except Exception:  # noqa: BLE001
            log.info("Controle de mídia indisponível neste sistema")
            self._emit_state(None)
            return
        self._manager_token = self._manager.add_current_session_changed(
            lambda *_: self._from_winrt("session")
        )
        try:  # o Spotify pode não ser a sessão "atual": acompanha a lista inteira
            self._sessions_token = self._manager.add_sessions_changed(lambda *_: self._from_winrt("session"))
        except Exception:  # noqa: BLE001
            log.debug("add_sessions_changed indisponível", exc_info=True)
        await self.refresh("session")

    def _pick_session(self) -> Any:
        if self._manager is None:
            return None
        current = self._manager.get_current_session()
        if matches(current, self._source):
            return current
        if not SOURCES.get(self._source):
            return current
        try:
            sessions = list(self._manager.get_sessions())
        except Exception:  # noqa: BLE001
            log.debug("get_sessions falhou", exc_info=True)
            return None
        return next((session for session in sessions if matches(session, self._source)), None)

    def _same(self, session: Any) -> bool:
        if session is self._session:
            return True
        # O WinRT devolve um objeto Python novo a cada consulta: compara pelo app.
        return (
            session is not None
            and self._session is not None
            and bool(SOURCES.get(self._source))
            and app_id(session) == app_id(self._session)
        )

    def _from_winrt(self, kind: str) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._schedule, kind)

    def _schedule(self, kind: str) -> None:
        if self._pending and not self._pending.done():
            if kind in ("session", "media"):
                self._pending.cancel()
            else:
                return
        self._pending = asyncio.ensure_future(self._debounced(kind))

    async def _debounced(self, kind: str) -> None:
        await asyncio.sleep(0.05)
        await self.refresh(kind)

    def _detach_session(self) -> None:
        for remove, token in self._tokens:
            try:
                remove(token)
            except Exception:  # noqa: BLE001
                log.debug("remoção de handler falhou", exc_info=True)
        self._tokens.clear()
        self._session = None

    def _attach_session(self, session: Any) -> None:
        self._session = session
        for event, kind in (
            ("media_properties_changed", "media"),
            ("playback_info_changed", "playback"),
            ("timeline_properties_changed", "timeline"),
        ):
            token = getattr(session, f"add_{event}")(lambda *_a, k=kind: self._from_winrt(k))
            self._tokens.append((getattr(session, f"remove_{event}"), token))

    async def refresh(self, kind: str = "session") -> None:
        try:
            if kind == "session":
                current = self._pick_session()
                if not self._same(current):
                    self._detach_session()
                    self._track_key = None
                    if current is not None:
                        self._attach_session(current)
            session = self._session
            if session is None:
                self._emit_state(None)
                if self._track_key is None and self.thumbnail:
                    self.thumbnail = b""
                    self.thumbnail_changed.emit(b"")
                return
            props = await session.try_get_media_properties_async()
            state = build_state(
                props,
                session.get_playback_info(),
                session.get_timeline_properties(),
                getattr(session, "source_app_user_model_id", "") or "",
            )
            self._emit_state(state)
            key = (state.title, state.artist, state.album) if state else None
            if key != self._track_key:
                self._track_key = key
                data = b""
                if state is not None:
                    try:
                        data = await self._thumbnail_reader(getattr(props, "thumbnail", None))
                    except Exception:  # noqa: BLE001
                        log.debug("capa indisponível", exc_info=True)
                self.thumbnail = data
                self.thumbnail_changed.emit(data)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Falha ao ler sessão de mídia")

    def _emit_state(self, state: MediaState | None) -> None:
        self.state = state
        self.state_changed.emit(state)

    # ---------- comandos ----------
    def _submit(self, make: Callable[[Any], Awaitable[Any]]) -> None:
        session, loop = self._session, self._loop
        if session is None or loop is None:
            return

        async def run() -> None:
            try:
                await make(session)
            except Exception:  # noqa: BLE001
                log.exception("Comando de mídia falhou")

        asyncio.run_coroutine_threadsafe(run(), loop)

    def play_pause(self) -> None:
        if self.state and self.state.can_play_pause:
            self._submit(lambda s: s.try_toggle_play_pause_async())

    def next(self) -> None:
        if self.state and self.state.can_next:
            self._submit(lambda s: s.try_skip_next_async())

    def previous(self) -> None:
        if self.state and self.state.can_previous:
            self._submit(lambda s: s.try_skip_previous_async())

    def seek(self, seconds: float) -> None:
        st = self.state
        if not st or not st.can_seek:
            return
        seconds = max(0.0, min(seconds, st.duration_s))
        self.state = replace(st, position_s=seconds, updated_at=time.monotonic())
        self._submit(lambda s: s.try_change_playback_position_async(int(seconds * TICKS_PER_SECOND)))
