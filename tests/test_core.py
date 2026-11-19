import http.server
import json
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime

import pytest

from ventude.core import net, secrets, timefmt
from ventude.core.config import DEFAULT_MODULES, Settings
from ventude.core.store import Store


# ---------- ajustes ----------
def test_settings_from_old_version_resets_modules_and_keeps_known_values():
    old = {"monitor_index": 2, "clock_24h": False, "compact_width": 216, "layout_version": 2, "modules": "x"}
    s = Settings.from_dict(old)
    assert s.monitor_index == 2 and s.clock_24h is False
    assert s.modules == DEFAULT_MODULES
    assert s.layout_version == 3


def test_settings_clamps_and_validates():
    s = Settings.from_dict(
        {"layout_version": 3, "collapse_delay_ms": 99999, "limits_interval_min": 1, "claude_mode": "x"}
    )
    assert s.collapse_delay_ms == 10_000
    assert s.limits_interval_min == 2
    assert s.claude_mode == "auto"
    assert s.copy(grok_mode="off").provider_mode("grok") == "off"
    assert s.module_enabled("home") and s.module_enabled("settings")


def test_settings_bool_from_string():
    assert Settings.from_dict({"layout_version": 3, "pill_rings": "false"}).pill_rings is False


# ---------- store ----------
def test_store_settings_roundtrip(store):
    store.save_settings(Settings(monitor_index=1, market_symbols="AAPL:Apple"))
    loaded = store.load_settings()
    assert loaded.monitor_index == 1 and loaded.market_symbols == "AAPL:Apple"


def test_shortcuts_crud(store):
    a = store.add_shortcut("url", "GitHub", "https://github.com")
    b = store.add_shortcut("folder", "Tmp", "/tmp")
    assert store.add_shortcut("url", "Dup", "https://github.com") is None
    store.rename_shortcut(a.id, "Git")
    store.reorder_shortcuts([b.id, a.id])
    items = store.shortcuts()
    assert [s.label for s in items] == ["Tmp", "Git"]
    store.remove_shortcut(b.id)
    assert len(store.shortcuts()) == 1


def test_events_and_next_event(store):
    today = date.today()
    store.add_event("Passado", today, dtime(0, 0), dtime(0, 1))
    store.add_event("Amanhã", today + timedelta(days=1), dtime(9, 0))
    store.add_event("Dia todo", today)
    now = datetime.combine(today, dtime(12, 0))
    assert store.next_event(now).title == "Dia todo"
    assert store.days_with_events(today.year, today.month) >= {today.day}
    assert [e.when for e in store.events_on(today)] == ["Dia todo", "00:00–00:01"]


def test_lists_and_items(store):
    lst = store.lists()[0]
    assert lst.name == "Geral"
    item = store.add_item(lst.id, "  café  ")
    store.add_item(lst.id, "pão")
    store.set_done(item.id, True)
    assert store.pending_count(lst.id) == 1
    assert [i.text for i in store.items(lst.id)] == ["pão", "café"]
    assert store.clear_done(lst.id) == 1
    second = store.add_list("Geral")
    assert second.name == "Geral 2"
    assert not store.rename_list(second.id, "Geral")
    store.remove_list(lst.id)
    store.remove_list(second.id)
    assert [x.name for x in store.lists()] == ["Geral"]


def test_cache_expiry(store):
    store.cache_set("k", {"a": 1}, -1)
    assert store.cache_get("k") is None
    assert store.cache_get("k", allow_expired=True) == {"a": 1}


def test_secrets_roundtrip(store):
    store.set_secret("token", "abc")
    assert store.secret("token") == "abc"
    store.set_secret("token", "")
    assert store.secret("token") == ""
    assert secrets.unprotect(secrets.protect("olá")) == "olá"


