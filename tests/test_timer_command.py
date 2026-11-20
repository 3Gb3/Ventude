import time
from datetime import date, timedelta
from datetime import time as dtime

from ventude.services.timer import TimerService, TimerState, countdown, finished_text, parse_duration
from ventude.ui.command import parse_event


def test_parse_duration_and_countdown():
    assert parse_duration("15") == 900 and parse_duration("90s") == 90 and parse_duration("1h30") == 5400
    assert parse_duration("1:30") == 5400 and parse_duration("abc") is None and parse_duration("0") is None
    assert countdown(65) == "01:05" and countdown(3725) == "1:02:05"


def test_timer_finishes_and_pomodoro_cycles(qapp):
    service = TimerService()
    done = []
    service.finished.connect(done.append)
    service.start(0.2)
    end = time.monotonic() + 2
    while not done and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.02)
    assert done and service.state is None
    assert finished_text(done[0], False)[0] == "Timer terminou"

    service.start_pomodoro()
    service.toggle_pause()
    assert not service.state.running and service.state.left() > 1490
    service.toggle_pause()
    service.state = TimerState("Foco", "focus", 1500, time.monotonic() - 1)
    service._check()
    assert service.state.kind == "break" and done[-1].kind == "focus"
    service.state = TimerState("Pausa", "break", 300, time.monotonic() - 1)
    service._check()
    assert service.state.kind == "focus" and service.state.cycle == 2
    service.stop()
    assert service.state is None


def test_parse_event_text():
    today = date(2026, 11, 20)
    assert parse_event("call 15h", today) == ("call", today, dtime(15, 0), None)
    title, day, start, end = parse_event("dentista amanhã 9h-10:30", today)
    assert (title, day, start, end) == ("dentista", today + timedelta(days=1), dtime(9, 0), dtime(10, 30))
    assert parse_event("ligar pro banco", today) == ("ligar pro banco", today, None, None)
