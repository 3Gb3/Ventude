"""Central de notificações, tela de bloqueio, Win 3 vezes (buraco negro) e botões da ponta da Doca."""

from __future__ import annotations

import time

from ventude.core.config import Settings
from ventude.services.notifications import MAX_ITEMS, NotificationCenter
from ventude.services.system import BLOCK, PASS, VK_LWIN, VK_S, WinKeyLogic


def wait_for(qapp, cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# ---------- notificações ----------
def test_notification_center_dedupe_skip_limit_and_persist(store):
    center = NotificationCenter(store)
    assert center.add("Claude em 95%", "Sessão de 5h", kind="limits", now=100.0) is not None
    assert center.add("Claude em 95%", "Sessão de 5h", now=103.0) is None  # bandeja repetindo a ilha
    assert center.add("Claude em 95%", "Sessão de 5h", now=200.0) is not None  # bem depois: conta
    assert center.add("Nova música", kind="music") is None
    assert center.add("Cor copiada", kind="tool") is None
    assert center.add("   ") is None
    assert center.unread == 2
    center.mark_all_read()
    assert center.unread == 0

    again = NotificationCenter(store)  # sobrevive a reiniciar o app
    assert [n.title for n in again.newest_first()] == ["Claude em 95%", "Claude em 95%"]
    assert again.add("Outro", now=500.0).id > max(n.id for n in center.items)

    for n in range(MAX_ITEMS + 10):
        again.add(f"Aviso {n}", now=1000.0 + n)
    assert len(again.items) == MAX_ITEMS and again.newest_first()[0].title == f"Aviso {MAX_ITEMS + 9}"
    again.add("Agenda", kind="agenda")
    again.dismiss_group("agenda")
    assert all(n.kind != "agenda" for n in again.items)
    again.dismiss(again.items[0].id)
    assert len(again.items) == MAX_ITEMS - 2  # o aviso da Agenda empurrou o mais velho para fora
    again.clear()
    assert not again.items and NotificationCenter(store).items == []


# ---------- Win 3 vezes ----------
def test_win_triple_tap_hides_island_event():
    now = [0.0]
    logic = WinKeyLogic({"launcher", "triple"})
    logic.clock = lambda: now[0]

    def tap(at: float) -> tuple[str, str]:
        now[0] = at
        logic.feed(VK_LWIN, True)
        return logic.feed(VK_LWIN, False)

    assert tap(0.0) == (BLOCK, "start")
    assert tap(0.3) == (BLOCK, "start")
    assert tap(0.6) == (BLOCK, "triple")
    assert tap(0.9) == (BLOCK, "start")  # recomeça a contar
    assert tap(2.0) == (BLOCK, "start")
    assert tap(2.6) == (BLOCK, "start")  # 0,6 s de intervalo: não é seguido
    assert tap(2.9) == (BLOCK, "start")
    assert tap(3.2) == (BLOCK, "triple")

    # um atalho no meio (Win + S) zera a contagem
    tap(10.0)
    tap(10.2)
    now[0] = 10.3
    logic.feed(VK_LWIN, True)
    logic.feed(VK_S, True)
    logic.feed(VK_S, False)
    assert logic.feed(VK_LWIN, False) == (BLOCK, "mask")
    assert tap(10.5) == (BLOCK, "start")

    # sem o Lançador na tecla Windows: os dois primeiros seguem para o Windows, o terceiro é nosso
    plain = WinKeyLogic({"triple"})
    plain.clock = lambda: now[0]
    for at, expected in ((20.0, (PASS, "")), (20.2, (PASS, "")), (20.4, (BLOCK, "triple"))):
        now[0] = at
        plain.feed(VK_LWIN, True)
        assert plain.feed(VK_LWIN, False) == expected

    off = WinKeyLogic({"launcher"})  # ajuste desligado
    off.clock = lambda: now[0]
    for at in (30.0, 30.1, 30.2):
        now[0] = at
        off.feed(VK_LWIN, True)
        assert off.feed(VK_LWIN, False) == (BLOCK, "start")


def test_black_hole_hides_and_brings_back_island(qapp, app_ctx, island):
    from ventude.ui.blackhole import strip_progress

    assert strip_progress(0.0, 1.0) == 0.0 and strip_progress(1.0, 0.0) == 1.0
    assert strip_progress(0.3, 1.0) > strip_progress(0.3, 0.0)  # pontas são sugadas primeiro

    island.show()
    qapp.processEvents()
    island.black_hole()
    assert not island.isVisible() and island._hole is not None
    assert wait_for(qapp, lambda: island._hole is None)
    assert not island.isVisible()
    island.black_hole()
    assert wait_for(qapp, lambda: island._hole is None and island.isVisible())


# ---------- tela de bloqueio ----------
def test_lock_settings_and_rules():
    from ventude.ui.lockscreen import should_auto_lock, timeout_label

    assert Settings().lock_after_min == 5 and Settings().lock_require_password
    assert Settings.from_dict({"lock_after_min": 99999, "layout_version": 9}).lock_after_min == 24 * 60
    assert Settings.from_dict({"lock_after_min": -3, "layout_version": 9}).lock_after_min == 0
    assert timeout_label(0) == "Nunca" and timeout_label(1) == "1 minuto" and timeout_label(60) == "1 hora"
    assert timeout_label(90) == "90 minutos" and timeout_label(120) == "2 horas"
    assert should_auto_lock(300, 5) and not should_auto_lock(299, 5)
    assert not should_auto_lock(9999, 0)  # Nunca
    assert not should_auto_lock(9999, 5, caffeine=True)
    assert not should_auto_lock(9999, 5, fullscreen=True)
    assert not should_auto_lock(9999, 5, keep_awake=True)


def test_lock_screen_idle_grace_and_password(qapp, app_ctx, monkeypatch):
    from ventude.ui import lockscreen
    from ventude.ui.lockscreen import LockScreen

    idle = [0.0]
    tick = [1]
    locks: list[bool] = []
    busy = [False]
    lock = LockScreen(
        app_ctx,
        idle_fn=lambda: idle[0],
        lock_fn=lambda: locks.append(True) or True,
        tick_fn=lambda: tick[0],
        busy_fn=lambda: busy[0],
    )
    monkeypatch.setattr(lockscreen, "ARM_DELAY_S", 0.0)

    lock.check_idle()
    assert not lock.active
    idle[0] = 5 * 60
    busy[0] = True  # Cafeína, vídeo, tela cheia
    lock.check_idle()
    assert not lock.active
    busy[0] = False
    lock.check_idle()
    assert lock.active and lock.auto and lock.panes

    # mexeu logo depois de aparecer sozinha: só some, sem senha
    tick[0] = 2
    lock._check_wake()
    assert not locks
    assert wait_for(qapp, lambda: not lock.active)

    # "Bloquear": o primeiro toque chama o bloqueio do Windows
    lock.show_lock(auto=False)
    lock.input_seen()
    assert locks == [True]
    assert wait_for(qapp, lambda: not lock.active, 2.5)

    # sem senha: o toque só tira a tela
    app_ctx.update_settings(lock_require_password=False)
    lock.show_lock(auto=False)
    lock.input_seen()
    assert locks == [True] and wait_for(qapp, lambda: not lock.active)

    # Nunca: não bloqueia sozinha
    app_ctx.update_settings(lock_after_min=0, lock_require_password=True)
    idle[0] = 10_000
    lock.check_idle()
    assert not lock.active

    # a prévia dos Ajustes nunca pede senha
    lock.show_lock(preview=True)
    lock.input_seen()
    assert locks == [True] and wait_for(qapp, lambda: not lock.active)


def test_request_power_lock_uses_lock_screen(qapp, app_ctx):
    shown: list[bool] = []

    class FakeLock:
        def show_lock(self, auto=False, preview=False):
            shown.append(auto)

    app_ctx.lock_screen = FakeLock()
    assert app_ctx.request_power("lock") == (True, "")
    assert shown == [False]


# ---------- Doca ----------
def test_dock_model_end_buttons():
    from ventude.ui.shell.dock_model import SlotModel
    from ventude.ui.shell.items import TaskItem

    items = [TaskItem(f"k{n}", f"App {n}", "app", f"C:/a{n}.exe", "app", f"C:/a{n}.exe") for n in range(3)]
    plain = SlotModel().layout(items, 1200, start_button=True)
    lay = SlotModel().layout(items, 1200, start_button=True, end_buttons=("background", "notifications"))
    assert lay.capsule_w > plain.capsule_w
    names = [name for name, _ in lay.end_rects]
    assert names == ["background", "notifications"]
    bx, by, bw, bh = lay.end_rect("notifications")
    assert bx + bw <= lay.capsule_x + lay.capsule_w
    assert lay.hit_end(bx + bw / 2, by + bh / 2) == "notifications"
    assert lay.hit_end(lay.slots[0].x + 2, by + bh / 2) == ""
    assert lay.end_separator_x is not None and lay.end_separator_x < lay.end_rect("background")[0]
    last = lay.slots[-1]
    assert last.x + last.w < lay.end_separator_x


# ---------- notificações do Windows ----------
def test_windows_notifications_denied_shows_access_hint(qapp, app_ctx):
    from ventude.services.winnotify import Snapshot, WindowsNotifications
    from ventude.ui.shell.notifications import NotificationsView

    win = WindowsNotifications(app_ctx.run, reader=lambda _k, _a: Snapshot("denied", [], {}))
    app_ctx.notifications.attach_windows(win)
    win.refresh()
    assert wait_for(qapp, lambda: win.access == "denied")
    view = NotificationsView(app_ctx)
    view.refresh()
    assert view.access_btn.isVisibleTo(view)


# ---------- luz noturna ----------
def test_night_light_blob_toggle():
    from ventude.services import nightlight

    off = bytes.fromhex(
        "43 42 01 00 0A 02 01 00 2A 06 E4 86 EC D5 06 2A 2B 0E 13 43 42 01 00 D0 0A "
        "02 C6 14 F7 D4 B0 D1 FC F4 D3 EE 01 00 00 00 00"
    )
    assert nightlight.is_on(off) is False
    on = nightlight.toggled(off, True)
    assert on is not None and len(on) == len(off) + 2 and nightlight.is_on(on) is True
    assert on[18] == 0x15 and on[23:25] == b"\x10\x00" and on[10] == 0xE5  # carimbo subiu
    back = nightlight.toggled(on, False)
    assert back is not None and nightlight.is_on(back) is False and back[19:] == off[19:]
    assert nightlight.toggled(b"\x00" * 10, True) is None  # formato desconhecido: não mexe


def test_background_uses_current_ventude_icon():
    import sys

    from ventude.services import apps
    from ventude.ui.shell.background import is_ventude

    assert is_ventude(apps.TrayApp("Ventude", r"C:\Apps\Ventude\Ventude.exe", b"old"))
    assert is_ventude(apps.TrayApp("Python", sys.executable, b""))
    assert not is_ventude(apps.TrayApp("Discord", r"C:\Apps\Discord.exe", b""))
