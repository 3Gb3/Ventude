"""Interface do pacote "Ventude Shell & Sistema": ilha, pílula, abas novas, busca e Dock."""

import sys
import time

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent, QWheelEvent

from tests.test_ui import _mouse, settle
from ventude.services.audio import AppSession, AudioService, AudioState, Device
from ventude.services.hardware import Gpu, Snapshot
from ventude.services.lyrics import Lyrics
from ventude.services.media import MediaState
from ventude.services.privacy import PrivacyState


def key(code, mods=Qt.KeyboardModifier.NoModifier, text=""):
    return QKeyEvent(QKeyEvent.Type.KeyPress, code, mods, text)


def click(island, pos):
    from PySide6.QtCore import QEvent

    left = Qt.MouseButton.LeftButton
    island.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, pos, left))
    island.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, pos, left))


# ---------- polimentos ----------
def test_ctrl_zero_opens_settings_and_typing_is_kept(qapp, island, app_ctx):
    island.expand()
    settle(qapp, 300)
    ctrl = Qt.KeyboardModifier.ControlModifier
    island.keyPressEvent(key(Qt.Key.Key_0, ctrl))
    assert island.panel.current == "settings"
    island.panel.select("home")
    island.keyPressEvent(key(Qt.Key.Key_Comma, ctrl))
    assert island.panel.current == "settings"
    island.keyPressEvent(key(Qt.Key.Key_A, text="a"))
    bar = island.panel.command
    assert bar.isVisible() and bar.input.text() == "a"
    bar.input.setText("ab")
    bar.input.clearFocus()  # o foco saiu do campo...
    island.keyPressEvent(key(Qt.Key.Key_C, text="c"))
    assert bar.input.text() == "abc"  # ...e o que já estava digitado continua


def test_toast_hides_pill_right_away(qapp, island, app_ctx):
    from ventude.ui.toast import Toast

    settle(qapp, 300)
    island.show_toast(Toast("Oi", "aviso", seconds=0.5))
    assert not island.pill.isVisible() and island.toast_view.isVisible()  # sem esperar a animação
    settle(qapp, 900)
    assert island.pill.isVisible()


# ---------- Sistema na pílula e na aba ----------
def test_pinned_metrics_on_the_pill(qapp, island, app_ctx):
    settle(qapp, 300)
    narrow = island.target_rect(False).width()
    app_ctx.update_settings(pinned_metrics_json='["cpu", "gpu", "gpu_temp", "net_down"]')
    pill = island.pill
    assert not pill.metrics_chip.isHidden() and pill.metrics_chip.text().startswith("CPU —")
    snap = Snapshot(cpu=22, gpu=Gpu(util=75, temp=64), net_down=2048.0, disk_total=1.0)
    app_ctx.hardware.updated.emit(snap)
    assert pill.metrics_chip.text() == "CPU 22%   GPU 75% · 64°   ↓ 2 KB/s"
    wide = island.target_rect(False).width()
    assert wide > narrow
    app_ctx.hardware.updated.emit(
        Snapshot(cpu=100, gpu=Gpu(util=100, temp=99), net_down=99e6, disk_total=1.0)
    )
    assert island.target_rect(False).width() == wide  # largura pelo texto mais largo: não "pula"
    settle(qapp, 300)
    center = pill.metrics_chip.geometry().center()
    assert pill.zone_at(center) == "metrics"
    click(island, center)
    assert island.expanded and island.panel.current == "hardware"
    app_ctx.update_settings(pinned_metrics_json="")
    assert pill.metrics_chip.isHidden()


