"""Serviços do pacote "Ventude Shell & Sistema" (sem mexer no sistema de verdade)."""

import sys
import time
from pathlib import Path

import pytest

from ventude.core.config import Settings
from ventude.services import calc, hardware, indexer, power, privacy, qr
from ventude.services.audio import (
    AudioService,
    AudioState,
    Device,
    device_kind,
    group_sessions,
    short_device_name,
)
from ventude.services.caffeine import AWAKE, ES_CONTINUOUS, CaffeineService
from ventude.services.hardware import Battery, Gpu, Snapshot
from ventude.services.lyrics import Lyrics, LyricsService, from_json, parse_lrc
from ventude.services.system import BLOCK, PASS, VK_LWIN, VK_S, WinKeyLogic
from ventude.services.taskbar import SW_HIDE, SW_SHOW, TaskbarHider, WindowInfo, is_app_window
from ventude.services.winutil import app_name, lnk_target


def wait(qapp, condition, seconds=3.0):
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


# ---------- ajustes ----------
def test_pinned_metrics_settings():
    s = Settings()
    assert s.pinned_metrics() == []
    s = s.copy(pinned_metrics_json=s.with_metric("cpu", True))
    s = s.copy(pinned_metrics_json=s.with_metric("ram", True))
    s = s.copy(pinned_metrics_json=s.with_metric("nada", True))  # métrica desconhecida é ignorada
    assert s.pinned_metrics() == ["cpu", "ram"]
    s = s.copy(pinned_metrics_json=s.with_metric("cpu", False))
    assert s.pinned_metrics() == ["ram"]
    assert Settings(pinned_metrics_json="{quebrado").pinned_metrics() == []


# ---------- Sistema ----------
def test_hardware_math_and_formatting():
    assert hardware.cpu_percent((100, 300, 100), (150, 400, 200)) == pytest.approx(75.0)
    assert hardware.cpu_percent(None, (1, 2, 3)) is None
    assert hardware.rate(1000, 3000, 2.0) == 1000 and hardware.rate(5000, 10, 1.0) is None
    assert hardware.fmt_rate(820 * 1024) == "820 KB/s"
    assert hardware.fmt_rate(12.4 * 1024**2) == "12,4 MB/s"
    assert hardware.fmt_rate(None) == "—"
    assert hardware.fmt_gb(8 * 1024**3) == "8,0 GB"
    assert hardware.top({"a": 1, "b": 5, "c": 3, "d": 0}, 2) == [("b", 5), ("c", 3)]
    engines = {
        "pid_10_luid_0x0_0x1_phys_0_eng_0_engtype_3D": 40.0,
        "pid_11_luid_0x0_0x1_phys_0_eng_0_engtype_3D": 35.0,
        "pid_12_luid_0x0_0x9_phys_0_eng_0_engtype_3D": 10.0,
    }
    assert hardware.gpu_utilization(engines) == 75.0  # soma por GPU, fica com a mais usada
    assert hardware.gpu_utilization({}) is None
    assert hardware.thermal_celsius([300.95, 345.15]) == pytest.approx(72.0)  # 27,8 °C fixo é ignorado
    assert hardware.thermal_celsius([300.0]) is None


def test_pill_texts_merge_gpu_and_temperature():
    snap = Snapshot(
        cpu=22.4,
        gpu=Gpu(util=75, temp=64),
        ram_used=58,
        ram_total=100,
        net_down=12.4 * 1024**2,
        net_up=300 * 1024,
        ping=18,
        battery=Battery(82, charging=True, plugged=True),
    )
    parts = hardware.pill_parts(["cpu", "gpu", "gpu_temp", "ram", "net_down", "ping", "battery"], snap)
    assert parts == ["CPU 22%", "GPU 75% · 64°", "RAM 58%", "↓ 12,4 MB/s", "18 ms", "BAT 82% ⚡"]
    assert hardware.pill_parts(["gpu_temp"], snap) == ["GPU 64°"]
    assert hardware.pill_parts(["net_up"], None) == ["↑ —"]
    assert hardware.pill_parts(["cpu", "net_down"], None, template=True) == ["CPU 100%", "↓ 99,9 MB/s"]
    assert snap.value("ram") == pytest.approx(58) and snap.value("battery") == 82


