"""Linguagem de movimento do Ventude Shell: curvas, escalas de duração e Tween único."""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import lru_cache

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPointF, QVariantAnimation, Signal

from ventude.services import system

DURATIONS: dict[str, int] = {
    "mare": 320,
    "esquiva": 180,
    "volta": 240,
    "halo": 120,
    "rotulo": 120,
    "press_in": 80,
    "press_out": 160,
    "respiro": 1100,
    "acender": 260,
    "chegar": 240,
    "partir": 200,
    "adormecer": 180,
    "lagarta": 240,
    "atencao": 300,
    "espaco": 160,
    "assentar": 220,
    "puf": 200,
    "desdobrar": 220,
    "desdobrar_fade": 120,
    "desdobrar_close": 120,
    "gota": 300,
    "gota_fast": 140,
    "recolher": 160,
    "modo": 160,
    "escada": 140,
    "selecao": 120,
    "linha": 140,
    "trilho": 200,
    "voo": 360,
    "morfose_open": 340,
    "morfose_close": 230,
    "volume_in": 140,
    "volume_out": 160,
    "ponto": 200,
    "eco": 200,
    "tab": 260,
    "indicador": 320,
    "pagina": 240,
    "anel": 720,
    "barra": 520,
    "interruptor": 220,
}

# Curvas cúbicas (as mesmas do CSS: cubic-bezier(x1, y1, x2, y2)). "enfase" é a curva principal do Ventude 3:
# arranca rápido e assenta devagar; "mola" passa um pouco do ponto e volta, como a Dynamic Island.
BEZIERS: dict[str, tuple[float, float, float, float]] = {
    "padrao": (0.2, 0.0, 0.0, 1.0),
    "enfase": (0.05, 0.7, 0.1, 1.0),
    "saida": (0.3, 0.0, 0.8, 0.15),
    "suave": (0.4, 0.0, 0.2, 1.0),
    "mola": (0.3, 1.36, 0.55, 1.0),
    "mola_leve": (0.34, 1.12, 0.64, 1.0),
}


def clamp(t: float) -> float:
    return max(0.0, min(1.0, float(t)))


def linear(t: float) -> float:
    return clamp(t)


def out_cubic(t: float) -> float:
    t = clamp(t)
    return 1.0 - (1.0 - t) ** 3


def in_cubic(t: float) -> float:
    t = clamp(t)
    return t**3


def in_out_cubic(t: float) -> float:
    t = clamp(t)
    return 4.0 * t**3 if t < 0.5 else 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0


def out_back(s: float = 1.15) -> Callable[[float], float]:
    c3 = s + 1.0

    def curve(t: float) -> float:
        t = clamp(t)
        if t <= 0.0:
            return 0.0
        if t >= 1.0:
            return 1.0
        return 1.0 + c3 * (t - 1.0) ** 3 + s * (t - 1.0) ** 2

    return curve


def in_out_sine(t: float) -> float:
    return -(math.cos(math.pi * clamp(t)) - 1.0) / 2.0


def mola(t: float) -> float:
    t = clamp(t)
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return 1.0 - math.exp(-7.0 * t) * math.cos(9.0 * t)


def bezier(x1: float, y1: float, x2: float, y2: float) -> Callable[[float], float]:
    """Curva cubic-bezier do CSS como função t -> progresso (para o Tween, pintado em Python)."""

    def sample(a1: float, a2: float, t: float) -> float:
        return ((1.0 - 3.0 * a2 + 3.0 * a1) * t + (3.0 * a2 - 6.0 * a1)) * t * t + 3.0 * a1 * t

    def slope(a1: float, a2: float, t: float) -> float:
        return 3.0 * (1.0 - 3.0 * a2 + 3.0 * a1) * t * t + 2.0 * (3.0 * a2 - 6.0 * a1) * t + 3.0 * a1

    def curve(x: float) -> float:
        x = clamp(x)
        if x <= 0.0 or x >= 1.0:
            return x
        t = x
        for _ in range(8):  # Newton: acha o t cujo x é o pedido
            err = sample(x1, x2, t) - x
            if abs(err) < 1e-5:
                break
            d = slope(x1, x2, t)
            if abs(d) < 1e-6:
                break
            t -= err / d
        if not 0.0 <= t <= 1.0:  # bisseção de reserva
            lo, hi, t = 0.0, 1.0, x
            for _ in range(24):
                if sample(x1, x2, t) < x:
                    lo = t
                else:
                    hi = t
                t = (lo + hi) / 2.0
        return sample(y1, y2, t)

    return curve


@lru_cache(maxsize=16)
def named(name: str) -> Callable[[float], float]:
    return bezier(*BEZIERS.get(name, BEZIERS["padrao"]))


def easing(name: str) -> QEasingCurve:
    """QEasingCurve nativa (BezierSpline): a interpolação roda no C++, sem chamar Python a cada quadro."""
    x1, y1, x2, y2 = BEZIERS.get(name, BEZIERS["padrao"])
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
    return curve


def out_expo(t: float) -> float:
    t = clamp(t)
    return 1.0 if t >= 1.0 else 1.0 - 2.0 ** (-10.0 * t)


def reduced_motion(settings: object | None = None) -> bool:
    if not system.client_area_animation():
        return True
    if settings is not None:
        if getattr(settings, "animation", None) == "nenhuma":
            return True
        if getattr(settings, "animation_ms", 260) <= 0:
            return True
    return False


def scale(settings: object | None = None) -> float:
    if reduced_motion(settings):
        return 0.0
    if settings is not None and hasattr(settings, "animation_ms"):
        base = int(settings.animation_ms)  # type: ignore[attr-defined]
    elif settings is not None and hasattr(settings, "animation"):
        from ventude.core.config import ANIMATION_SPEEDS

        base = ANIMATION_SPEEDS.get(str(settings.animation), 260)  # type: ignore[attr-defined]
    else:
        base = 260
    if base <= 0:
        return 0.0
    return float(base) / 260.0


def ms(name: str, settings: object | None = None, *, base: int | None = None) -> int:
    duration = base if base is not None else DURATIONS.get(name, 220)
    return max(0, round(duration * scale(settings)))


class Tween(QVariantAnimation):
    """Animação linear de 0→1 com curva aplicada em Python; duração 0 dispara na hora."""

    progressed = Signal(float)
    done = Signal()

    def __init__(self, parent: object = None) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.value = 0.0
        self._start = 0.0
        self._end = 1.0
        self._curve: Callable[[float], float] = out_cubic
        self.valueChanged.connect(self._on_value)
        self.finished.connect(self._on_finished)

    @property
    def running(self) -> bool:
        return self.state() == QAbstractAnimation.State.Running

    def play(
        self,
        duration_ms: int,
        start: float = 0.0,
        end: float = 1.0,
        curve: Callable[[float], float] = out_cubic,
        loops: int = 1,
    ) -> None:
        self.stop()
        self._start, self._end, self._curve = float(start), float(end), curve
        if duration_ms <= 0:
            self.value = self._end
            self.progressed.emit(self.value)
            self.done.emit()
            return
        self.value = self._start
        self.setStartValue(0.0)
        self.setEndValue(1.0)
        self.setDuration(max(1, int(duration_ms)))
        self.setLoopCount(max(1, int(loops)))
        self.start()

    def _on_value(self, raw: object) -> None:
        t = self._curve(float(raw))  # type: ignore[arg-type]
        self.value = self._start + (self._end - self._start) * t
        self.progressed.emit(self.value)

    def _on_finished(self) -> None:
        self.value = self._end
        self.done.emit()
