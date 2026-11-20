import time

import pytest

from ventude.core.config import Settings
from ventude.core.tasks import Tasks
from ventude.limits.manager import LimitsManager
from ventude.limits.model import ProviderError, Report, Window


def wait(qapp, manager, provider, timeout=3.0):
    end = time.monotonic() + timeout
    while manager.busy(provider) and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()


class Fake:
    def __init__(self):
        self.result = None

    def __call__(self, ctx):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make(qapp, store, home, settings=None):
    holder = {"s": settings or Settings(antigravity_mode="off", grok_mode="off")}
    fake = Fake()
    manager = LimitsManager(
        store,
        Tasks(),
        lambda: holder["s"],
        home=home,
        fetchers={"claude": fake, "antigravity": fake, "grok": fake},
    )
    alerts = []
    manager.alert.connect(lambda _p, t, m: alerts.append((t, m)))
    return manager, fake, alerts, holder


def test_success_then_failure_keeps_last_values(qapp, store, home):
    manager, fake, _, _ = make(qapp, store, home)
    fake.result = Report("claude", "ok", [Window("session", "Sessão", 40, time.time() + 3600)])
    manager.refresh("claude", force=True)
    wait(qapp, manager, "claude")
    first = manager.report("claude")
    assert first.state == "ok" and first.fetched_at > 0
    fake.result = ProviderError("Sem conexão")
    manager.refresh("claude", force=True)
    wait(qapp, manager, "claude")
    stale = manager.report("claude")
    assert stale.state == "stale" and stale.message == "Sem conexão"
    assert stale.windows[0].used == 40
    assert manager.report("grok").state == "off"


def test_failure_without_history_is_error(qapp, store, home):
    manager, fake, _, _ = make(qapp, store, home)
    fake.result = RuntimeError("bug")
    manager.refresh("claude", force=True)
    wait(qapp, manager, "claude")
    assert manager.report("claude").state == "error"


def test_threshold_alert_and_reset_alert(qapp, store, home):
    manager, fake, alerts, _ = make(qapp, store, home)
    soon = time.time() + 3600
    fake.result = Report("claude", "ok", [Window("session", "Sessão · 5h", 70, soon)])
    manager.refresh("claude", force=True)
    wait(qapp, manager, "claude")
    assert alerts == []  # primeira leitura nunca avisa
    fake.result = Report("claude", "ok", [Window("session", "Sessão · 5h", 85, soon)])
    manager.refresh("claude", force=True)
    wait(qapp, manager, "claude")
    assert alerts and "85%" in alerts[-1][0]
    manager.reports["claude"].windows[0].resets_at = time.time() - 1
    manager._check_resets("claude", time.time())
    assert "renovado" in alerts[-1][0]
    count = len(alerts)
    manager._check_resets("claude", time.time())
    assert len(alerts) == count  # avisa uma vez só
    wait(qapp, manager, "claude")


def test_cache_survives_restart_as_stale(qapp, store, home):
    manager, fake, _, _ = make(qapp, store, home)
    fake.result = Report("claude", "ok", [Window("session", "Sessão", 12, time.time() + 60)], plan="Pro")
    manager.refresh("claude", force=True)
    wait(qapp, manager, "claude")
    again, _, _, _ = make(qapp, store, home)
    restored = again.report("claude")
    assert restored.state == "stale" and restored.plan == "Pro" and restored.windows[0].used == 12
    assert "Claude 12%" in again.summary()


def test_mode_off_short_circuits(qapp, store, home):
    manager, fake, _, holder = make(qapp, store, home)
    holder["s"] = holder["s"].copy(claude_mode="off")
    manager.tick()
    assert manager.report("claude").state == "off"
    assert manager.visible_reports() == []


def test_window_exhausts_in_by_pace_and_history():
    from ventude.limits.model import Window

    now = 10_000_000.0
    # sessão de 5h: 2h passadas, 60% usado -> 100% em ~1h20, antes de renovar (3h)
    fast = Window("session", "Sessão", 60, now + 3 * 3600, period=5 * 3600)
    assert fast.exhausts_in(now) == pytest.approx(80 * 60, rel=0.01)
    calm = Window("session", "Sessão", 20, now + 3 * 3600, period=5 * 3600)
    assert calm.exhausts_in(now) is None  # no ritmo, sobra cota
    early = Window("session", "Sessão", 30, now + 5 * 3600 - 60, period=5 * 3600)
    assert early.exhausts_in(now) is None  # começo da janela: cedo demais para prever
    model = Window("model:x", "Gemini", 50, now + 4 * 3600)
    history = [(now - 1800, 40.0), (now - 900, 45.0), (now, 50.0)]  # +10% em 30 min
    assert model.exhausts_in(now, history) == pytest.approx(9000, rel=0.01)
    assert model.exhausts_in(now, None) is None


def test_manager_keeps_usage_history(qapp, store, home):
    manager, fake, _alerts, _ = make(qapp, store, home)
    fake.result = Report("claude", "ok", [Window("session", "Sessão", 10, time.time() + 3600)])
    manager.refresh("claude", force=True)
    wait(qapp, manager, "claude")
    points = manager.history("claude", "session", 3600)
    assert points and points[-1][1] == 10
    manager.refresh("claude", force=True)  # mesma leitura logo depois: não repete a amostra
    wait(qapp, manager, "claude")
    assert len(manager.history("claude", "session", 3600)) == len(points)
    store.prune_samples(time.time() + 1)
    assert manager.history("claude", "session", 3600) == []