def test_hardware_monitor_alerts_and_timers(qapp):
    pinned: list[str] = []
    runs = []
    monitor = hardware.HardwareMonitor(lambda *a, **k: runs.append(a), lambda: pinned, lambda: True)
    seen = []
    monitor.alert.connect(lambda kind, title, text: seen.append((kind, text)))
    hot = Snapshot(gpu=Gpu(temp=91), ram_used=95, ram_total=100, cpu_temp=70)
    assert monitor.check_alerts(hot, now=1000) == ["gpu_temp", "ram"]
    assert monitor.check_alerts(hot, now=1200) == []  # espera 10 min para repetir
    assert monitor.check_alerts(hot, now=1000 + hardware.ALERT_COOLDOWN + 1) == ["gpu_temp", "ram"]
    assert seen[0] == ("gpu_temp", "91 °C na placa de vídeo")
    monitor.sync()
    assert not monitor.timer.isActive() and not monitor.slow.isActive()  # sem start(): nada roda
    monitor.start()
    assert monitor.slow.isActive() and not monitor.timer.isActive()  # só a leitura leve dos avisos
    pinned.append("cpu")
    monitor.sync()
    assert monitor.timer.isActive() and not monitor.slow.isActive()
    monitor.stop()
    assert not monitor.timer.isActive()
    monitor._done(Snapshot(cpu=10.0, disk_total=1.0))
    assert list(monitor.history["cpu"]) == [10.0] and monitor.last.cpu == 10.0


@pytest.mark.skipif(sys.platform != "win32", reason="leituras nativas do Windows")
def test_native_sampler_reads_this_pc():
    sampler = hardware.Sampler()
    sampler.sample(detail=True)
    time.sleep(0.3)
    snap = sampler.sample(detail=True)
    assert snap.cores >= 1 and snap.ram_total > 0 and 0 <= (snap.ram_pct or 0) <= 100
    assert snap.cpu is not None and 0 <= snap.cpu <= 100
    assert snap.disk_total > 0
    assert hardware.network_totals() is not None  # MIB_IF_ROW2 com o tamanho certo


# ---------- áudio ----------
def test_audio_helpers():
    assert device_kind("Alto-falantes (Redragon Gaming Headset)", "render") == "headphones"
    assert device_kind("3 - TS35505 (AMD High Definition Audio Device)", "render") == "hdmi"
    assert device_kind("Fone (WH-1000XM4 Hands-Free)", "render") == "headphones"
    assert device_kind("Galaxy Buds Bluetooth", "render") == "headphones"
    assert device_kind("JBL Bluetooth", "render") == "bluetooth"
    assert device_kind("Alto-falantes (Realtek)", "render") == "speaker"
    assert device_kind("Microfone (USB)", "capture") == "mic"
    assert short_device_name("Alto-falantes (Realtek(R) Audio)") == "Alto-falantes · Realtek(R) Audio"
    raw = [
        {"pid": 1, "path": r"C:\Apps\Chrome.exe", "level": 0.4, "muted": False, "state": 1},
        {"pid": 2, "path": r"C:\Apps\Chrome.exe", "level": 0.8, "muted": False, "state": 0},
        {"pid": 3, "path": r"C:\Apps\Old.exe", "level": 1.0, "muted": True, "state": 2},  # expirada
        {"pid": 4, "path": "", "level": 1.0, "muted": True, "state": 0, "system": True},
    ]
    sessions = group_sessions(raw)
    assert [s.key for s in sessions] == [r"c:\apps\chrome.exe", "system"]
    chrome, system_sounds = sessions
    assert chrome.pids == (1, 2) and chrome.level == 0.8 and chrome.active and not chrome.muted
    assert system_sounds.name == "Sons do sistema" and system_sounds.muted


