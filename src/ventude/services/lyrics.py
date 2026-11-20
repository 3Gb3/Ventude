"""Letra sincronizada da música tocando, pelo lrclib.net (base aberta e gratuita, sem chave).

Busca em segundo plano quando a faixa troca e guarda no cache do SQLite (30 dias; "sem letra",
1 dia). O verso atual sai de `MediaState.position_at()`.
"""

from __future__ import annotations

import bisect
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from ventude.core import net

log = logging.getLogger(__name__)

API = "https://lrclib.net/api"
FOUND_TTL = 30 * 86400
MISSING_TTL = 86400
LRC_LINE = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")


@dataclass(frozen=True)
class Lyrics:
    lines: tuple[tuple[float, str], ...] = ()  # (segundo, verso), em ordem
    plain: str = ""
    instrumental: bool = False

    @property
    def synced(self) -> bool:
        return bool(self.lines)

    def at(self, position: float) -> tuple[str, str]:
        """(verso atual, próximo verso) no segundo `position`."""
        if not self.lines:
            return "", ""
        times = [t for t, _ in self.lines]
        index = bisect.bisect_right(times, position + 0.15) - 1
        if index < 0:
            return "", self.lines[0][1]
        current = self.lines[index][1]
        upcoming = self.lines[index + 1][1] if index + 1 < len(self.lines) else ""
        return current, upcoming


def parse_lrc(text: str) -> tuple[tuple[float, str], ...]:
    """'[01:02.50] verso' -> ((62.5, 'verso'), ...). Aceita vários tempos na mesma linha."""
    out: list[tuple[float, str]] = []
    for raw in (text or "").splitlines():
        stamps = list(LRC_LINE.finditer(raw))
        if not stamps:
            continue
        verse = raw[stamps[-1].end() :].strip()
        for stamp in stamps:
            minutes, seconds = int(stamp.group(1)), float(stamp.group(2).replace(":", "."))
            out.append((minutes * 60 + seconds, verse))
    out.sort(key=lambda item: item[0])
    return tuple(out)


def from_json(data: dict | None) -> Lyrics | None:
    if not isinstance(data, dict):
        return None
    lines = parse_lrc(str(data.get("syncedLyrics") or ""))
    plain = str(data.get("plainLyrics") or "")
    instrumental = bool(data.get("instrumental"))
    if not lines and not plain and not instrumental:
        return None
    return Lyrics(lines, plain, instrumental)


def cache_key(title: str, artist: str) -> str:
    return f"lyrics:{artist.strip().lower()}|{title.strip().lower()}"


def fetch(title: str, artist: str, album: str = "", duration: float = 0.0) -> Lyrics | None:
    """Letra da faixa (None se o lrclib não tiver). Rede: chame fora da thread da interface."""
    params = {"track_name": title, "artist_name": artist}
    if album:
        params["album_name"] = album
    if duration > 0:
        params["duration"] = str(round(duration))
        try:
            found = from_json(net.get_json(f"{API}/get", params=params, timeout=8))
            if found:
                return found
        except net.NetError as exc:
            if exc.status not in (0, 404):
                raise
    try:
        results = net.get_json(
            f"{API}/search", params={"track_name": title, "artist_name": artist}, timeout=8
        )
    except net.NetError as exc:
        if exc.status == 404:
            return None
        raise
    if not isinstance(results, list):
        return None
    candidates = [r for r in results if isinstance(r, dict)]
    if duration > 0:  # prefere a versão com a mesma duração (ao vivo, remix...)
        candidates.sort(key=lambda r: abs(float(r.get("duration") or 0) - duration))
    synced = next((r for r in candidates if r.get("syncedLyrics")), None)
    return from_json(synced or (candidates[0] if candidates else None))


class LyricsService(QObject):
    ready = Signal(str, object)  # chave da faixa, Lyrics | None

    def __init__(
        self, store: object, run: Callable[..., None], fetcher: Callable[..., Lyrics | None] = fetch
    ) -> None:
        super().__init__()
        self.store, self._run, self._fetch = store, run, fetcher
        self._pending: set[str] = set()
        self.online = False  # só busca na internet depois de start() (o app chama; os testes não)

    def start(self) -> None:
        self.online = True

    def lookup(self, title: str, artist: str, album: str = "", duration: float = 0.0) -> Lyrics | None | bool:
        """Letra do cache na hora; sem cache, busca e avisa por `ready`. Devolve False enquanto busca."""
        key = cache_key(title, artist)
        cached = self.store.cache_get(key)  # type: ignore[attr-defined]
        if cached is not None:
            return from_json(cached) if cached else None
        if not self.online or not title:
            return None
        if key not in self._pending:
            self._pending.add(key)
            self._run(
                self._fetch,
                title,
                artist,
                album,
                duration,
                done=lambda found, k=key: self._done(k, found),
                fail=lambda error, k=key: self._failed(k, error),
                owner=self,
            )
        return False

    def _done(self, key: str, found: Lyrics | None) -> None:
        self._pending.discard(key)
        if found is None:
            self.store.cache_set(key, {}, MISSING_TTL)  # type: ignore[attr-defined]
        else:
            data = {
                "syncedLyrics": "\n".join(f"[{int(t // 60):02d}:{t % 60:05.2f}] {v}" for t, v in found.lines),
                "plainLyrics": found.plain,
                "instrumental": found.instrumental,
            }
            self.store.cache_set(key, data, FOUND_TTL)  # type: ignore[attr-defined]
        self.ready.emit(key, found)

    def _failed(self, key: str, error: BaseException) -> None:
        self._pending.discard(key)
        log.debug("Letra indisponível: %r", error)
        self.ready.emit(key, None)
