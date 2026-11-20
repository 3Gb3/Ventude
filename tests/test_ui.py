import time
from datetime import date
from datetime import time as dtime

import pytest

from ventude.core.config import MODULES
from ventude.core.tasks import Tasks
from ventude.limits.model import Report, Window
from ventude.services.media import MediaService, MediaState
from ventude.ui.pages.agenda import parse_times


def settle(qapp, ms=300):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)


@pytest.fixture
def app_ctx(qapp, store, home):
    from ventude.ui.context import AppContext

    now = time.time()
    fetchers = {
        "claude": lambda c: Report("claude", "ok", [Window("session", "Sessão · 5h", 55, now + 3600)], "Pro"),
        "antigravity": lambda c: Report("antigravity", "setup", message="Abra o Antigravity"),
        "grok": lambda c: Report("grok", "error", message="Cookie recusado"),
    }
    ctx = AppContext(store, Tasks(), media=MediaService(), fetchers=fetchers, home=home)
    store.save_settings(ctx.settings.copy(animation="rapida"))
    ctx.settings = store.load_settings()
    yield ctx
    ctx.tasks.shutdown()


@pytest.fixture
def island(qapp, app_ctx):
    from ventude.ui.island import Island

    widget = Island(app_ctx)
    widget.show()
    yield widget
    widget.hide()
    widget.deleteLater()


def test_every_tab_opens(qapp, island, app_ctx):
    island.expand()
    settle(qapp, 300)
    assert island.expanded and island.panel.isVisible() and not island.pill.isVisible()
    for key, _, _ in MODULES:
        island.panel.select(key)
        assert island.panel.current == key
        assert key in island.panel.pages
    island.collapse()
    island.enterEvent(None)  # o mouse continua onde estava o painel
    settle(qapp, 400)
    assert not island.expanded and island.pill.isVisible()


def test_limits_page_states(qapp, island, app_ctx):
    app_ctx.limits.refresh_all()
    end = time.monotonic() + 3
    while any(app_ctx.limits.busy(p) for p in app_ctx.limits.providers()) and time.monotonic() < end:
        qapp.processEvents()
    island.expand("limits")
    settle(qapp, 200)
    page = island.panel.pages["limits"]
    claude_card = page.cards["claude"]
    assert claude_card.plan.text() == "Pro"
    assert page.cards["antigravity"].setup_btn.isVisibleTo(page)
    assert page.cards["grok"].message.text() == "Cookie recusado"
    assert island.pill.visible_rings() == 3


def test_compact_width_grows_with_music(qapp, island, app_ctx):
    settle(qapp, 300)
    before = island.target_rect(False).width()
    state = MediaState("Song", "Band", "", "playing", 1, 100, time.monotonic(), True, True, True, True)
    app_ctx.media.state = state
    app_ctx.media.state_changed.emit(state)
    assert island.target_rect(False).width() > before
    assert island.pill.title.full_text() == "Song  ·  Band"


def test_disabling_modules_rebuilds_tabs(qapp, island, app_ctx):
    app_ctx.update_settings(modules="limits")
    assert list(island.panel.tabs) == ["home", "limits", "settings"]
    app_ctx.update_settings(pill_rings=False)
    assert island.pill.visible_rings() == 0


def test_tasks_and_agenda_pages(qapp, island, app_ctx, store):
    island.expand("tasks")
    page = island.panel.pages["tasks"]
    page.input.setText("Comprar pão")
    page._add_item()
    assert [i.text for i in store.items(page.current)] == ["Comprar pão"]
    island.panel.select("agenda")
    agenda = island.panel.pages["agenda"]
    agenda.title_in.setText("Reunião")
    agenda.time_in.setText("14h-15:30")
    agenda._add()
    event = store.events_on(date.today())[0]
    assert event.start == dtime(14, 0) and event.end == dtime(15, 30)
    agenda.time_in.setText("25:00")
    agenda.title_in.setText("Inválido")
    agenda._add()
    assert agenda.error.isVisibleTo(agenda)