def test_hardware_page_shows_live_data_and_pins(qapp, island, app_ctx):
    island.expand("hardware")
    settle(qapp, 300)
    page = island.panel.pages["hardware"]
    assert "page" in app_ctx.hardware._viewers  # a aba avisou que está aberta
    snap = Snapshot(
        cpu=42,
        cores=8,
        gpu=Gpu(name="GPU Teste", util=30, vram_used=2 * 1024**3, vram_total=8 * 1024**3, temp=88),
        ram_used=8 * 1024**3,
        ram_total=16 * 1024**3,
        net_down=1024.0,
        net_up=2048.0,
        ping=20,
        disk_path="C:\\",
        disk_used=100.0,
        disk_total=400.0,
        top_cpu=[("Chrome", 12.0)],
        top_ram=[("Chrome", 1024**3)],
    )
    app_ctx.hardware._done(snap)
    assert page.lines["cpu"].value.text() == "42%"
    assert (
        page.lines["gpu_temp"].value.text() == "88°" and "F27A7A" in page.lines["gpu_temp"].value.styleSheet()
    )
    assert page.lines["ping"].value.text() == "20 ms" and page.cpu_top.rows[0][0].full_text() == "Chrome"
    assert page.no_battery.isVisibleTo(page)
    page.lines["ram"].pin.click()
    assert app_ctx.settings.pinned_metrics() == ["ram"] and page.lines["ram"].pin.pinned
    island.panel.select("home")
    assert "page" not in app_ctx.hardware._viewers


def test_hardware_alert_becomes_island_toast(qapp, island, app_ctx):
    settle(qapp, 300)
    app_ctx.hardware.alert.emit("gpu_temp", "GPU muito quente", "90 °C na placa de vídeo")
    assert island._toast.kind == "hardware" and island._toast.title == "GPU muito quente"


# ---------- áudio ----------
def test_audio_page_lists_devices_and_apps(qapp, island, app_ctx, monkeypatch):
    calls = []
    for name in ("set_master", "set_default", "set_session", "set_session_mute", "set_mic_mute"):
        monkeypatch.setattr(app_ctx.audio, name, lambda *a, n=name: calls.append((n, *a)))
    island.expand("audio")
    settle(qapp, 300)
    page = island.panel.pages["audio"]
    state = AudioState(
        outputs=(
            Device("o1", "Alto-falantes (Realtek)", "render", True),
            Device("o2", "Fones (USB)", "render"),
        ),
        inputs=(Device("i1", "Microfone (USB)", "capture", True),),
        master=0.5,
        mic=0.9,
        mic_muted=True,
        sessions=(
            AppSession("c:\\x\\discord.exe", "Discord", "c:\\x\\discord.exe", (1,), 0.8, False, True),
            AppSession("system", "Sons do sistema", "", (2,), 1.0, False, system=True),
        ),
    )
    app_ctx.audio.changed.emit(state)
    assert page.master.slider.value() == 50 and page.mic.value.text() == "Mudo"
    assert page.outputs.buttons["o1"].isChecked() and not page.outputs.buttons["o2"].isChecked()
    assert list(page.rows) == ["c:\\x\\discord.exe", "system"]
    page.outputs.buttons["o2"].click()
    page.rows["c:\\x\\discord.exe"].volume.slider.setValue(30)
    page.rows["system"].volume.mute.click()
    page.mic.mute.click()
    assert ("set_default", "o2") in calls
    assert ("set_session", "c:\\x\\discord.exe", 0.3) in calls
    assert ("set_session_mute", "system", True) in calls and ("set_mic_mute", False) in calls


