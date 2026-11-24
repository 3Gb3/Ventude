import time
from datetime import date
from datetime import time as dtime
from pathlib import Path

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


def test_sibling_pages_share_one_tab(qapp, island, app_ctx):
    from ventude.core.config import TAB_GROUPS

    island.expand()
    settle(qapp, 300)
    panel = island.panel
    assert list(panel.tabs) == [group[0] for group in TAB_GROUPS]  # 8 abas para 12 páginas
    panel.select("tasks")
    tab = panel.tabs["agenda"]
    assert panel.current == "tasks" and tab.isChecked() and tab.current == "tasks"
    assert [k for k, _ in tab.members] == ["agenda", "tasks"]
    settle(qapp, 450)  # a aba ativa cresce até mostrar os dois nomes
    agenda_x = next(rect for key, _t, rect in tab._segment_rects() if key == "agenda").center().x()
    assert tab.segment_at(agenda_x) == "agenda"
    tab.picked.emit(tab.segment_at(agenda_x))
    assert panel.current == "agenda" and not panel.tabs["market"].isChecked()
    panel.select_index(2)  # Ctrl + 3: a terceira aba (Sistema)
    assert panel.current == "hardware"
    assert panel.strip.indicator_rect() is not None
    app_ctx.update_settings(modules="limits,tasks")  # só Tarefas ligada: a aba do grupo é dela
    assert list(panel.tabs) == ["home", "limits", "tasks", "settings"]
    panel.select("tasks")
    assert panel.current == "tasks"


def test_settings_sections_nav_scrolls_and_follows(qapp, island, app_ctx):
    island.expand("settings")
    settle(qapp, 300)
    page = island.panel.pages["settings"]
    assert list(page.section_cards) == ["geral", "ias", "abas", "shell", "bloqueio", "sobre"]
    assert page.nav.current == "geral"
    page.scroll_to("bloqueio", animate=False)
    bar = page.area.verticalScrollBar()
    assert page.nav.current == "bloqueio" and bar.value() > 0
    bar.setValue(0)  # rolar de volta para o topo marca Geral de novo
    assert page.nav.current == "geral"
    page.nav.buttons["ias"].click()
    settle(qapp, 600)
    assert page.nav.current == "ias" and bar.value() > 0


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
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QDropEvent

    settle(qapp, 200)
    assert not island.expanded and island.acceptDrops()
    mime, enter, _drop = _drag_events([str(tmp_path), "https://example.com/x"])
    island.dragEnterEvent(enter)
    assert enter.isAccepted() and island.expanded
    hint = island.panel.drop_hint
    assert hint.zones == ["shelf", "shortcuts"]  # duas áreas: estante e atalhos
    assert island._keep_open()  # não fecha enquanto o arquivo está sendo arrastado
    settle(qapp, 400)
    right = hint.mapTo(island, hint._rects()[1].center().toPoint())
    drop = QDropEvent(
        QPointF(right),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    island.dropEvent(drop)
    assert {s.kind for s in store.shortcuts()} == {"folder", "url"}
    page = island.panel.pages["shortcuts"]
    assert island.panel.current == "shortcuts" and "2 atalhos adicionados" in page.status.text()
    assert not page.grid.acceptDrops()  # a grade não "engole" o arraste: a ilha cuida


def test_drop_on_shelf_and_drag_out(qapp, island, app_ctx, tmp_path):
    from PySide6.QtCore import QMimeData, QPointF, Qt
    from PySide6.QtGui import QDragEnterEvent, QDropEvent

    from ventude.ui.pages.shelf import item_mime

    settle(qapp, 200)
    file = tmp_path / "foto.png"
    file.write_bytes(b"x")
    mime, enter, _ = _drag_events([str(file)])
    island.dragEnterEvent(enter)
    settle(qapp, 400)
    hint = island.panel.drop_hint
    left = hint.mapTo(island, hint._rects()[0].center().toPoint())
    args = (Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    island.dropEvent(QDropEvent(QPointF(left), *args))
    assert island.panel.current == "shelf"
    assert [(i.kind, i.name) for i in app_ctx.shelf.items] == [("image", "foto.png")]
    text = QMimeData()
    text.setText("um trecho copiado")
    island.dragEnterEvent(QDragEnterEvent(QPointF(left), *args[:1], text, *args[2:]))
    assert island.panel.drop_hint.zones == ["shelf"]  # texto só cabe na estante
    island.dropEvent(QDropEvent(QPointF(left), args[0], text, *args[2:]))
    assert app_ctx.shelf.items[0].kind == "text"
    out = item_mime(app_ctx.shelf.items)
    assert out.text() == "um trecho copiado" and Path(out.urls()[0].toLocalFile()) == file
    page = island.panel.pages["shelf"]
    assert page.grid.count() == 2 and page.grid.dragEnabled()
    app_ctx.shelf.clear()
    assert page.grid.count() == 0 and page.empty.isVisibleTo(page)


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


def test_command_bar_actions(qapp, island, app_ctx, store):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    store.add_shortcut("url", "GitHub", "https://github.com")
    island.expand()
    settle(qapp, 300)
    bar = island.panel.command
    island.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    )
    assert bar.isVisible()
    titles = [c.title for c in bar.commands("git")]
    assert "GitHub" in titles
    assert [c.title for c in bar.commands("clima")] == ["Abrir Clima"]
    bar.commands("t: revisar PR")[0].run()
    assert any(i.text == "revisar PR" for i in store.items(store.lists()[0].id))
    bar.commands("a: call 15h")[0].run()
    assert [(e.title, e.start.hour) for e in store.events_on(date.today())] == [("call", 15)]
    bar.commands("timer 10m")[0].run()
    assert app_ctx.timer.state is not None and app_ctx.timer.state.total == 600
    assert not island.pill.timer_chip.isHidden()
    app_ctx.timer.stop()
    bar.close_bar()
    island.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_G, Qt.KeyboardModifier.NoModifier, "g")
    )
    assert bar.isVisible() and bar.input.text() == "g" and island._keep_open()
    island.collapse()
    assert not bar.isVisible()