def test_shortcuts_drop_and_site(qapp, island, app_ctx, store, tmp_path):
    island.expand("shortcuts")
    page = island.panel.pages["shortcuts"]
    page._add_paths([str(tmp_path), "https://example.com/a"])
    assert {s.kind for s in store.shortcuts()} == {"folder", "url"}
    page.url.setText("github.com")
    page._add_site()
    assert any(s.target == "https://github.com" for s in store.shortcuts())
    assert page.grid.count() == 3


def test_parse_times():
    assert parse_times("") == (None, None)
    assert parse_times("9") == (dtime(9, 0), None)
    assert parse_times("14:30 - 16") == (dtime(14, 30), dtime(16, 0))
    with pytest.raises(ValueError):
        parse_times("abc")
    with pytest.raises(ValueError):
        parse_times("24:00")


def _drag_events(paths):
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent

    mime = QMimeData()
    mime.setUrls([QUrl(p) if p.startswith("http") else QUrl.fromLocalFile(p) for p in paths])
    action = Qt.DropAction.CopyAction
    buttons, mods = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    enter = QDragEnterEvent(QPointF(20, 10), action, mime, buttons, mods)
    drop = QDropEvent(QPointF(20, 10), action, mime, buttons, mods)
    return mime, enter, drop


def test_drop_on_closed_island_opens_shortcuts(qapp, island, app_ctx, store, tmp_path):
    settle(qapp, 200)
    assert not island.expanded and island.acceptDrops()
    _mime, enter, drop = _drag_events([str(tmp_path), "https://example.com/x"])
    island.dragEnterEvent(enter)
    assert enter.isAccepted() and island.expanded and island.panel.current == "shortcuts"
    assert island._keep_open()  # não fecha enquanto o arquivo está sendo arrastado
    island.dropEvent(drop)
    assert {s.kind for s in store.shortcuts()} == {"folder", "url"}
    page = island.panel.pages["shortcuts"]
    assert "2 atalhos adicionados" in page.status.text()
    assert not page.grid.acceptDrops()  # a grade não "engole" o arraste: a ilha cuida


def test_drop_ignored_when_shortcuts_tab_is_off(qapp, island, app_ctx, tmp_path):
    app_ctx.update_settings(modules="limits")
    _mime, enter, _drop = _drag_events([str(tmp_path)])
    island.dragEnterEvent(enter)
    assert not enter.isAccepted() and not island.expanded


def test_paused_music_stays_on_the_pill(qapp, island, app_ctx):
    settle(qapp, 200)
    compact = island.target_rect(False).width()
    paused = MediaState("Song", "Band", "", "paused", 1, 100, time.monotonic(), True, True, True, True)
    app_ctx.media.state = paused
    app_ctx.media.state_changed.emit(paused)
    assert island.pill.has_media and not island.pill.playing
    assert island.pill.title.isVisibleTo(island.pill) and not island.pill.date.isVisibleTo(island.pill)
    assert island.target_rect(False).width() > compact
    app_ctx.media.state_changed.emit(None)
    assert not island.pill.has_media and island.pill.date.isVisibleTo(island.pill)


def test_tab_switch_animates_and_settles(qapp, island, app_ctx):
    island.expand()
    settle(qapp, 400)
    island.panel.select("limits")
    assert island.panel._transition is not None
    page = island.panel.pages["limits"]
    assert page.graphicsEffect() is not None
    settle(qapp, 400)
    assert island.panel._transition is None and page.graphicsEffect() is None
    assert page.geometry() == island.panel.stack.contentsRect()


def test_hover_opens_without_delay(qapp, island, app_ctx, monkeypatch):
    from PySide6.QtGui import QCursor

    settle(qapp, 300)
    island._collapsed_at = 0
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: island.geometry().center()))
    island.enterEvent(None)
    assert island.expanded  # na hora, sem esperar timer
    assert island.panel.isVisible()  # o conteúdo já aparece durante a animação
    settle(qapp, 400)
    assert island.panel.geometry() == island.rect() and island.panel.mask().isEmpty()


