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
    assert areas == [(0, 0, 1920, 1080)]  # sem reserva: a área útil vira a tela toda (só onde mudou)
    areas.clear()
    hider.reserve = lambda mon: 60  # barra do Ventude com 48 px a 125%
    hider.apply_work_areas()
    assert areas == [(0, 0, 1920, 1020), (1920, 0, 3840, 1020)]  # janelas maximizadas não cobrem a barra
    hider.peek(True)
    assert shown[-2:] == [(11, SW_SHOW), (22, SW_SHOW)] and hider.peeking
    hider.reassert()  # mostrando por um tempo: não esconde de novo
    assert shown[-1] == (22, SW_SHOW)
    hider.peek(False)
    assert shown[-1] == (22, SW_HIDE) and not hider.peeking
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
    from ventude.core.store import Pin
    from ventude.ui.shell.items import build_items, store_families

    claude_exe = r"C:\Program Files\WindowsApps\Claude_6.6395.0.0_x64__pzs8sxrjxfjjc\app\claude.exe"
    pins = [
        Pin(1, "taskbar", "app", "Code", r"C:\Apps\Code.exe", 0),
        Pin(2, "taskbar", "uwp", "Calculadora", "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App", 1),
    ]
    windows = [
        WindowInfo(100, "projeto - Code", 5, r"C:\Apps\Code.exe", foreground=True),
        WindowInfo(101, "outro - Code", 6, r"c:\apps\code.exe"),
        WindowInfo(200, "Firefox", 7, r"C:\Apps\Firefox.exe", app_id="308046B0AF4A39CB"),
        WindowInfo(201, "Outra aba", 7, r"C:\Apps\Firefox.exe", app_id="308046B0AF4A39CB"),
        WindowInfo(
            300,
            "Calculadora",
            8,
            r"C:\Windows\calc.exe",
            app_id="Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
        ),
        WindowInfo(400, "Claude", 9, claude_exe),
    ]
    store_apps = [indexer.entry("Claude", "Claude_pzs8sxrjxfjjc!Claude", "uwp")]
    items = build_items(pins, windows, store_families(store_apps))
    assert [i.label for i in items][:2] == ["Code", "Calculadora"] and len(items) == 4
    code, calculator, firefox, claude = items
    assert code.pinned and code.active and len(code.windows) == 2  # mesmo .exe, maiúsculas diferentes
    assert calculator.pinned and calculator.windows[0].hwnd == 300  # casou pelo AppUserModelID
    assert not firefox.pinned and [w.hwnd for w in firefox.windows] == [200, 201]
    assert (
        claude.kind == "uwp" and claude.icon_target == "Claude_pzs8sxrjxfjjc!Claude"
    )  # app da Loja: ícone real
    from ventude.services import apps

    assert apps.package_family(claude_exe) == "Claude_pzs8sxrjxfjjc"
    assert apps.package_family(r"C:\Apps\Code.exe") == ""


# ---------- Ventude Shell: catálogo, fixados e histórico ----------
def test_app_categories_seeds_and_system_items():
    from ventude.services import apps

    assert apps.category("Visual Studio Code") == "Desenvolvimento"
    assert apps.category("Claude") == "Inteligência Artificial"
    assert apps.category("Antigravity") == "Inteligência Artificial"
    assert apps.category("Microsoft Edge") == "Navegadores"
    assert apps.category("WhatsApp") == "Comunicação"
    assert apps.category("League of Legends") == "Jogos"
    assert apps.category("Spotify") == "Entretenimento"
    assert apps.category("Paint") == "Criatividade"
    assert apps.category("Calculadora") == "Utilitários e Ferramentas"
    assert apps.category("Bloco de Notas") == "Produtividade"
    assert apps.category("Codex") == "Inteligência Artificial"  # "code" não pega "codex"
    assert apps.category("Xyzzy") == apps.OTHER
    names = ["Spotify", "Calculadora", "Firefox", "Steam", "Paint", "Configurações", "Algo"]
    assert apps.pick_start_seeds(names) == [
        "Firefox",
        "Configurações",
        "Calculadora",
        "Spotify",
        "Steam",
        "Paint",
    ]
    assert [i.title for i in apps.match_system("wi")][0] == "Wi-Fi"
    assert apps.match_system("lixeira")[0].target == "shell:RecycleBinFolder"
    assert apps.match_system("x") == []
    assert (
        apps.resolve_known_folder(r"{F38BF404-1D43-42F2-9305-67DE0B28FC23}\explorer.exe")
        .lower()
        .endswith(r"windows\explorer.exe")
    )
    assert apps.first_name("Fulano de Tal") == "Fulano" and apps.first_name("") == "Você"