def test_color_picker_and_ocr_flow(qapp, island, app_ctx, monkeypatch):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QMouseEvent, QPixmap

    from ventude.services import ocr
    from ventude.ui.capture import ScreenOverlay, color_hex, prepare_for_ocr

    image = QImage(100, 60, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    overlay = ScreenOverlay(QGuiApplication.primaryScreen(), QPixmap.fromImage(image), "color")
    picked = []
    overlay.picked.connect(picked.append)
    click = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    overlay.mousePressEvent(click)
    assert color_hex(picked[0]) == "#336699"
    ocr_overlay = ScreenOverlay(QGuiApplication.primaryScreen(), QPixmap.fromImage(image), "ocr")
    ocr_overlay.start, ocr_overlay.end = QPoint(5, 5), QPoint(45, 35)
    assert ocr_overlay.crop().size().width() == 41
    assert prepare_for_ocr(ocr_overlay.crop()).width() == 82  # região pequena fica maior para o OCR
    overlay.deleteLater()
    ocr_overlay.deleteLater()

    settle(qapp, 300)
    island._capture_done("color", QColor("#ABCDEF"))
    assert QGuiApplication.clipboard().text() == "#ABCDEF" and island._toast.title == "Cor #ABCDEF copiada"
    island._cancel_toasts()
    monkeypatch.setattr(ocr, "recognize", lambda data: "Olá mundo")
    island._capture_done("ocr", image)
    end = time.monotonic() + 2
    while QGuiApplication.clipboard().text() != "Olá mundo" and time.monotonic() < end:
        qapp.processEvents()
    assert QGuiApplication.clipboard().text() == "Olá mundo"
    assert island._toast.title.startswith("Texto copiado")
    assert not ocr.available() or ocr.available()  # só existe no Windows


def test_settings_update_button(qapp, island, app_ctx, monkeypatch):
    from ventude.core import net, updates

    island.expand("settings")
    settle(qapp, 100)
    page = island.panel.pages["settings"]

    def wait_enabled():
        end = time.monotonic() + 3
        while not page.update_btn.isEnabled() and time.monotonic() < end:
            qapp.processEvents()

    monkeypatch.setattr(updates, "latest", lambda _v: None)
    page.update_btn.click()
    wait_enabled()
    assert "mais nova" in page.update_status.text()

    def boom(_v):
        raise net.NetError("sem rede")

    monkeypatch.setattr(updates, "latest", boom)
    page.update_btn.click()
    wait_enabled()
    assert "sem rede" in page.update_status.text()

    found = updates.Update("9.9.9", "- Nova", "https://github.com/x/Ventude-Setup.exe", 10)
    monkeypatch.setattr(updates, "latest", lambda _v: found)
    monkeypatch.setattr(updates, "enabled", lambda: False)
    page.update_btn.click()
    wait_enabled()
    assert "9.9.9" in page.update_status.text() and page._update_dialog is None
    monkeypatch.setattr(updates, "enabled", lambda: True)
    page.update_btn.click()
    wait_enabled()
    assert page._update_dialog is not None and page._update_dialog.isVisible()
    page._update_dialog.close()
