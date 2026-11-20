"""Um único SQLite para tudo: ajustes, atalhos, agenda, tarefas, cache e segredos."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any

from ventude.core import secrets
from ventude.core.config import Settings

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS shortcuts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    target TEXT NOT NULL UNIQUE,
    position INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, day TEXT NOT NULL, start TEXT, end TEXT, notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS list_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, blob BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS usage_samples (
    provider TEXT NOT NULL, key TEXT NOT NULL, ts REAL NOT NULL, used REAL NOT NULL);
CREATE INDEX IF NOT EXISTS usage_samples_idx ON usage_samples(provider, key, ts);
CREATE TABLE IF NOT EXISTS shelf_pins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL, value TEXT NOT NULL UNIQUE, name TEXT NOT NULL, added REAL NOT NULL);
"""


LEGACY_FILES = ("calendar.db", "lists.db", "cache.db", "assistant.db", "credentials.bin")


@dataclass(frozen=True)
class Shortcut:
    id: int
    kind: str  # app | folder | file | url
    label: str
    target: str
    position: int


@dataclass(frozen=True)
class Event:
    id: int
    title: str
    day: date
    start: dtime | None
    end: dtime | None
    notes: str = ""

    @property
    def when(self) -> str:
        if self.start and self.end:
            return f"{self.start:%H:%M}–{self.end:%H:%M}"
        if self.start:
            return f"{self.start:%H:%M}"
        return "Dia todo"


@dataclass(frozen=True)
class TaskList:
    id: int
    name: str