class FakeAudio:
    def __init__(self):
        self.calls = []
        self.level = 0.5

    def read_state(self):
        return AudioState(outputs=(Device("a", "Caixas", "render", True),), master=self.level)

    def set_endpoint_volume(self, flow, level=None, mute=None):
        self.calls.append(("endpoint", flow, level, mute))

    def step_master(self, delta):
        self.level = round(self.level + delta, 2)
        self.calls.append(("step", round(delta, 2)))
        return self.level, False

    def toggle_mic_mute(self):
        self.calls.append(("mic",))
        return True

    def set_session(self, key, level=None, mute=None):
        self.calls.append(("session", key, level, mute))

    def set_default(self, device_id):
        self.calls.append(("default", device_id))


def test_audio_service_runs_commands_in_its_thread(qapp):
    backend = FakeAudio()
    service = AudioService(backend)
    states, volumes, mics = [], [], []
    service.changed.connect(states.append)
    service.master_feedback.connect(lambda level, muted: volumes.append(level))
    service.mic_feedback.connect(mics.append)
    service.set_master(0.3)  # sem start(): só fica na fila
    assert backend.calls == []
    service.start()
    try:
        assert service.running
        assert wait(qapp, lambda: ("endpoint", 0, 0.3, None) in backend.calls and states)
        service.step_master(True, 2)
        service.toggle_mic_mute()
        service.set_default("b")
        service.set_session("x.exe", 0.2)
        assert wait(qapp, lambda: volumes and mics and ("default", "b") in backend.calls)
        assert ("step", 0.04) in backend.calls and volumes[-1] == pytest.approx(0.54)
        assert mics == [True] and ("session", "x.exe", 0.2, None) in backend.calls
        assert states[-1].default_output.name == "Caixas"
    finally:
        service.stop()
    assert not service.running


# ---------- privacidade ----------
def test_privacy_reads_who_is_using_mic_and_camera():
    assert privacy.packaged_name("Microsoft.WindowsCamera_8wekyb3d8bbwe") == "Windows Camera"
    assert privacy.desktop_name(r"C:#Nao#Existe#Discord.exe") == "Discord"
    assert privacy.in_use({"LastUsedTimeStart": 5, "LastUsedTimeStop": 0})
    assert not privacy.in_use({"LastUsedTimeStart": 5, "LastUsedTimeStop": 9})

    def entries(capability):
        if capability == "microphone":
            yield "C:#Apps#Discord.exe", True, {"LastUsedTimeStart": 10, "LastUsedTimeStop": 0}
            yield "Microsoft.SoundRecorder_x", False, {"LastUsedTimeStart": 10, "LastUsedTimeStop": 20}
        else:
            yield "Microsoft.WindowsCamera_x", False, {"LastUsedTimeStart": 10, "LastUsedTimeStop": 0}

    state = privacy.read_state(entries)
    assert state.mic == ("Discord",) and state.cam == ("Windows Camera",)
    assert state.text("mic") == "Microfone em uso por Discord"
    assert privacy.PrivacyState().text("cam") == ""


@pytest.mark.skipif(sys.platform != "win32", reason="registro do Windows")
def test_privacy_registry_read_does_not_fail():
    state = privacy.read_state()
    assert isinstance(state.mic, tuple) and isinstance(state.cam, tuple)


# ---------- cafeína ----------
def test_caffeine_keeps_pc_awake_and_stops(qapp):
    calls = []
    service = CaffeineService(apply=lambda flags: calls.append(flags) or True)
    changes = []
    service.changed.connect(changes.append)
    service.start(1800)
    assert service.active and calls == [AWAKE] and service.until is not None
    assert service.describe().startswith("Cafeína: tela sempre acesa até")
    service.stop()
    assert not service.active and calls[-1] == ES_CONTINUOUS and changes == [True, False]
    service.start(0.05)
    assert wait(qapp, lambda: not service.active, 2)  # desliga sozinha no tempo
    service.toggle()
    assert service.active and service.until is None
    service.toggle()
    assert not service.active