def test_wheel_changes_volume_and_shows_it(qapp, island, app_ctx, monkeypatch):
    settle(qapp, 300)
    steps = []
    monkeypatch.setattr(AudioService, "running", property(lambda self: True))
    monkeypatch.setattr(app_ctx.audio, "step_master", lambda up, n=1: steps.append((up, n)))
    pos = QPointF(island.pill.time.geometry().center())
    island.wheelEvent(
        QWheelEvent(
            pos, pos, QPoint(), QPoint(0, 240), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
    )  # fmt: skip
    assert steps == [(True, 2)] and not island.expanded  # sem música também muda o volume
    app_ctx.audio.master_feedback.emit(0.68, False)
    assert island.volume_overlay.active and abs(island.volume_overlay.level - 0.68) < 1e-6
    app_ctx.audio.master_feedback.emit(0.70, False)
    assert abs(island.volume_overlay.level - 0.70) < 1e-6 and not island._toasts  # troca sem enfileirar


def test_privacy_dots_and_mic_click(qapp, island, app_ctx, monkeypatch):
    settle(qapp, 300)
    toggles = []
    monkeypatch.setattr(AudioService, "running", property(lambda self: True))
    monkeypatch.setattr(app_ctx.audio, "toggle_mic_mute", lambda: toggles.append(1))
    app_ctx.privacy.apply(PrivacyState(mic=("Discord",), cam=("Teams",)))
    pill = island.pill
    assert not pill.mic_dot.isHidden() and not pill.cam_dot.isHidden()
    assert pill.mic_dot.toolTip().startswith("Microfone em uso por Discord")
    settle(qapp, 300)
    dot = pill.mic_dot.mapTo(pill, pill.mic_dot.rect().center())
    assert pill.zone_at(dot) == "mic"
    click(island, dot)
    assert toggles == [1] and not island.expanded
    app_ctx.audio.mic_feedback.emit(True)
    assert island._toast.title == "Microfone mutado"
    app_ctx.update_settings(pill_privacy=False)
    assert pill.privacy_box.isHidden()


# ---------- cafeína, energia, QR ----------
def test_caffeine_button_command_and_pill(qapp, island, app_ctx, monkeypatch):
    applied = []
    monkeypatch.setattr(app_ctx.caffeine, "_apply", lambda flags: applied.append(flags) or True)
    island.expand()
    settle(qapp, 300)
    bar = island.panel.command
    bar.open_bar()
    command = bar.commands("cafeina 30m")[0]
    assert command.title == "Cafeína por 30:00"
    command.run()
    assert app_ctx.caffeine.active and not island.pill.caffeine_icon.isHidden()
    assert bar.commands("cafeina off")[0].title == "Desligar a Cafeína"
    island.panel.coffee_btn.click()  # o botão do cabeçalho desliga
    assert not app_ctx.caffeine.active and island.pill.caffeine_icon.isHidden()
    app_ctx.power.pending = True
    app_ctx.power.changed.emit(True)
    assert island.panel.power_btn._badge  # selo de atualização pendente


def test_qr_from_command_bar_and_shelf(qapp, island, app_ctx):
    island.expand()
    settle(qapp, 300)
    bar = island.panel.command
    bar.open_bar("qr https://exemplo.com")
    assert bar.commands(bar.input.text())[0].title == "Mostrar QR Code"
    bar._run(bar.results.item(0))
    card = island.panel.qr
    assert card.isVisible() and card.text == "https://exemplo.com" and card.view.matrix
    assert island._keep_open()
    island.keyPressEvent(key(Qt.Key.Key_Escape))
    assert not card.isVisible() and island.expanded  # Esc fecha só o QR
    app_ctx.shelf.add_text("#11AA33")
    island.panel.select("shelf")
    page = island.panel.pages["shelf"]
    page.show_qr(app_ctx.shelf.items[0])
    assert card.isVisible() and card.text == "#11AA33"


# ---------- estante ----------
def test_shelf_filters_and_pin(qapp, island, app_ctx):
    from PySide6.QtCore import QMimeData

    app_ctx.shelf.add_text("solto na ilha")
    copied = QMimeData()
    copied.setText("https://copiado.com")
    app_ctx.shelf.capture(copied)
    island.expand("shelf")
    settle(qapp, 200)
    page = island.panel.pages["shelf"]
    assert page.grid.count() == 2
    page.set_mode("clip")
    assert [i.value for i in page.shown_items()] == ["https://copiado.com"]
    page._pin(page.shown_items()[0])
    page.set_mode("pinned")
    assert page.grid.count() == 1
    assert island.pill.shelf_count.text() == "2"  # solto + fixado (o histórico puro não conta)


# ---------- letra no Início ----------
def test_media_card_shows_synced_lyric(qapp, island, app_ctx, store):
    from ventude.services.lyrics import cache_key

    store.cache_set(
        cache_key("Song", "Band"), {"syncedLyrics": "[00:00.50] Primeiro verso\n[00:30.00] Outro"}, 60
    )
    island.expand("home")
    settle(qapp, 200)
    state = MediaState("Song", "Band", "", "playing", 10, 100, time.monotonic(), True, True, True, True)
    app_ctx.media.state = state
    app_ctx.media.state_changed.emit(state)
    card = island.panel.pages["home"].media
    assert isinstance(card.lyrics, Lyrics) and card.lyric.isVisibleTo(card)
    assert card.lyric.full_text() == "♪ Primeiro verso" and card.lyric.toolTip() == "A seguir: Outro"
    app_ctx.update_settings(lyrics_enabled=False)
    assert not card.lyric.isVisibleTo(card)


# ---------- busca universal ----------
def test_command_bar_search_calculator_folders_and_power(qapp, island, app_ctx, monkeypatch, tmp_path):
    from ventude.services import indexer, power

    folder = tmp_path / "Projetos"
    folder.mkdir()
    app_ctx.indexer.files = [indexer.entry("Projetos", str(folder), "folder")]
    app_ctx.indexer.apps = [indexer.entry("Calculadora", "Microsoft.WindowsCalculator!App", "uwp")]
    opened = []
    monkeypatch.setattr(indexer, "open_terminal", lambda f: opened.append(("terminal", f)) or (True, ""))
    monkeypatch.setattr(indexer, "open_in_editor", lambda f: opened.append(("editor", f)) or (True, "Cursor"))
    monkeypatch.setattr(indexer, "launch", lambda e: opened.append(("open", e.path)) or (True, ""))
    ran = []
    monkeypatch.setattr(power, "run", lambda k: ran.append(k) or (True, ""))
    island.open_start()
    settle(qapp, 200)
    bar = island.panel.command
    assert bar.isVisible() and bar.start_mode
    bar.input.setText("1450 * 5,85")
    assert bar.commands(bar.input.text())[0].title == "= 8482,5"
    bar.eventFilter(bar.input, key(Qt.Key.Key_Return))
    assert QGuiApplication.clipboard().text() == "8482,5"
    bar.open_bar("/p proj", start=True)
    first = bar.commands(bar.input.text())[0]
    assert first.title == "Projetos" and first.ctrl and first.shift and len(first.actions) == 2
    bar.eventFilter(bar.input, key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier))
    bar.open_bar("/p proj", start=True)
    bar.eventFilter(bar.input, key(Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier))
    bar.open_bar("calc", start=True)
    bar.eventFilter(bar.input, key(Qt.Key.Key_Return))
    assert opened == [
        ("terminal", str(folder)),
        ("editor", str(folder)),
        ("open", "Microsoft.WindowsCalculator!App"),
    ]
    bar.open_bar("reiniciar", start=True)
    assert bar.commands("reiniciar")[0].confirm
    bar.eventFilter(bar.input, key(Qt.Key.Key_Return))
    assert ran == [] and "Enter de novo" in bar.status.text()  # 1º Enter só arma
    bar.eventFilter(bar.input, key(Qt.Key.Key_Return))
    assert ran == ["restart"]
    app_ctx.store.add_shortcut("url", "GitHub", "https://github.com")
    bar.open_bar("", start=True)
    assert [c.title for c in bar.commands("")] == ["GitHub"]  # Menu Iniciar vazio: atalhos fixados
    assert [c.title for c in bar.commands("sistema")] == ["Abrir Sistema"]


