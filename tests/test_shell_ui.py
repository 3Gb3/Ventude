"""Interface do pacote "Ventude Shell & Sistema": ilha, pílula, abas novas, busca e Dock."""

import time

from PySide6.QtCore import QPoint, QPointF, Qt
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
    assert island._toast.kind == "volume" and island._toast.title == "Volume 68%"
    app_ctx.audio.master_feedback.emit(0.70, False)
    assert island._toast.title == "Volume 70%" and not island._toasts  # troca, não enfileira


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


def test_win_key_toggles_start_menu(qapp, island, app_ctx):
    settle(qapp, 200)
    island.keys.start_pressed.emit()
    settle(qapp, 100)
    assert island.expanded and island.panel.command.isVisible()
    island.keys.start_pressed.emit()  # de novo: fecha
    settle(qapp, 100)
    assert not island.expanded
    island.keys.search_pressed.emit()
    settle(qapp, 100)
    assert island.expanded and island.panel.command.isVisible()
    assert not island.keys.active  # o hook só liga com o ajuste (e no app de verdade)


# ---------- Dock ----------
def test_dock_controller_hides_and_always_restores(qapp, app_ctx, tmp_path, monkeypatch):
    from ventude.services import taskbar
    from ventude.services.taskbar import TaskbarHider
    from ventude.ui.dock import DockController

    log = []
    hider = TaskbarHider(
        windows=lambda: [1],
        show=lambda hwnd, cmd: log.append(cmd),
        screens=lambda: [((0, 0, 100, 100), (0, 0, 100, 90))],
        work_area=lambda rect: True,
        marker=tmp_path / "m.json",
    )
    monkeypatch.setattr(taskbar, "list_windows", lambda own_pid=None: [])
    monkeypatch.setattr(taskbar, "covering_window", lambda rect, own_pid=None: False)
    monkeypatch.setattr("ventude.ui.dock.sys.platform", "win32")
    starts = []
    controller = DockController(app_ctx, lambda: starts.append(1), hider)
    controller.start()
    assert controller.dock is None and log == []  # desligado: nada muda
    app_ctx.update_settings(replace_taskbar=True)
    assert hider.hidden and log == [taskbar.SW_HIDE] and controller.dock.isVisible()
    controller.dock.click(controller.dock.start)
    assert starts == [1]
    controller.emergency()  # erro inesperado: a barra volta na hora
    assert not hider.hidden and log[-1] == taskbar.SW_SHOW and not controller.dock.isVisible()
    controller.sync()
    assert not hider.hidden  # continua restaurada até mexer no ajuste
    app_ctx.update_settings(replace_taskbar=False)
    app_ctx.update_settings(replace_taskbar=True)
    assert hider.hidden
    app_ctx.update_settings(replace_taskbar=False)
    assert not hider.hidden and not (tmp_path / "m.json").exists()
    controller.dock.deleteLater()