# ---------- estante ----------
def test_clipboard_history_classifies_trims_and_skips_passwords(qapp, store):
    from PySide6.QtCore import QMimeData
    from PySide6.QtGui import QColor, QImage

    from ventude.services.shelf import CLIP_LIMIT, ShelfService, classify_text

    assert classify_text(" #a1b2c3 ")[:2] == ("color", "#A1B2C3")
    assert classify_text("https://github.com/x/y")[0] == "link"
    assert classify_text("oi\nmundo")[2] == "oi"
    shelf = ShelfService(store)

    def mime(text):
        data = QMimeData()
        data.setText(text)
        return data

    assert (
        shelf.capture(mime("#FF8800")) and shelf.items[0].kind == "color" and shelf.items[0].source == "clip"
    )
    secret = mime("senha-super-secreta")
    secret.setData('application/x-qt-windows-mime;value="ExcludeClipboardContentFromMonitorProcessing"', b"1")
    assert not shelf.capture(secret)
    allowed = mime("pode guardar")
    allowed.setData('application/x-qt-windows-mime;value="CanIncludeInClipboardHistory"', b"\x01\x00\x00\x00")
    assert shelf.capture(allowed)
    blocked = mime("não guarde")
    blocked.setData('application/x-qt-windows-mime;value="CanIncludeInClipboardHistory"', b"\x00\x00\x00\x00")
    assert not shelf.capture(blocked)
    for n in range(CLIP_LIMIT + 5):
        shelf.capture(mime(f"texto {n}"))
    assert sum(1 for i in shelf.items if i.source == "clip") == CLIP_LIMIT
    assert shelf.dropped_count == 0  # o histórico não conta na pílula
    shelf.capture(mime("texto 10"))  # copiou de novo: volta para o topo
    assert shelf.items[0].value == "texto 10"
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor("#123456"))
    data = QMimeData()
    data.setImageData(image)
    assert shelf.capture(data, image) and shelf.items[0].kind == "image" and shelf.items[0].temporary
    assert not shelf.add_image(image, "clip") or shelf.items[0].kind == "image"


def test_pinned_shelf_items_survive_restart(qapp, store, tmp_path):
    from PySide6.QtGui import QColor, QImage

    from ventude.services.shelf import ShelfService, pinned_dir

    shelf = ShelfService(store)
    shelf.add_text("guarde isto")
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(QColor("#00FF00"))
    shelf.add_image(image)
    text_item = next(i for i in shelf.items if i.kind == "text")
    image_item = next(i for i in shelf.items if i.kind == "image")
    shelf.pin(text_item.id)
    pinned_image = shelf.pin(image_item.id)
    assert pinned_image.pinned and Path(pinned_image.value).parent == pinned_dir()
    assert Path(pinned_image.value).exists() and not pinned_image.temporary
    shelf.add_text("temporário")
    shelf.clear()  # limpa só o que não está fixado
    assert {i.value for i in shelf.items} == {"guarde isto", pinned_image.value}
    again = ShelfService(store)  # "reabriu" o Ventude
    assert {(i.kind, i.pinned) for i in again.items} == {("text", True), ("image", True)}
    again.remove(next(i for i in again.items if i.kind == "image").id)
    assert not Path(pinned_image.value).exists()
    assert [v for _, v, _, _ in store.shelf_pins()] == ["guarde isto"]
    unpinned = again.pin(again.items[0].id, False)
    assert not unpinned.pinned and store.shelf_pins() == []