# ---------- Ventude Shell (3.0) ----------
def wait_for(qapp, condition, seconds=3.0):
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


@pytest.fixture
def shell(qapp, app_ctx, island, tmp_path, monkeypatch):
    """ShellController com barra do Windows simulada: nunca toca na barra real."""
    from ventude.services import taskbar
    from ventude.services.taskbar import TaskbarHider, WindowInfo
    from ventude.ui.shell.controller import ShellController
    from ventude.ui.shell.curtain import PowerFx

    log = []
    hider = TaskbarHider(
        windows=lambda: [1],
        show=lambda hwnd, cmd: log.append(("show", cmd)),
        screens=lambda: [((0, 0, 1920, 1080), (0, 0, 1920, 1040))],
        work_area=lambda rect: log.append(("area", rect)) or True,
        marker=tmp_path / "barra.json",
    )
    windows = [WindowInfo(10, "Documento - Editor", 1, sys.executable, foreground=True)]
    monkeypatch.setattr(taskbar, "list_windows", lambda own_pid=None: list(windows))
    monkeypatch.setattr(taskbar, "covering_window", lambda *a, **k: False)
    monkeypatch.setattr(taskbar, "foreground_rect", lambda *a, **k: None)
    monkeypatch.setattr(taskbar, "monitor_scale", lambda rect: 1.25)
    monkeypatch.setattr(taskbar, "activate", lambda hwnd: log.append(("activate", hwnd)) or True)
    monkeypatch.setattr(taskbar, "minimize", lambda hwnd: log.append(("minimize", hwnd)))

    fake_power = PowerFx(
        app_ctx,
        runner=lambda k: log.append(("power", k)) or (True, ""),
        time_scale=0.05,
        island=island,
    )
    from ventude.services import system

    keys = system.KeyboardHook()
    monkeypatch.setattr(keys, "install", lambda: True)
    monkeypatch.setattr(keys, "remove", lambda: None)
    controller = ShellController(app_ctx, island, hider, keys=keys, fx=fake_power)
    controller.test_log = log
    controller.test_windows = windows
    yield controller
    controller.shutdown()
    for widget in [*controller.docks, *controller.echos, controller.launcher, controller.thumbs]:
        if widget is not None:
            widget.hide()
            widget.deleteLater()
    app_ctx.shell = None
    app_ctx.power_fx = None


