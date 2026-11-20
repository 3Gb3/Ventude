"""Timer e Pomodoro ao vivo: contam no relógio do sistema e avisam a ilha ao terminar."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, QTimer, Signal

FOCUS_MIN = 25
BREAK_MIN = 5


@dataclass(frozen=True)
class TimerState:
    label: str  # Timer | Foco | Pausa
    kind: str  # timer | focus | break
    total: float  # segundos
    ends_at: float  # time.monotonic() do fim (quando rodando)
    paused_left: float | None = None  # segundos que faltavam ao pausar
    cycle: int = 1  # Pomodoro: número do ciclo de foco

    @property
    def running(self) -> bool:
        return self.paused_left is None

    def left(self, now: float | None = None) -> float:
        if self.paused_left is not None:
            return max(0.0, self.paused_left)
        return max(0.0, self.ends_at - (time.monotonic() if now is None else now))

    def progress(self, now: float | None = None) -> float:
        """0 a 100: quanto do tempo já passou."""
        return 100.0 * (1 - self.left(now) / self.total) if self.total > 0 else 100.0


def countdown(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def parse_duration(text: str) -> float | None:
    """'15', '15m', '90s', '1h30', '1h 30m', '1:30' (h:mm) -> segundos."""
    text = text.strip().lower()
    if not text:
        return None
    if match := re.fullmatch(r"(\d{1,2}):(\d{2})", text):
        total = int(match[1]) * 3600 + int(match[2]) * 60
    else:
        if re.sub(r"[\d\s]|min|h|m|s", "", text):
            return None  # sobrou algo que não é número nem unidade
        parts = re.findall(r"(\d+)\s*(min|h|m|s)?", text)
        if not parts:
            return None
        units = {"h": 3600, "min": 60, "m": 60, "s": 1, "": 60}
        total = sum(int(number) * units[unit] for number, unit in parts)
    return float(total) if 0 < total <= 24 * 3600 else None


class TimerService(QObject):
    changed = Signal(object)  # TimerState | None
    finished = Signal(object)  # TimerState que terminou

    def __init__(self) -> None:
        super().__init__()
        self.state: TimerState | None = None
        self.pomodoro = False
        self._tick = QTimer(self, interval=500)
        self._tick.timeout.connect(self._check)

    def start(self, seconds: float, label: str = "Timer") -> None:
        self.pomodoro = False
        self._set(TimerState(label, "timer", float(seconds), time.monotonic() + seconds))

    def start_pomodoro(self, focus_min: int = FOCUS_MIN) -> None:
        self.pomodoro = True
        self._set(TimerState("Foco", "focus", focus_min * 60.0, time.monotonic() + focus_min * 60.0))

    def toggle_pause(self) -> None:
        state = self.state
        if state is None:
            return
        if state.running:
            self._set(replace(state, paused_left=state.left()))
        else:
            self._set(replace(state, ends_at=time.monotonic() + (state.paused_left or 0), paused_left=None))

    def stop(self) -> None:
        self.pomodoro = False
        self._set(None)

    def _set(self, state: TimerState | None) -> None:
        self.state = state
        if state is not None and state.running:
            self._tick.start()
        elif state is None:
            self._tick.stop()
        self.changed.emit(state)

    def _check(self) -> None:
        state = self.state
        if state is None or not state.running or state.left() > 0:
            return
        if self.pomodoro:
            if state.kind == "focus":
                after = TimerState(
                    "Pausa", "break", BREAK_MIN * 60.0, time.monotonic() + BREAK_MIN * 60, cycle=state.cycle
                )
            else:
                after = TimerState(
                    "Foco",
                    "focus",
                    FOCUS_MIN * 60.0,
                    time.monotonic() + FOCUS_MIN * 60,
                    cycle=state.cycle + 1,
                )
            self.state = after
            self.changed.emit(after)
        else:
            self._set(None)
        self.finished.emit(state)


def finished_text(state: TimerState, pomodoro: bool) -> tuple[str, str]:
    if pomodoro and state.kind == "focus":
        return f"Foco {state.cycle} concluído", f"Hora da pausa de {BREAK_MIN} min."
    if pomodoro:
        return "Pausa acabou", f"Mais {FOCUS_MIN} min de foco começando agora."
    return "Timer terminou", f"{countdown(state.total)} se passaram."