def test_weather_day_details(qapp, island, app_ctx, store):
    from datetime import timedelta

    from ventude.services.weather import Place

    today = date.today()
    days = [
        {"date": (today + timedelta(days=i)).isoformat(), "code": 61, "max": 25 + i, "min": 15, "rain": 70}
        for i in range(3)
    ]
    hours = [
        {
            "time": f"{days[1]['date']}T{h:02d}:00",
            "temp": 15 + h / 3,
            "humidity": 60 + h,
            "rain": 40,
            "code": 61,
        }
        for h in range(24)
    ]
    cached = {
        "place": "Cidade",
        "temp": 20,
        "feels": 21,
        "humidity": 60,
        "wind": 5,
        "code": 61,
        "is_day": True,
    }
    store.cache_set("wx2:1.000,2.000", {**cached, "days": days, "hours": hours}, 3600)
    app_ctx.update_settings(weather_city_json=Place("Cidade", "BR", 1.0, 2.0).to_json())
    app_ctx.weather_focus = days[1]["date"]  # veio do clique no card do Início
    island.expand("weather")
    settle(qapp, 300)
    page = island.panel.pages["weather"]
    assert page.day == days[1]["date"] and page.detail.isVisibleTo(page) and not page.week.isVisibleTo(page)
    assert "Amanhã" in page.detail.title.full_text() and len(page.detail.chart.hours) == 24
    page.detail._hovered(14)
    assert page.detail.info.full_text().startswith("14:00")
    page._step(1)
    assert page.day == days[2]["date"] and page.detail.chart.hours == []
    page.close_day()
    assert page.week.isVisibleTo(page) and not page.detail.isVisibleTo(page)
    rows = [page.rows.itemAt(i).widget() for i in range(page.rows.count())]
    rows[0].clicked.emit(rows[0].iso)
    assert page.day == days[0]["date"]


def test_provider_table_and_ring_choice(qapp, island, app_ctx):
    app_ctx.limits.refresh_all()
    end = time.monotonic() + 3
    while any(app_ctx.limits.busy(p) for p in app_ctx.limits.providers()) and time.monotonic() < end:
        qapp.processEvents()
    island.expand("settings")
    settle(qapp, 200)
    table = island.panel.pages["settings"].providers
    box = table.limit["claude"]
    assert [box.itemData(i) for i in range(box.count())] == ["", "session"]
    box.setCurrentIndex(1)
    box.activated.emit(1)
    assert app_ctx.settings.pinned_limit("claude") == "session"
    assert island.pill.visible_rings() == 3
    table.ring["grok"].setChecked(False)
    assert not app_ctx.settings.ring_enabled("grok") and island.pill.visible_rings() == 2
    home = island.panel.page("home")
    assert not home.limits.columns["grok"][0].isVisibleTo(home)
    table.active["antigravity"].setChecked(False)
    assert app_ctx.settings.antigravity_mode == "off"
    assert not island.panel.pages["settings"].sections["antigravity"].isVisibleTo(island.panel)
    table.active["antigravity"].setChecked(True)
    assert app_ctx.settings.antigravity_mode == "auto" and app_ctx.settings.ring_enabled("antigravity")


def test_weather_on_the_pill(qapp, island, app_ctx, store):
    from ventude.services.weather import Place

    cached = {
        "place": "Cidade",
        "temp": 23.4,
        "feels": 24,
        "humidity": 60,
        "wind": 5,
        "code": 0,
        "is_day": True,
    }
    store.cache_set("wx2:1.000,2.000", {**cached, "days": [], "hours": []}, 3600)
    settle(qapp, 200)
    narrow = island.target_rect(False).width()
    app_ctx.update_settings(weather_city_json=Place("Cidade", "BR", 1.0, 2.0).to_json())
    settle(qapp, 400)
    assert not island.pill.weather.isHidden() and island.pill.weather_temp.text() == "23°"
    assert island.target_rect(False).width() > narrow
    app_ctx.update_settings(pill_weather=False)
    settle(qapp, 100)
    assert island.pill.weather.isHidden()