def test_shell_dock_hides_windows_bar_dodge_and_always_restores(qapp, app_ctx, island, shell):
    from ventude.services.taskbar import SW_HIDE, SW_SHOW

    log = shell.test_log
    shell.start()
    assert not shell.docks and not any(kind == "show" for kind, _ in log)
    assert [p.label for p in app_ctx.store.pins("taskbar")] == ["Explorador de Arquivos"]

    app_ctx.update_settings(replace_taskbar=True)
    assert shell.hider.hidden and ("show", SW_HIDE) in log
    # Modo padrão "esquivar" libera a tela toda (sem reserva de 64 px)
    assert ("area", (0, 0, 1920, 1080)) in log
    dock = shell.docks[0]
    assert dock.isVisible() and island.panel.power_btn.isHidden()
    assert wait_for(qapp, lambda: len(dock.items) == 2)
    explorer, editor = dock.items
    assert explorer.pinned and not explorer.windows and editor.windows[0].hwnd == 10

    # Modo "sempre" reserva 64 px (80 px em 125%)
    app_ctx.update_settings(dock_mode="sempre")
    assert ("area", (0, 0, 1920, 1000)) in log

    shell.item_clicked(editor, dock.slot_screen_rect(editor.key), dock)
    assert ("minimize", 10) in log

    log.clear()
    shell.emergency()
    assert not shell.hider.hidden and ("show", SW_SHOW) in log and not dock.isVisible()
    shell.sync()
    assert not shell.hider.hidden

    app_ctx.update_settings(replace_taskbar=False)
    app_ctx.update_settings(replace_taskbar=True, dock_mode="oculta")
    assert shell.hider.hidden
    app_ctx.update_settings(replace_taskbar=False)
    assert not shell.hider.hidden and not shell.hider.marker.exists()
    assert not island.panel.power_btn.isHidden()


def test_dock_reorder_unpin_and_controls_in_island(qapp, app_ctx, island, shell):
    from ventude.ui.island import IslandMode

    app_ctx.update_settings(replace_taskbar=True, dock_mode="sempre")
    shell.start()
    dock = shell.docks[0]
    p1 = app_ctx.store.add_pin("taskbar", "app", "App A", r"C:\Apps\a.exe")
    p2 = app_ctx.store.add_pin("taskbar", "app", "App B", r"C:\Apps\b.exe")
    shell.refresh_windows(force=True)
    assert wait_for(qapp, lambda: len(app_ctx.store.pins("taskbar")) >= 3)

    ids = [p.id for p in app_ctx.store.pins("taskbar")]
    dock.pin_reordered.emit(list(reversed(ids)))
    assert [p.id for p in app_ctx.store.pins("taskbar")] == list(reversed(ids))
    dock.pin_unpinned.emit(p1.id)
    assert app_ctx.store.pinned("taskbar", p1.target) is None
    dock.pin_unpinned.emit(p2.id)

    shell.keys.controls_pressed.emit()
    assert wait_for(qapp, lambda: island.mode == IslandMode.CONTROLS)
    h_before = island.controls.preferred_height()
    island.controls._toggle_devices()
    assert island.controls.preferred_height() > h_before
    app_ctx.caffeine._apply = lambda _flags: True
    island.controls.caffeine.clicked.emit()
    assert app_ctx.caffeine.active and island.controls.caffeine.on
    app_ctx.caffeine.stop()