@dataclass(frozen=True)
class TaskItem:
    id: int
    list_id: int
    text: str
    done: bool
    position: int


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._db.commit()
        self._migrate(path.parent)
        if not self.lists():
            self.add_list("Geral")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _exec(self, sql: str, args: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock, self._db:
            return self._db.execute(sql, args)

    def _all(self, sql: str, args: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # ---------- migração da versão anterior ----------
    def _migrate(self, folder: Path) -> None:
        if self._meta("migrated_v3"):
            return
        try:
            tables = {r[0] for r in self._all("SELECT name FROM sqlite_master WHERE type='table'")}
            if "pinned_items" in tables:
                for kind, label, path in self._all(
                    "SELECT kind, label, path FROM pinned_items ORDER BY kind, position, id"
                ):
                    self.add_shortcut(kind, label, path)
            old_calendar = folder / "calendar.db"
            if old_calendar.exists():
                with closing(sqlite3.connect(old_calendar)) as old:
                    for row in old.execute("SELECT title, day, start, end, notes FROM events"):
                        self._exec(
                            "INSERT INTO events(title, day, start, end, notes) VALUES(?,?,?,?,?)",
                            tuple(row),
                        )
            old_lists = folder / "lists.db"
            if old_lists.exists():
                with closing(sqlite3.connect(old_lists)) as old:
                    for list_id, name in old.execute("SELECT id, name FROM lists ORDER BY id"):
                        new = self.add_list(name)
                        for text, done, position in old.execute(
                            "SELECT text, done, position FROM list_items WHERE list_id=? "
                            "ORDER BY position, id",
                            (list_id,),
                        ):
                            self._exec(
                                "INSERT INTO list_items(list_id, text, done, position) VALUES(?,?,?,?)",
                                (new.id, text, int(done), position),
                            )
        except sqlite3.Error:
            log.exception("Migração dos dados antigos falhou; seguindo com dados novos")
            return
        self._set_meta("migrated_v3", "1")
        self._archive_legacy(folder)

    @staticmethod
    def _archive_legacy(folder: Path) -> None:
        """Guarda os arquivos da versão antiga (inclusive a IA removida) em 'legado'."""
        legacy = [folder / n for n in LEGACY_FILES if (folder / n).exists()]
        if not legacy:
            return
        target = folder / "legado"
        try:
            target.mkdir(exist_ok=True)
            for path in legacy:
                path.replace(target / path.name)
        except OSError:
            log.warning("Não foi possível arquivar os dados antigos", exc_info=True)

    def _meta(self, key: str) -> str | None:
        rows = self._all("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0][0] if rows else None

    def _set_meta(self, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO meta(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # ---------- ajustes ----------
    def load_settings(self) -> Settings:
        data: dict[str, Any] = {}
        for key, value in self._all("SELECT key, value FROM settings"):
            try:
                data[key] = json.loads(value)
            except json.JSONDecodeError:
                continue
        return Settings.from_dict(data)

    def save_settings(self, settings: Settings) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(k, json.dumps(v)) for k, v in settings.to_dict().items()],
            )

    # ---------- cache ----------
    def cache_get(self, key: str, *, allow_expired: bool = False) -> Any | None:
        rows = self._all("SELECT value, expires FROM cache WHERE key=?", (key,))
        if not rows:
            return None
        value, expires = rows[0]
        if expires < time.time() and not allow_expired:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    def cache_set(self, key: str, value: Any, ttl: float) -> None:
        self._exec(
            "INSERT INTO cache(key, value, expires) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires=excluded.expires",
            (key, json.dumps(value, ensure_ascii=False), time.time() + ttl),
        )

    def cache_clear(self, prefix: str = "") -> None:
        self._exec("DELETE FROM cache WHERE key LIKE ?", (prefix + "%",))

    # ---------- histórico de uso das IAs ----------
    def add_sample(self, provider: str, key: str, ts: float, used: float) -> None:
        self._exec(
            "INSERT INTO usage_samples(provider, key, ts, used) VALUES(?,?,?,?)", (provider, key, ts, used)
        )

    def samples(self, provider: str, key: str, since: float) -> list[tuple[float, float]]:
        return [
            (float(ts), float(used))
            for ts, used in self._all(
                "SELECT ts, used FROM usage_samples WHERE provider=? AND key=? AND ts>=? ORDER BY ts",
                (provider, key, since),
            )
        ]

    def prune_samples(self, before: float) -> None:
        self._exec("DELETE FROM usage_samples WHERE ts<?", (before,))

    # ---------- itens fixados na Estante ----------
    def shelf_pins(self) -> list[tuple[str, str, str, float]]:
        """(tipo, valor, nome, quando entrou), do mais novo para o mais antigo."""
        return [
            (str(k), str(v), str(n), float(a))
            for k, v, n, a in self._all("SELECT kind, value, name, added FROM shelf_pins ORDER BY added DESC")
        ]

    def add_shelf_pin(self, kind: str, value: str, name: str, added: float) -> None:
        self._exec(
            "INSERT INTO shelf_pins(kind, value, name, added) VALUES(?,?,?,?) "
            "ON CONFLICT(value) DO UPDATE SET kind=excluded.kind, name=excluded.name",
            (kind, value, name, added),
        )

    def remove_shelf_pin(self, value: str) -> None:
        self._exec("DELETE FROM shelf_pins WHERE value=?", (value,))

    # ---------- segredos ----------
    def secret(self, name: str) -> str:
        rows = self._all("SELECT blob FROM secrets WHERE name=?", (name,))
        if not rows:
            return ""
        try:
            return secrets.unprotect(bytes(rows[0][0]))
        except (ValueError, OSError):
            log.warning("Segredo %s ilegível", name)
            return ""

    def set_secret(self, name: str, value: str) -> None:
        value = value.strip()
        if not value:
            self._exec("DELETE FROM secrets WHERE name=?", (name,))
            return
        self._exec(
            "INSERT INTO secrets(name, blob) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET blob=excluded.blob",
            (name, secrets.protect(value)),
        )

    # ---------- atalhos ----------
    def shortcuts(self) -> list[Shortcut]:
        return [
            Shortcut(*r)
            for r in self._all(
                "SELECT id, kind, label, target, position FROM shortcuts ORDER BY position, id"
            )
        ]

    def add_shortcut(self, kind: str, label: str, target: str) -> Shortcut | None:
        pos = self._all("SELECT COALESCE(MAX(position), -1) + 1 FROM shortcuts")[0][0]
        try:
            cur = self._exec(
                "INSERT INTO shortcuts(kind, label, target, position) VALUES(?,?,?,?)",
                (kind, label.strip() or target, target, pos),
            )
        except sqlite3.IntegrityError:
            return None
        return Shortcut(int(cur.lastrowid or 0), kind, label, target, pos)

    def rename_shortcut(self, shortcut_id: int, label: str) -> None:
        if label.strip():
            self._exec("UPDATE shortcuts SET label=? WHERE id=?", (label.strip(), shortcut_id))

    def remove_shortcut(self, shortcut_id: int) -> None:
        self._exec("DELETE FROM shortcuts WHERE id=?", (shortcut_id,))

    def reorder_shortcuts(self, ids: list[int]) -> None:
        with self._lock, self._db:
            self._db.executemany("UPDATE shortcuts SET position=? WHERE id=?", list(enumerate(ids)))

    # ---------- agenda ----------
    def add_event(self, title: str, day: date, start: dtime | None = None, end: dtime | None = None) -> Event:
        cur = self._exec(
            "INSERT INTO events(title, day, start, end) VALUES(?,?,?,?)",
            (
                title.strip(),
                day.isoformat(),
                start.isoformat(timespec="minutes") if start else None,
                end.isoformat(timespec="minutes") if end else None,
            ),
        )
        return Event(int(cur.lastrowid or 0), title.strip(), day, start, end)

    def remove_event(self, event_id: int) -> None:
        self._exec("DELETE FROM events WHERE id=?", (event_id,))

    def events_on(self, day: date) -> list[Event]:
        return self._events("WHERE day=?", (day.isoformat(),))

    def upcoming_events(self, start: date | None = None, days: int = 14) -> list[Event]:
        first = start or date.today()
        return self._events(
            "WHERE day>=? AND day<?", (first.isoformat(), (first + timedelta(days)).isoformat())
        )

    def next_event(self, now: datetime | None = None) -> Event | None:
        now = now or datetime.now()
        for event in self.upcoming_events(now.date(), 30):
            if event.day > now.date():
                return event
            limit = event.end or event.start
            if limit is None or limit >= now.time().replace(second=0, microsecond=0):
                return event
        return None

    def days_with_events(self, year: int, month: int) -> set[int]:
        rows = self._all("SELECT DISTINCT day FROM events WHERE day LIKE ?", (f"{year:04d}-{month:02d}-%",))
        return {int(r[0][8:10]) for r in rows}

    def _events(self, where: str, args: tuple[Any, ...]) -> list[Event]:
        rows = self._all(
            f"SELECT id, title, day, start, end, notes FROM events {where} "
            "ORDER BY day, start IS NOT NULL, start",
            args,
        )
        return [
            Event(
                r[0],
                r[1],
                date.fromisoformat(r[2]),
                dtime.fromisoformat(r[3]) if r[3] else None,
                dtime.fromisoformat(r[4]) if r[4] else None,
                r[5] or "",
            )
            for r in rows
        ]

    # ---------- tarefas ----------
    def lists(self) -> list[TaskList]:
        return [TaskList(*r) for r in self._all("SELECT id, name FROM lists ORDER BY id")]

    def add_list(self, name: str) -> TaskList:
        name = name.strip() or "Nova lista"
        base, n = name, 2
        while self._all("SELECT 1 FROM lists WHERE name=?", (name,)):
            name, n = f"{base} {n}", n + 1
        cur = self._exec("INSERT INTO lists(name) VALUES(?)", (name,))
        return TaskList(int(cur.lastrowid or 0), name)

    def rename_list(self, list_id: int, name: str) -> bool:
        try:
            self._exec("UPDATE lists SET name=? WHERE id=?", (name.strip(), list_id))
        except sqlite3.IntegrityError:
            return False
        return True

    def remove_list(self, list_id: int) -> None:
        self._exec("DELETE FROM list_items WHERE list_id=?", (list_id,))
        self._exec("DELETE FROM lists WHERE id=?", (list_id,))
        if not self.lists():
            self.add_list("Geral")

    def items(self, list_id: int) -> list[TaskItem]:
        rows = self._all(
            "SELECT id, list_id, text, done, position FROM list_items WHERE list_id=? "
            "ORDER BY done, position, id",
            (list_id,),
        )
        return [TaskItem(r[0], r[1], r[2], bool(r[3]), r[4]) for r in rows]

    def add_item(self, list_id: int, text: str) -> TaskItem:
        pos = self._all("SELECT COALESCE(MAX(position), -1) + 1 FROM list_items WHERE list_id=?", (list_id,))[
            0
        ][0]
        cur = self._exec(
            "INSERT INTO list_items(list_id, text, position) VALUES(?,?,?)",
            (list_id, text.strip(), pos),
        )
        return TaskItem(int(cur.lastrowid or 0), list_id, text.strip(), False, pos)

    def set_done(self, item_id: int, done: bool) -> None:
        self._exec("UPDATE list_items SET done=? WHERE id=?", (int(done), item_id))

    def edit_item(self, item_id: int, text: str) -> None:
        if text.strip():
            self._exec("UPDATE list_items SET text=? WHERE id=?", (text.strip(), item_id))

    def remove_item(self, item_id: int) -> None:
        self._exec("DELETE FROM list_items WHERE id=?", (item_id,))

    def clear_done(self, list_id: int) -> int:
        cur = self._exec("DELETE FROM list_items WHERE list_id=? AND done=1", (list_id,))
        return cur.rowcount

    def pending_count(self, list_id: int) -> int:
        return int(self._all("SELECT COUNT(*) FROM list_items WHERE list_id=? AND done=0", (list_id,))[0][0])