# ---------- QR Code ----------
def test_qr_matches_the_standard_vectors():
    data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236]  # "HELLO WORLD" 1-Q
    assert qr.rs_remainder(data, qr.rs_divisor(13)) == [
        168,
        72,
        22,
        82,
        217,
        54,
        156,
        0,
        46,
        15,
        180,
        122,
        16,
    ]
    assert format(qr.format_bits(1, 0), "015b") == "101010000010010"  # M, máscara 0
    assert format(qr.format_bits(0, 0), "015b") == "111011111000100"  # L, máscara 0
    assert format(qr.version_bits(7), "018b") == "000111110010010100"
    assert qr.alignment_positions(7) == [6, 22, 38]
    assert qr.alignment_positions(32) == [6, 34, 60, 86, 112, 138]
    assert qr.data_codewords(1, 1) == 16 and qr.data_codewords(40, 1) == 2334
    with pytest.raises(qr.QrError):
        qr.encode("x" * 3000)


def _read_format(matrix):
    bits = 0
    for i in range(6):
        bits |= matrix[i][8] << i
    bits |= matrix[7][8] << 6
    bits |= matrix[8][8] << 7
    bits |= matrix[8][7] << 8
    for i in range(9, 15):
        bits |= matrix[8][14 - i] << i
    return bits


@pytest.mark.parametrize("text", ["https://github.com", "Olá, mundo! ção", "x" * 200])
def test_qr_round_trip_reads_back_the_codewords(text):
    matrix = qr.encode(text)
    size = len(matrix)
    version = (size - 17) // 4
    data = text.encode()
    assert version == qr.choose_version(len(data), qr.LEVELS["M"]) or version >= 1
    fmt = _read_format(matrix)
    level, mask = next((lv, m) for lv in range(4) for m in range(8) if qr.format_bits(lv, m) == fmt)
    expected = qr.add_ecc_and_interleave(qr.encode_codewords(data, version, level), version, level)
    reference = qr._Matrix(version)
    reference.draw_patterns(level)
    rule = qr.MASKS[mask]
    bits = [int(matrix[y][x]) ^ int(rule(x, y) == 0) for x, y in reference.zigzag()]
    read = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(expected) * 8, 8)]
    assert read == expected
    for x, y in ((0, 0), (size - 7, 0), (0, size - 7)):  # cantos dos três padrões de localização
        assert matrix[y][x] and matrix[y + 6][x + 6] and not matrix[y + 1][x + 1]


# ---------- letras ----------
def test_lyrics_parse_and_sync():
    lines = parse_lrc("[00:01.00] Primeira\n[00:05.50][00:20.00] Refrão\nsem tempo\n[01:02.25] Fim")
    assert lines == ((1.0, "Primeira"), (5.5, "Refrão"), (20.0, "Refrão"), (62.25, "Fim"))
    lyrics = Lyrics(lines)
    assert lyrics.at(0.2) == ("", "Primeira")
    assert lyrics.at(6.0) == ("Refrão", "Refrão")
    assert lyrics.at(100) == ("Fim", "")
    assert from_json({"syncedLyrics": "", "plainLyrics": "", "instrumental": False}) is None
    assert from_json({"instrumental": True}).instrumental


def test_lyrics_service_caches_and_stays_offline_until_started(qapp, store):
    calls = []

    def fetcher(title, artist, album, duration):
        calls.append(title)
        return Lyrics(((1.0, "verso"),)) if title == "Tem" else None

    service = LyricsService(store, lambda fn, *a, done, fail, owner: done(fn(*a)), fetcher)
    assert service.lookup("Tem", "Banda") is None and calls == []  # sem start(): sem internet
    service.start()
    ready = []
    service.ready.connect(lambda key, found: ready.append(found))
    assert service.lookup("Tem", "Banda", duration=180) is False
    assert ready[-1].lines == ((1.0, "verso"),)
    cached = service.lookup("Tem", "Banda")
    assert cached.lines[0][1] == "verso" and calls == ["Tem"]  # veio do cache
    service.lookup("Sem", "Banda")
    assert service.lookup("Sem", "Banda") is None and calls == ["Tem", "Sem"]  # "sem letra" também fica