def test_relative_times_like_windows():
    from datetime import datetime

    from ventude.services import apps

    now = datetime(2026, 11, 20, 10, 29).timestamp()
    assert apps.relative_time(now - 20, now) == "agora"
    assert apps.relative_time(now - 60, now) == "há 1 minuto"
    assert apps.relative_time(now - 5 * 60, now) == "há 5 minutos"
    assert apps.relative_time(now - 3 * 3600, now) == "há 3 horas"
    assert apps.relative_time(datetime(2026, 11, 19, 10, 12).timestamp(), now) == "Ontem às 10:12"
    assert apps.relative_time(datetime(2026, 11, 11, 9, 0).timestamp(), now) == "há 9 dias"


def test_pins_and_launch_history(store):
    first = store.add_pin("taskbar", "app", "Code", r"C:\Apps\Code.exe")
    assert (
        first is not None and store.add_pin("taskbar", "app", "Code", r"c:\apps\code.exe") is None
    )  # repetido
    store.add_pin("taskbar", "uwp", "Calculadora", "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App")
    store.add_pin("start", "app", "Code", r"C:\Apps\Code.exe")  # outro lugar: pode
    assert store.add_pin("nenhum", "app", "X", "y") is None
    assert [p.label for p in store.pins("taskbar")] == ["Code", "Calculadora"]
    calc_pin = store.pins("taskbar")[1]
    store.move_pin_first(calc_pin.id)
    assert [p.label for p in store.pins("taskbar")] == ["Calculadora", "Code"]
    store.add_pin("taskbar", "folder", "Downloads", r"C:\Users\x\Downloads", first=True)
    assert store.pins("taskbar")[0].label == "Downloads"
    store.remove_pin(first.id)
    assert store.pinned("taskbar", r"C:\Apps\Code.exe") is None and store.pinned("start", r"C:\Apps\Code.exe")
    store.record_launch("app", "Code", r"C:\Apps\Code.exe", ts=100)
    store.record_launch("system", "Wi-Fi", "ms-settings:network-wifi", ts=200)
    store.record_launch("app", "Code", r"C:\Apps\Code.exe", ts=300)
    launches = store.launches()
    assert [(l.label, l.count) for l in launches] == [("Code", 2), ("Wi-Fi", 1)]  # noqa: E741
    store.forget_launch("ms-settings:network-wifi")
    assert len(store.launches()) == 1
    for index in range(80):
        store.record_launch("file", f"f{index}", f"C:\\f{index}", ts=1000 + index)
    assert len(store.launches(100)) == 60  # guarda só os 60 mais recentes
    store.clear_launches()
    assert store.launches() == []


