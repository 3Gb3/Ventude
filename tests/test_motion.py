"""Testes das curvas de animação, redução de movimento e Tween do Ventude."""

from __future__ import annotations

from types import SimpleNamespace

from ventude.services import system
from ventude.ui import motion


def test_curves_boundaries_and_monotonicity():
    assert motion.clamp(-1.0) == 0.0
    assert motion.clamp(2.5) == 1.0
    assert motion.linear(0.4) == 0.4
    assert motion.out_cubic(0.0) == 0.0 and motion.out_cubic(1.0) == 1.0
    assert motion.out_cubic(0.5) > 0.5
    assert motion.in_cubic(0.0) == 0.0 and motion.in_cubic(1.0) == 1.0
    assert motion.in_cubic(0.5) < 0.5
    assert motion.in_out_cubic(0.0) == 0.0 and motion.in_out_cubic(0.5) == 0.5
    assert motion.in_out_cubic(1.0) == 1.0
    assert motion.out_back(1.15)(0.0) == 0.0
    assert abs(motion.out_back(1.15)(1.0) - 1.0) < 1e-6
    assert motion.out_back(1.15)(0.7) > 1.0  # pequeno overshoot
    assert motion.in_out_sine(0.0) == 0.0 and abs(motion.in_out_sine(1.0) - 1.0) < 1e-6
    assert motion.mola(0.0) == 0.0 and abs(motion.mola(1.0) - 1.0) < 1e-6


def test_reduced_motion_and_duration_scaling(monkeypatch):
    monkeypatch.setattr(system, "client_area_animation", lambda: True)
    fast = SimpleNamespace(animation="rapida")
    normal = SimpleNamespace(animation="normal")
    slow = SimpleNamespace(animation="suave")
    none_anim = SimpleNamespace(animation="nenhuma")

    from ventude.core.config import ANIMATION_SPEEDS

    assert not motion.reduced_motion(fast)
    assert motion.reduced_motion(none_anim)
    assert abs(motion.scale(fast) - ANIMATION_SPEEDS["rapida"] / 260.0) < 1e-6
    assert motion.scale(normal) == 1.0
    assert abs(motion.scale(slow) - ANIMATION_SPEEDS["suave"] / 260.0) < 1e-6
    assert motion.scale(none_anim) == 0.0
    assert motion.ms("mare", normal) == motion.DURATIONS["mare"]
    assert motion.ms("mare", fast) == round(motion.DURATIONS["mare"] * (ANIMATION_SPEEDS["rapida"] / 260.0))
    assert motion.ms("mare", none_anim) == 0

    monkeypatch.setattr(system, "client_area_animation", lambda: False)
    assert motion.reduced_motion(normal)
    assert motion.ms("mare", normal) == 0


def test_tween_zero_duration_finishes_immediately(qapp):
    tween = motion.Tween()
    vals: list[float] = []
    done: list[bool] = []
    tween.progressed.connect(vals.append)
    tween.done.connect(lambda: done.append(True))
    tween.play(0, 0.2, 1.0)
    assert vals == [1.0]
    assert done == [True]
    assert not tween.running


def test_tween_timed_reaches_end(qapp):
    tween = motion.Tween()
    vals: list[float] = []
    done: list[bool] = []
    tween.progressed.connect(vals.append)
    tween.done.connect(lambda: done.append(True))
    tween.play(30, 0.0, 1.0, motion.out_cubic)
    assert tween.running
    for _ in range(30):
        qapp.processEvents()
        if done:
            break
        import time

        time.sleep(0.01)
    assert done == [True]
    assert vals and abs(vals[-1] - 1.0) < 1e-6
