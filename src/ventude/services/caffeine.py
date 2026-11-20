"""Modo Cafeína: impede que o Windows apague a tela ou suspenda (ótimo para deixar agentes de IA rodando).

Usa SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED) na thread da
interface (o pedido vale enquanto a thread que o fez estiver viva) e desfaz com ES_CONTINUOUS.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
AWAKE = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED


def set_execution_state(flags: int) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
    kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
    return bool(kernel32.SetThreadExecutionState(flags))


class CaffeineService(QObject):
    changed = Signal(bool)

    def __init__(self, apply: Callable[[int], bool] = set_execution_state) -> None:
        super().__init__()
        self._apply = apply
        self.active = False
        self.until: float | None = None  # time.time() em que desliga sozinho (None: até desligar)
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.stop)

    def start(self, seconds: float | None = None) -> None:
        """Liga por `seconds` (ou até desligar). Ligar de novo troca o tempo."""
        self._apply(AWAKE)
        self.active = True
        self._timer.stop()
        if seconds and seconds > 0:
            self.until = time.time() + seconds
            self._timer.start(int(seconds * 1000))
        else:
            self.until = None
        self.changed.emit(True)

    def stop(self) -> None:
        self._timer.stop()
        was = self.active
        self.active, self.until = False, None
        self._apply(ES_CONTINUOUS)
        if was:
            self.changed.emit(False)

    def toggle(self) -> None:
        if self.active:
            self.stop()
        else:
            self.start()

    def describe(self) -> str:
        if not self.active:
            return "Cafeína desligada"
        if self.until is None:
            return "Cafeína: tela sempre acesa até você desligar"
        return f"Cafeína: tela sempre acesa até {time.strftime('%H:%M', time.localtime(self.until))}"