def test_win_key_opens_launcher_and_slot_shortcuts(qapp, app_ctx, island, shell):
    app_ctx.update_settings(replace_taskbar=True)
    shell.start()
    shell.keys.start_pressed.emit()
    assert wait_for(qapp, lambda: shell.launcher is not None and shell.launcher.isVisible())
    shell.keys.start_pressed.emit()
    assert wait_for(qapp, lambda: not shell.launcher.isVisible())
    shell.keys.search_pressed.emit()
    assert wait_for(qapp, lambda: shell.launcher.isVisible())
    assert "slots" in shell.keys.logic.features and "launcher" not in shell.keys.logic.features
    shell.launcher.hide()

    app_ctx.update_settings(replace_start_menu=True)
    assert "launcher" in shell.keys.logic.features
    island.expand()
    settle(qapp, 250)
    island.set_shell_mode(True)
    island.keyPressEvent(key(Qt.Key.Key_D, text="d"))
    assert wait_for(qapp, lambda: shell.launcher.isVisible()) and shell.launcher.input.text() == "d"
    shell.launcher.hide()

    assert wait_for(qapp, lambda: len(shell.docks[0].items) == 2)
    shell.keys.slot_pressed.emit(2, False)
    assert ("minimize", 10) in shell.test_log


def test_launcher_home_scopes_all_apps_and_power(qapp, app_ctx, shell, monkeypatch, tmp_path):
    from ventude.services import indexer
    from ventude.services.audio import AudioService

    monkeypatch.setattr(AudioService, "running", property(lambda self: True))
    monkeypatch.setattr(app_ctx.audio, "set_master", lambda val: shell.test_log.append(("vol", val)))

    repo = tmp_path / "meu-projeto"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    app_ctx.indexer.apps = [
        indexer.entry("Calculadora", "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App", "uwp"),
        indexer.entry("Discord", sys.executable, "app"),
        indexer.entry("Steam", sys.executable + "x", "app"),
        indexer.entry("Spotify", "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "uwp"),
        indexer.entry("Claude", "Claude_pzs8sxrjxfjjc!Claude", "uwp"),
    ]
    app_ctx.indexer.files = [
        indexer.entry("meu-projeto", str(repo), "folder", is_project=True, branch="main")
    ]
    launched = []
    monkeypatch.setattr(indexer, "launch", lambda e: launched.append(e.path) or (True, ""))

    shell.seed_start_pins()
    assert [p.label for p in app_ctx.store.pins("start")] == ["Calculadora", "Spotify", "Discord", "Steam"]
    app_ctx.store.rename_pin(app_ctx.store.pins("start")[0].id, "Calc")
    app_ctx.store.record_launch("app", "Discord", sys.executable)

    shell.open_search()
    ln = shell.launcher
    assert wait_for(qapp, ln.isVisible)
    assert ln.pinned.tiles[0].label == "Calc"

    app_ctx.store.record_launch("app", "Steam", sys.executable + "x")
    ln.refresh_recent()
    assert len(ln.continue_list._rows) == 2

    ln.input.setText("= 1450 * 5,85")
    assert ln.results.list.rows[1].command.title == "= 8482,5"
    ln.input.setText("@ Doc")
    assert ln.results.list.rows[1].command.kind == "window"
    ln.input.setText("/ meu-projeto")
    proj_cmd = ln.results.list.rows[1].command
    assert proj_cmd.kind == "project" and proj_cmd.branch == "main" and proj_cmd.agent is not None
    ln.input.setText("> volume 40")
    ln.eventFilter(ln.input, key(Qt.Key.Key_Return))
    assert ("vol", 0.4) in shell.test_log
    assert wait_for(qapp, lambda: not ln.isVisible())
    shell.open_search()
    assert wait_for(qapp, ln.isVisible)

    ln.show_all_apps()
    assert "S" in ln.all_apps.app_list.letters()
    ln.all_apps.rail.picked.emit("S")

    ran_power = []
    monkeypatch.setattr(app_ctx, "request_power", lambda k: ran_power.append(k) or (True, ""))
    app_ctx.update_settings(power_animation=False)
    ln.show_power()
    ln.keyPressEvent(key(Qt.Key.Key_4))
    assert ran_power == [] and ln.power_view.warn.isVisible()
    ln.keyPressEvent(key(Qt.Key.Key_4))
    assert wait_for(qapp, lambda: ran_power == ["shutdown"])

    ln.eventFilter(ln.input, key(Qt.Key.Key_Escape))
    assert wait_for(qapp, lambda: not ln.isVisible())


def test_thumbs_echo_pill_quiet_mode_and_v7_migration(qapp, app_ctx, island, shell):
    from ventude.core.store import Pin
    from ventude.services.taskbar import WindowInfo
    from ventude.ui.echo import EchoPill
    from ventude.ui.shell.items import build_items
    from ventude.ui.shell.thumbs import ThumbnailPopup
    from ventude.ui.toast import Toast

    app_ctx.update_settings(quiet_mode=True)
    island.show_toast(Toast("Build", "ok", kind="info", seconds=0.5))
    assert not island.toast_view.isVisible() and not island._toasts
    island.show_toast(Toast("GPU", "quente", kind="alert", seconds=0.5))
    assert island.toast_view.isVisible()
    island._cancel_toasts()

    echo = EchoPill(app_ctx, qapp.primaryScreen())
    assert echo.anchor_rect().height() == 30
    echo.set_shown(True)
    assert echo._shown
    echo.set_shown(False)
    echo.deleteLater()

    items = build_items(
        [Pin(1, "taskbar", "app", "Code", r"C:\Apps\Code.exe", 0)],
        [WindowInfo(99, "main.py", 12, r"C:\Apps\Code.exe", foreground=True)],
    )
    popup = ThumbnailPopup(app_ctx, shell)
    popup.show_for(items[0], QRect(400, 1010, 44, 44), 1010, QRect(0, 0, 1920, 1080))
    assert len(popup.windows) == 1
    popup.hide_now()
    popup.deleteLater()

    app_ctx.store.add_shortcut("url", "GitHub", "https://github.com")
    app_ctx.update_settings(
        layout_version=6, taskbar_autohide=True, pill_date=False, pill_weather=False
    )
    app_ctx.apply_migrations()
    assert app_ctx.settings.layout_version == 7
    assert app_ctx.settings.dock_mode == "oculta"
    assert app_ctx.settings.pill_date and app_ctx.settings.pill_weather
    assert app_ctx.store.pinned("start", "https://github.com") is not None


def test_power_curtain_interruptor_and_ipc_notify(qapp, app_ctx, island, monkeypatch):
    from ventude import app as ventude_app
    from ventude.services import power
    from ventude.ui.shell.curtain import PowerFx

    ran = []
    landed = []
    monkeypatch.setattr(power, "run", lambda k: ran.append(f"direto:{k}") or (True, ""))
    fx = PowerFx(
        app_ctx, runner=lambda k: ran.append(k) or (True, ""), time_scale=0.05, island=island
    )
    fx.greet_landed.connect(lambda: landed.append(True))
    app_ctx.power_fx = fx
    try:
        assert app_ctx.request_power("shutdown") == (True, "")
        assert fx.active and fx.title == "Até logo!" and fx.curtains and fx.cancellable
        assert ran == []
        assert wait_for(qapp, lambda: ran == ["shutdown"])
        assert fx.phase == "gone" and not fx.cancellable
        fx.skip()
        assert not fx.active and not fx.curtains

        fx.play("restart")
        assert fx.title == "Já volto!"
        fx.curtains[0].keyPressEvent(key(Qt.Key.Key_Escape))
        assert wait_for(qapp, lambda: not fx.active) and ran == ["shutdown"]

        fx.greet("Fulano de Tal", "27° e sol")
        assert fx.title.endswith(", Fulano!") and "27° e sol" in fx.subtitle
        assert wait_for(qapp, lambda: not fx.active) and landed == [True]
    finally:
        app_ctx.power_fx = None

    stamps: list[float] = []
    res = ventude_app.handle_ipc_payload(
        b'notify {"title": "Deploy", "text": "Concluido"}', island, stamps, now=10.0
    )
    assert res == "toast" and not island.expanded and island.toast_view.isVisible()
    for i in range(4):
        assert (
            ventude_app.handle_ipc_payload(b'notify {"title": "X"}', island, stamps, now=10.1 + i * 0.01)
            == "toast"
        )
    limited = ventude_app.handle_ipc_payload(b'notify {"title": "X"}', island, stamps, now=10.2)
    assert limited == "rate_limited"
    assert ventude_app.handle_ipc_payload(b"x" * 5000, island, stamps, now=12.0) == "too_large"
    assert ventude_app.handle_ipc_payload(b"show", island, stamps, now=12.0) == "reveal" and island.expanded