def test_weather_located_by_ip_when_no_city(qapp, app_ctx, monkeypatch):
    from ventude.services.weather import Place

    found = Place("Cachoeirinha", "BR", -29.9, -51.1, "RS")
    monkeypatch.setattr(app_ctx.weather, "locate", lambda: found)
    calls = []
    monkeypatch.setattr(app_ctx.weather, "forecast", lambda place, force=False: calls.append(place) or None)
    app_ctx.refresh_weather()
    end = time.monotonic() + 2
    while not app_ctx.settings.weather_city_json and time.monotonic() < end:
        qapp.processEvents()
    assert Place.from_json(app_ctx.settings.weather_city_json) == found
    settle(qapp, 200)
    assert calls and calls[0] == found  # a cidade nova já dispara a busca do clima


def _mouse(kind, pos, button):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    return QMouseEvent(kind, QPointF(pos), QPointF(pos), button, button, Qt.KeyboardModifier.NoModifier)


def test_pill_zones_and_quick_media(qapp, island, app_ctx, monkeypatch):
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    app_ctx.limits.refresh_all()
    settle(qapp, 400)
    pill = island.pill
    center = pill.ring_box.geometry().center()
    assert pill.zone_at(center) == "rings"
    press, release = QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease
    left = Qt.MouseButton.LeftButton
    island.mousePressEvent(_mouse(press, center, left))
    island.mouseReleaseEvent(_mouse(release, center, left))
    assert island.expanded and island.panel.current == "limits"
    island.collapse()
    settle(qapp, 400)
    island.mousePressEvent(_mouse(press, pill.date.geometry().center(), left))
    island.mouseReleaseEvent(_mouse(release, pill.date.geometry().center(), left))
    assert island.panel.current == "agenda"
    island.collapse()
    settle(qapp, 400)

    calls = []
    for name in ("play_pause", "next", "previous"):
        monkeypatch.setattr(app_ctx.media, name, lambda n=name: calls.append(n))
    monkeypatch.setattr("ventude.services.system.volume_step", lambda up, steps=1: calls.append(f"vol{up}"))
    state = MediaState("Song", "Band", "", "playing", 10, 100, time.monotonic(), True, True, True, True)
    app_ctx.media.state_changed.emit(state)
    settle(qapp, 400)
    art = pill.art.geometry().center()
    assert pill.zone_at(art) == "media"
    island.mousePressEvent(_mouse(press, art, left))
    island.mouseReleaseEvent(_mouse(release, art, left))
    middle = Qt.MouseButton.MiddleButton
    island.mousePressEvent(_mouse(press, QPoint(5, 5), middle))
    island.mouseReleaseEvent(_mouse(release, QPoint(5, 5), middle))
    for delta, mods in (
        (QPoint(0, 120), Qt.KeyboardModifier.NoModifier),
        (QPoint(0, -120), Qt.KeyboardModifier.ShiftModifier),
    ):
        wheel = QWheelEvent(
            QPointF(art),
            QPointF(art),
            QPoint(),
            delta,
            Qt.MouseButton.NoButton,
            mods,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        island.wheelEvent(wheel)
    assert calls == ["play_pause", "play_pause", "volTrue", "next"]
    assert not island.expanded and island._media_hold  # mexer na música não abre o painel


def test_compact_ring_keeps_provider_color_in_alert(qapp, island, app_ctx):
    from ventude.limits.model import Report, Window
    from ventude.ui import theme
    from ventude.ui.pages.home import fill_ring
    from ventude.ui.widgets import Ring

    ring = Ring(16, 2.6)
    report = Report("claude", "ok", [Window("session", "Sessão", 92, None)])
    fill_ring(ring, report, report.windows[0], compact=True)
    assert ring._color == theme.DANGER and ring._base == theme.provider_color("claude") and ring._marker


def test_pill_width_follows_text(qapp, island, app_ctx):
    settle(qapp, 300)
    short = MediaState("Oi", "", "", "playing", 1, 100, time.monotonic(), True, True, True, True)
    app_ctx.media.state_changed.emit(short)
    narrow = island.pill.preferred_width()
    long_title = "Uma música com um nome bem comprido de verdade"
    app_ctx.media.state_changed.emit(
        MediaState(long_title, "Banda", "", "playing", 1, 100, time.monotonic(), True, True, True, True)
    )
    wide = island.pill.preferred_width()
    assert 150 <= narrow < wide <= 560


def test_island_toast_grows_and_returns(qapp, island, app_ctx):
    from ventude.ui.toast import TOAST_HEIGHT, Toast

    settle(qapp, 300)
    island.show_toast(Toast("Claude: 85% usado", "Sessão · 5h chegou a 85%.", kind="limits", seconds=0.3))
    assert island.target_rect(False).height() == TOAST_HEIGHT
    settle(qapp, 250)
    assert island.toast_view.isVisible() and not island.pill.isVisible()
    assert island.toast_view.title.full_text() == "Claude: 85% usado"
    settle(qapp, 600)
    assert island._toast is None and island.pill.isVisible()
    tray = []
    app_ctx.tray_message.connect(lambda t, m: tray.append(t))
    island.expand()
    island.show_toast(Toast("Lembrete", kind="agenda"))
    island.show_toast(Toast("Música", kind="music"))
    assert tray == ["Lembrete"]  # aberto: aviso importante vai para a bandeja, música não


def test_limit_alert_and_music_change_become_toasts(qapp, island, app_ctx):
    settle(qapp, 300)
    app_ctx.limits.alert.emit("claude", "Claude: 95% usado", "Sessão · 5h chegou a 95%.")
    assert island._toast is not None and island._toast.kind == "limits"
    island._cancel_toasts()
    first = MediaState("Um", "A", "", "playing", 1, 100, time.monotonic(), True, True, True, True)
    second = MediaState("Dois", "B", "", "playing", 1, 100, time.monotonic(), True, True, True, True)
    app_ctx.media.state_changed.emit(first)
    assert island._toast is None  # primeira faixa ao abrir: sem aviso
    app_ctx.media.state_changed.emit(second)
    assert island._toast is not None and island._toast.title == "Dois"


def test_agenda_reminders(store):
    from datetime import datetime, timedelta

    from ventude.ui.toast import Reminders

    now = datetime(2026, 11, 20, 14, 0)
    event = store.add_event("Reunião", now.date(), (now + timedelta(minutes=10)).time())
    store.add_event("Dia todo", now.date())
    reminders = Reminders(store)
    seen = []
    reminders.due.connect(lambda e, m: seen.append((e.title, m)))
    reminders.check(now)
    reminders.check(now + timedelta(seconds=30))  # não repete
    reminders.check(now + timedelta(minutes=5, seconds=10))
    reminders.check(now + timedelta(minutes=7))
    assert seen == [("Reunião", 10), ("Reunião", 5)]
    assert event.start is not None


def test_detect_installed_tools_turns_them_on(qapp, store, home, monkeypatch):
    from ventude.limits import detect
    from ventude.ui.context import AppContext

    now = time.time()
    fetchers = {
        p: (lambda c, p=p: Report(p, "ok", [Window("session", "Sessão", 10, now + 3600)]))
        for p in ("claude", "antigravity", "grok", "codex", "cursor")
    }
    ctx = AppContext(store, Tasks(), media=MediaService(), fetchers=fetchers, home=home)
    monkeypatch.setattr(detect, "installed", lambda _home: ["codex", "cursor", "copilot"])
    found = []
    ctx.detect_providers(found.extend)
    end = time.monotonic() + 3
    while not found and time.monotonic() < end:
        qapp.processEvents()
    assert found == ["codex", "cursor"]  # o Copilot nem existe neste app de teste
    assert ctx.settings.codex_mode == "auto" and ctx.settings.cursor_mode == "auto"
    assert ctx.settings.ring_enabled("codex") and not ctx.settings.ring_enabled("cursor")  # até 4 anéis
    ctx.tasks.shutdown()


def test_limits_card_shows_pace_and_history(qapp, island, app_ctx, store):
    now = time.time()
    for minutes, used in ((240, 5.0), (120, 30.0), (10, 55.0)):
        store.add_sample("claude", "session", now - minutes * 60, used)
    app_ctx.limits.refresh_all()
    settle(qapp, 400)
    island.expand("limits")
    settle(qapp, 300)
    card = island.panel.pages["limits"].cards["claude"]
    assert card.spark.isVisibleTo(card)
