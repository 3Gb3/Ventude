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