def test_lyrics_fetch_uses_get_then_search(monkeypatch):
    from ventude.core import net
    from ventude.services import lyrics

    seen = []

    def fake_get_json(url, params=None, timeout=0):
        seen.append(url.rsplit("/", 1)[-1])
        if url.endswith("/get"):
            raise net.NetError("HTTP 404", 404)
        return [
            {"duration": 300, "syncedLyrics": "[00:01.00] longe"},
            {"duration": 181, "syncedLyrics": "[00:02.00] perto"},
        ]

    monkeypatch.setattr(net, "get_json", fake_get_json)
    found = lyrics.fetch("Música", "Banda", "", 180)
    assert seen == ["get", "search"] and found.lines == ((2.0, "perto"),)


# ---------- barra de tarefas ----------
def test_taskbar_hide_restore_and_crash_recovery(tmp_path):
    shown = []
    areas = []
    screens = [((0, 0, 1920, 1080), (0, 0, 1920, 1032)), ((1920, 0, 3840, 1080), (1920, 0, 3840, 1080))]
    marker = tmp_path / "marcador.json"
    hider = TaskbarHider(
        windows=lambda: [11, 22],
        show=lambda hwnd, cmd: shown.append((hwnd, cmd)),
        screens=lambda: screens,
        work_area=lambda rect: areas.append(rect) or True,
        marker=marker,
    )
    assert hider.hide() and hider.hidden and marker.exists()
    assert shown == [(11, SW_HIDE), (22, SW_HIDE)]
    assert areas == [(0, 0, 1920, 1080), (1920, 0, 3840, 1080)]  # área útil vira a tela toda
    shown.clear()
    areas.clear()
    crashed = TaskbarHider(  # outra abertura do Ventude depois de um travamento
        windows=lambda: [11, 22],
        show=lambda hwnd, cmd: shown.append((hwnd, cmd)),
        screens=lambda: screens,
        work_area=lambda rect: areas.append(rect) or True,
        marker=marker,
    )
    assert crashed.recover()
    assert shown == [(11, SW_SHOW), (22, SW_SHOW)] and areas == [(0, 0, 1920, 1032)]
    assert not marker.exists() and not crashed.recover()
    hider.restore()
    hider.restore()  # seguro chamar de novo
    assert not hider.hidden
    no_bar = TaskbarHider(windows=lambda: [], show=lambda h, c: None, screens=lambda: [], marker=marker)
    assert not no_bar.hide() and not no_bar.hidden


def test_app_window_rules_and_dock_grouping():
    assert is_app_window(True, "Doc", 0, 0, False, "Notepad", 10, 1)
    assert not is_app_window(True, "", 0, 0, False, "Notepad", 10, 1)  # sem título
    assert not is_app_window(True, "Dica", 0, 0x80, False, "X", 10, 1)  # janela de ferramenta
    assert is_app_window(True, "App", 0, 0x80 | 0x40000, False, "X", 10, 1)  # ... marcada como app
    assert not is_app_window(True, "Doc", 0, 0, True, "X", 10, 1)  # "cloaked" (outra área de trabalho)
    assert not is_app_window(True, "Ventude", 0, 0, False, "X", 1, 1)  # o próprio Ventude
    assert not is_app_window(True, "Doc", 99, 0, False, "X", 10, 1)  # tem dono (diálogo)
    from types import SimpleNamespace

    from ventude.ui.dock import group_windows

    pins = [SimpleNamespace(id=1, kind="app", label="Code", target=r"C:\Apps\Code.exe")]
    windows = [
        WindowInfo(100, "projeto - Code", 5, r"C:\Apps\Code.exe", foreground=True),
        WindowInfo(101, "outro - Code", 6, r"c:\apps\code.exe"),
        WindowInfo(200, "Firefox", 7, r"C:\Apps\Firefox.exe"),
    ]
    items = group_windows(pins, windows)
    assert [i.label for i in items][0] == "Code" and len(items) == 2
    assert items[0].pinned and items[0].active and len(items[0].windows) == 2
    assert not items[1].pinned and items[1].windows[0].hwnd == 200