def test_migrates_previous_version(tmp_home):
    folder = tmp_home / "data"
    with sqlite3.connect(folder / "ventude.db") as old:
        old.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE pinned_items (id INTEGER PRIMARY KEY, kind TEXT, label TEXT, path TEXT,
                position INTEGER);
            INSERT INTO settings VALUES ('clock_24h', 'false');
            INSERT INTO pinned_items VALUES (1, 'folder', 'Docs', '/tmp', 0);
            """
        )
    with sqlite3.connect(folder / "calendar.db") as old:
        old.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, title, day, start, end, notes)")
        old.execute("INSERT INTO events VALUES (1, 'Consulta', '2030-01-02', '10:00', NULL, '')")
    with sqlite3.connect(folder / "lists.db") as old:
        old.executescript(
            """
            CREATE TABLE lists (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE list_items (id INTEGER PRIMARY KEY, list_id, text, done, position);
            INSERT INTO lists VALUES (7, 'Mercado');
            INSERT INTO list_items VALUES (1, 7, 'Leite', 1, 0);
            """
        )
    store = Store(folder / "ventude.db")
    try:
        assert store.load_settings().clock_24h is False
        assert [s.label for s in store.shortcuts()] == ["Docs"]
        assert store.events_on(date(2030, 1, 2))[0].title == "Consulta"
        mercado = next(lst for lst in store.lists() if lst.name == "Mercado")
        assert store.items(mercado.id)[0].done is True
    finally:
        store.close()
    assert not (folder / "calendar.db").exists()
    assert (folder / "legado" / "calendar.db").exists() and (folder / "legado" / "lists.db").exists()
    again = Store(folder / "ventude.db")  # migração roda uma vez só
    assert len(again.shortcuts()) == 1
    again.close()


# ---------- datas ----------
def test_parse_instant_formats():
    assert timefmt.parse_instant("2025-11-04T04:59:59.943648+00:00") == pytest.approx(1762232399.943648)
    assert timefmt.parse_instant("2025-11-04T04:59:59.123456789Z") == pytest.approx(1762232399.123456)
    assert timefmt.parse_instant(1762232399000) == 1762232399
    assert timefmt.parse_instant("1762232399") == 1762232399
    assert timefmt.parse_instant({"seconds": "1762232399"}) == 1762232399
    assert timefmt.parse_instant("nope") is None
    assert timefmt.parse_instant(None) is None


def test_durations_and_resets():
    assert timefmt.duration(30) == "menos de 1 min"
    assert timefmt.duration(45 * 60) == "45 min"
    assert timefmt.duration(2 * 3600 + 14 * 60) == "2h 14min"
    assert timefmt.duration(3 * 86400 + 3600) == "3d 1h"
    now = 1_000_000.0
    assert timefmt.resets_text(now - 1, now) == "Renovado"
    assert timefmt.resets_text(now + 3600, now) == "Renova em 1h"
    assert timefmt.resets_text(now + 5 * 86400, now).startswith("Renova ")
    assert timefmt.short_reset(now + 600, now) == "10 min"
    assert timefmt.short_reset(now - 5, now) == "livre"
    assert timefmt.ago(now - 120, now) == "há 2 min"
    assert timefmt.weekday_name(date(2026, 11, 19)) == "Quinta-feira"
    assert timefmt.weekday_name(date(2026, 11, 21)) == "Sábado"
    assert timefmt.clock(datetime(2026, 1, 1, 15, 5), False) == "3:05 PM"


# ---------- rede ----------
class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/ok"):
            body = json.dumps({"ua": self.headers.get("User-Agent"), "path": self.path}).encode()
            self.send_response(200)
        elif self.path == "/bad":
            body = b"not json"
            self.send_response(200)
        else:
            body = b"nope"
            self.send_response(404)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers["Content-Length"])
        data = json.loads(self.rfile.read(length))
        body = json.dumps({"echo": data, "csrf": self.headers.get("X-Test")}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_net_get_post_and_errors(server):
    data = net.get_json(f"{server}/ok", params={"a": "b c"})
    assert data["path"] == "/ok?a=b+c" and data["ua"].startswith("Ventude")
    assert net.post_json(f"{server}/p", {"x": 1}, headers={"X-Test": "t"}) == {"echo": {"x": 1}, "csrf": "t"}
    with pytest.raises(net.NetError) as exc:
        net.get_json(f"{server}/missing")
    assert exc.value.status == 404 and exc.value.body == "nope"
    with pytest.raises(net.NetError):
        net.get_json(f"{server}/bad")
    with pytest.raises(net.NetError) as closed:
        net.get_json("http://127.0.0.1:9/ok", timeout=2)
    assert closed.value.status == 0


def test_tasks_deliver_on_main_thread(qapp):
    from ventude.core.tasks import Tasks

    tasks = Tasks()
    results, errors = [], []
    tasks.run(lambda x: x * 2, 21, done=results.append)
    tasks.run(lambda: 1 / 0, fail=errors.append)
    end = time.monotonic() + 3
    while (not results or not errors) and time.monotonic() < end:
        qapp.processEvents()
    tasks.shutdown()
    assert results == [42]
    assert isinstance(errors[0], ZeroDivisionError)
