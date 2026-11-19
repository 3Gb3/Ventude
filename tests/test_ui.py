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