# ---------- tecla Windows ----------
def test_win_key_logic():
    logic = WinKeyLogic()
    assert logic.feed(VK_LWIN, True) == (PASS, "")
    assert logic.feed(VK_LWIN, False) == (BLOCK, "start")  # sozinha: abre o Ventude, sem Iniciar
    logic.feed(VK_LWIN, True)
    assert logic.feed(VK_S, True) == (BLOCK, "search")
    assert logic.feed(VK_S, True) == (BLOCK, "")  # repetição não abre de novo
    assert logic.feed(VK_S, False) == (BLOCK, "")
    assert logic.feed(VK_LWIN, False) == (BLOCK, "mask")  # o S foi engolido: máscara para o Iniciar
    logic.feed(VK_LWIN, True)
    assert logic.feed(0x45, True) == (PASS, "")  # Win + E: do Windows
    assert logic.feed(VK_LWIN, False) == (PASS, "")
    logic.feed(VK_LWIN, True)
    logic.feed(0xA0, True)  # Shift
    assert logic.feed(VK_S, True) == (PASS, "")  # Win + Shift + S: captura de tela do Windows
    logic.feed(0xA0, False)
    assert logic.feed(VK_LWIN, False) == (PASS, "")
    logic.feed(0xA2, True)  # Ctrl antes do Win
    logic.feed(VK_LWIN, True)
    assert logic.feed(VK_LWIN, False) == (PASS, "")
    logic.feed(0xA2, False)
    assert logic.feed(VK_LWIN, True, injected=True) == (PASS, "")  # injetada: nem conta
    assert logic.feed(VK_S, True) == (PASS, "")  # sem Win de verdade, o S é só um S


# ---------- calculadora ----------
def test_calculator():
    assert calc.calculate("1450 * 5.85") == "8482.5"
    assert calc.calculate("1450 * 5,85") == "8482,5"
    assert calc.calculate("1.450,50 + 1") == "1451,5"
    assert calc.calculate("2^10") == "1024"
    assert calc.calculate("raiz(16) + 2 * (3 - 1)") == "8"
    assert calc.calculate("10 / 4") == "2.5"
    assert calc.calculate("pi * 2").startswith("6.283185")
    for text in ("github", "t: tarefa", "timer 15m", "42", "", "1 / 0", "9 ** 9 ** 9", "__import__('os')"):
        assert calc.calculate(text) is None, text
    with pytest.raises(calc.CalcError):
        calc.evaluate("10 ** 100000")


# ---------- índice da busca ----------
def test_indexer_scans_and_ranks(tmp_path):
    home = tmp_path / "casa"
    (home / "Desktop" / "Projetos" / "ventude" / "src").mkdir(parents=True)
    (home / "Desktop" / "Projetos" / "node_modules" / "lixo").mkdir(parents=True)
    (home / "Documents" / "Projetos antigos").mkdir(parents=True)
    (home / "Desktop" / "Projetos" / "notas.txt").write_text("x")
    (home / "Desktop" / ".git").mkdir()
    entries = indexer.scan_files([(home / "Desktop", 2), (home / "Documents", 1), (home, 2)])
    names = {e.name for e in entries}
    assert {"Projetos", "ventude", "notas.txt", "Projetos antigos"} <= names
    assert "node_modules" not in names and ".git" not in names and "src" not in names  # profundidade 2
    assert len({e.path.lower() for e in entries}) == len(entries)  # sem repetir (home inclui Desktop)
    menu = tmp_path / "menu"
    (menu / "Pasta").mkdir(parents=True)
    (menu / "Pasta" / "Visual Studio Code.lnk").write_bytes(b"")
    (menu / "Uninstall Code.lnk").write_bytes(b"")
    apps = indexer.scan_apps([menu])
    assert [a.name for a in apps] == ["Visual Studio Code"]
    items = apps + entries
    assert indexer.search(items, "proj")[0].name == "Projetos"  # começa com: na frente
    assert indexer.search(items, "code")[0].kind == "app"
    assert all(e.is_folder for e in indexer.search(items, "projetos", folders_only=True))
    assert indexer.parse_query("/p projetos") == ("projetos", True)
    assert indexer.parse_query("pasta: fotos") == ("fotos", True)
    assert indexer.parse_query("fotos") == ("fotos", False)
    assert indexer.search(items, "") == []