def test_store_apps_from_start_menu_list():
    rows = [
        {"Name": "Calculadora", "AppID": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
        {"Name": "Explorador de Arquivos", "AppID": "Microsoft.Windows.Explorer"},
        {"Name": "Code", "AppID": "Microsoft.VisualStudioCode"},  # já veio por .lnk
        {"Name": "Site", "AppID": "https://example.com"},
        {"Name": "Uninstall App", "AppID": "{GUID}\\uninst.exe"},
        {"Name": "Calculadora", "AppID": "duplicado"},
    ]
    names = [(e.name, e.kind) for e in indexer.store_entries(rows, {"code"})]
    assert names == [("Calculadora", "uwp"), ("Explorador de Arquivos", "uwp")]


def test_recent_files_reads_windows_recent_links(tmp_path):
    from ventude.services import apps

    target = tmp_path / "Relatório.docx"
    target.write_text("x")
    recent = tmp_path / "Recent"
    recent.mkdir()
    link = recent / "Relatório.docx.lnk"
    link.write_bytes(b"")
    (recent / "ms-screenclip---x.lnk").write_bytes(b"")
    import ventude.services.winutil as winutil

    original = winutil.lnk_target
    winutil.lnk_target = lambda path: str(target) if "Relat" in str(path) else ""
    try:
        files = apps.recent_files(5, recent)
    finally:
        winutil.lnk_target = original
    assert [(f.name, f.is_dir) for f in files] == [("Relatório.docx", False)]


def test_shell_icons_without_windows_fall_back(qapp):
    from PySide6.QtGui import QPixmap

    from ventude.services import shellicons

    assert shellicons.parsing_name("uwp", "App!Id") == "shell:AppsFolder\\App!Id"
    assert shellicons.parsing_name("url", "https://x") == ""
    assert shellicons.parsing_name("app", r"C:\a.exe") == r"C:\a.exe"
    cache = shellicons.IconCache()
    cache.enabled = False
    assert cache.pixmap(r"C:\a.exe", 24) is None
    cache.enabled = True
    seen = []
    cache.changed.connect(seen.append)
    cache.put(r"C:\a.exe", 24, QPixmap(48, 48))
    assert (
        cache.ready(r"C:\A.EXE", 24) and cache.pixmap(r"C:\a.exe", 24) is not None and seen == [r"C:\a.exe"]
    )


def test_dwm_helpers_are_safe():
    from ventude.services import dwm

    assert dwm.fit((1920, 1080), (200, 120)) == (200, 112)
    assert dwm.fit((0, 0), (200, 120)) == (200, 120)
    assert dwm.register_thumbnail(0, 0) == 0 and not dwm.place_thumbnail(0, (0, 0, 1, 1))
    assert dwm._color_ref(0xB0101216) == 0xB0161210


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


# ---------- Ventude Shell 3.0 (serviços e modelos puros) ----------
def test_win_key_logic_v3_controls_and_dock_digits():
    from ventude.services.system import VK_A, VK_LWIN, VK_S

    # Tudo ligado
    logic = WinKeyLogic({"launcher", "controls", "slots"})
    logic.feed(VK_LWIN, True)
    assert logic.feed(VK_A, True) == (BLOCK, "controls")
    assert logic.feed(VK_A, False) == (BLOCK, "")
    assert logic.feed(VK_LWIN, False) == (BLOCK, "mask")

    # Multi-press Win + 1 -> Win + 2 -> Win + Shift + 3 sem soltar Win
    logic.feed(VK_LWIN, True)
    assert logic.feed(0x31, True) == (BLOCK, "slot:1")
    assert logic.feed(0x31, False) == (BLOCK, "")
    assert logic.feed(0x32, True) == (BLOCK, "slot:2")
    logic.feed(0xA0, True)  # Shift
    assert logic.feed(0x33, True) == (BLOCK, "slot_new:3")
    logic.feed(0xA0, False)
    assert logic.feed(0x32, False) == (BLOCK, "")
    assert logic.feed(0x33, False) == (BLOCK, "")
    assert logic.feed(VK_LWIN, False) == (BLOCK, "mask")

    # Ctrl + Win + 1 passa para o Windows
    logic.feed(VK_LWIN, True)
    logic.feed(0xA2, True)
    assert logic.feed(0x31, True) == (PASS, "")
    logic.feed(0x31, False)
    logic.feed(0xA2, False)
    logic.feed(VK_LWIN, False)

    # Features desligadas: só Win+S e controls
    partial = WinKeyLogic({"controls"})
    partial.feed(VK_LWIN, True)
    assert partial.feed(VK_S, True) == (PASS, "")
    assert partial.feed(VK_LWIN, False) == (PASS, "")
    partial.feed(VK_LWIN, True)
    assert partial.feed(VK_A, True) == (BLOCK, "controls")
    assert partial.feed(VK_LWIN, False) == (BLOCK, "mask")


def test_shell_events_classify_debounce_and_flash_limit(qapp):
    from ventude.services.shellhook import (
        HSHELL_FLASH,
        HSHELL_RUDEAPPACTIVATED,
        HSHELL_WINDOWACTIVATED,
        HSHELL_WINDOWCREATED,
        HSHELL_WINDOWDESTROYED,
        ShellEvents,
        classify_shell_event,
    )

    assert classify_shell_event(HSHELL_WINDOWCREATED, 10) == ("windows", 10)
    assert classify_shell_event(HSHELL_WINDOWDESTROYED, 11) == ("windows", 11)
    assert classify_shell_event(HSHELL_WINDOWACTIVATED, 12) == ("activated", 12)
    assert classify_shell_event(HSHELL_RUDEAPPACTIVATED, 13) == ("activated", 13)
    assert classify_shell_event(HSHELL_FLASH, 14) == ("flashed", 14)
    assert classify_shell_event(999, 14) is None

    now = [100.0]
    events = ShellEvents(debounce_ms=20, flash_cooldown_ms=1000, clock=lambda: now[0])
    got: list[tuple[str, int]] = []
    events.windows_changed.connect(lambda: got.append(("win", 0)))
    events.activated.connect(lambda h: got.append(("act", h)))
    events.flashed.connect(lambda h: got.append(("flash", h)))

    events.handle(HSHELL_WINDOWCREATED, 1)
    events.handle(HSHELL_WINDOWCREATED, 2)  # debounced em um único sinal
    events.handle(HSHELL_WINDOWACTIVATED, 99)
    events.handle(HSHELL_FLASH, 55)
    events.handle(HSHELL_FLASH, 55)  # rate-limited (dentro de 1s)
    now[0] = 101.2
    events.handle(HSHELL_FLASH, 55)  # agora passou 1s -> emite de novo
    assert wait(qapp, lambda: ("win", 0) in got)
    assert got.count(("win", 0)) == 1
    assert ("act", 99) in got
    assert got.count(("flash", 55)) == 2


def test_dock_model_layout_overflow_dodge_and_split_by_monitor():
    from ventude.core.store import Pin
    from ventude.ui.shell.dock_model import SlotModel, edge_reveal, should_dodge
    from ventude.ui.shell.items import TaskItem, provider_for_app, split_by_monitor

    assert provider_for_app("Claude Desktop", "claude.exe") == "claude"
    assert provider_for_app("Antigravity", "gemini") == "gemini"
    assert provider_for_app("Bloco de Notas", "notepad.exe") == ""

    model = SlotModel()
    items = [
        TaskItem(f"pin:{i}", f"App {i}", "app", f"app{i}.exe", "app", f"app{i}.exe", pin_id=i + 1)
        for i in range(18)
    ]
    lay_wide = model.layout(items[:4], window_w=1000, max_w=900)
    assert lay_wide.slot_size == 44 and lay_wide.overflow_count == 0 and len(lay_wide.slots) == 4
    lay_narrow = model.layout(items, window_w=460, max_w=440)
    assert lay_narrow.slot_size == 36 and lay_narrow.overflow_count > 0

    # Dodge math
    dock_rect = (700, 1018, 1220, 1070)
    above = (100, 100, 900, 1000)
    overlapping = (0, 0, 1920, 1080)
    assert not should_dodge(dock_rect, above, "esquivar")
    assert should_dodge(dock_rect, overlapping, "esquivar")
    assert not should_dodge(dock_rect, overlapping, "esquivar", menu_open=True)
    assert not should_dodge(dock_rect, overlapping, "sempre")
    assert should_dodge(dock_rect, overlapping, "sempre", fullscreen=True)
    assert should_dodge(dock_rect, None, "oculta")
    assert not should_dodge(dock_rect, None, "oculta", hover_reveal=True)
    assert edge_reveal(500, 1078, (0, 0, 1920, 1080))
    assert not edge_reveal(500, 900, (0, 0, 1920, 1080))

    mon1 = (0, 0, 1920, 1080)
    mon2 = (1920, 0, 3840, 1080)
    from ventude.ui.shell.items import build_items

    built = build_items(
        [Pin(1, "taskbar", "app", "Code", r"C:\Apps\Code.exe", 0)],
        [
            WindowInfo(1, "Repo", 10, r"C:\Apps\Code.exe", monitor=mon2),
            WindowInfo(2, "Terminal", 11, r"C:\Apps\wt.exe", monitor=mon1),
        ],
    )
    m1 = split_by_monitor(built, mon1)
    m2 = split_by_monitor(built, mon2)
    assert m1[0].other_monitor and [i.label for i in m1] == ["Code", "Wt"]
    assert not m2[0].other_monitor and [i.label for i in m2] == ["Code"]


def test_indexer_git_projects_antigravity_and_rename_pin(tmp_path, store, monkeypatch):
    repo = tmp_path / "root" / "meu-repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/feat/calma\n", encoding="utf-8")
    plain = tmp_path / "root" / "docs"
    plain.mkdir()
    entries = indexer.scan_files([(tmp_path / "root", 2)])
    by_name = {e.name: e for e in entries}
    assert by_name["meu-repo"].is_project and by_name["meu-repo"].branch == "feat/calma"
    assert not by_name["docs"].is_project

    fake_local = tmp_path / "local"
    agy_exe = fake_local / "Programs" / "Antigravity" / "Antigravity.exe"
    agy_exe.parent.mkdir(parents=True)
    agy_exe.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local))
    assert indexer.find_editor() == ("Antigravity", str(agy_exe))

    spawned: list[list[str]] = []
    monkeypatch.setattr(indexer.shutil, "which", lambda n: "wt.exe" if n == "wt" else None)
    monkeypatch.setattr(indexer.subprocess, "Popen", lambda args, **kw: spawned.append(args))
    assert indexer.open_agent(str(repo), "claude") == (True, "claude")
    assert spawned[0] == ["wt.exe", "-d", str(repo), "--", "cmd", "/k", "claude"]

    pin = store.add_pin("start", "app", "Velho Nome", r"C:\Apps\x.exe")
    store.rename_pin(pin.id, "Novo Nome")
    assert store.pins("start")[0].label == "Novo Nome"