# ---------- energia ----------
def test_power_detection_and_matching(qapp, monkeypatch):
    assert power.update_pending(lambda key: key.endswith("RebootRequired"))
    assert not power.update_pending(lambda key: False)
    assert [a.key for a in power.matching("reinic")] == ["restart", "update_restart"]
    assert [a.key for a in power.matching("bloq")] == ["lock"]
    assert {a.key for a in power.matching("atualizar")} == {"update_restart", "update_shutdown"}
    assert power.matching("ab") == [] and len(power.matching("energia")) == len(power.ACTIONS)
    service = power.PowerService(check=lambda: True)
    changes = []
    service.changed.connect(changes.append)
    service.refresh()
    assert service.pending and changes == [True]
    assert any(a.needs_update for a in service.available())
    service._check = lambda: False
    service.refresh()
    assert not any(a.needs_update for a in service.available())
    monkeypatch.setattr(power.sys, "platform", "linux")
    assert power.run("restart") == (False, "Só funciona no Windows.")


def test_power_menu_asks_twice_for_destructive_actions(qapp):
    from ventude.ui.power_menu import PowerMenu

    ran = []
    menu = PowerMenu(list(power.ACTIONS), runner=lambda key: ran.append(key) or (True, ""))
    menu.choose("restart")
    assert menu.armed == "restart" and ran == [] and "clique de novo" in menu.buttons["restart"].text()
    menu.choose("shutdown")  # trocou de ideia: desarma o anterior e arma o novo
    assert menu.armed == "shutdown" and menu.buttons["restart"].text().strip() == "Reiniciar"
    menu.choose("shutdown")
    assert wait(qapp, lambda: ran == ["shutdown"])
    menu.choose("lock")  # não destrutivo: na hora
    assert wait(qapp, lambda: ran == ["shutdown", "lock"])


# ---------- utilitários ----------
def test_lnk_target_and_app_name(tmp_path):
    target = b"C:\\Program Files\\App\\app.exe\x00"
    header = bytearray(0x4C)
    header[0:4] = b"\x4c\x00\x00\x00"
    header[0x14:0x18] = (0x2).to_bytes(4, "little")  # só HasLinkInfo
    info_header = 0x1C
    info = bytearray(info_header)
    info[0:4] = (info_header + len(target) + 1).to_bytes(4, "little")
    info[4:8] = info_header.to_bytes(4, "little")
    info[8:12] = (1).to_bytes(4, "little")  # VolumeIDAndLocalBasePath
    info[16:20] = info_header.to_bytes(4, "little")  # LocalBasePathOffset
    info[24:28] = (info_header + len(target)).to_bytes(4, "little")  # sufixo vazio
    link = tmp_path / "App.lnk"
    link.write_bytes(bytes(header) + bytes(info) + target + b"\x00")
    assert lnk_target(link) == r"C:\Program Files\App\app.exe"
    assert lnk_target(tmp_path / "nao-existe.lnk") == ""
    (tmp_path / "ruim.lnk").write_bytes(b"lixo")
    assert lnk_target(tmp_path / "ruim.lnk") == ""
    assert app_name(r"C:\nao\existe\spotify.exe") == "Spotify"
