"""Cortina de energia (Interruptor) da Ventude Shell 3.0.

Antes de desligar, reiniciar, suspender, hibernar, bloquear ou sair:
- A Ilha passa o bastão (crossfade 120 ms) para o grande Interruptor em Ink no centro do monitor dela.
- Uma contagem no perímetro avisa quanto falta (Esc ou "Cancelar" desistirão).
- Cada ação faz seu gesto final (CRT no desligar, giro no reiniciar, respiração no suspender,
  afundar no hibernar, estalo no bloquear, vôo no sair, tremor vermelho ±6 px em erro).
- Monitores secundários espelham o mesmo estado com um pequeno interruptor 44×22.
- No ligar do PC (`greet`), o Interruptor acende com mola e desliza de volta à Ilha.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QElapsedTimer, QObject, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from ventude.core.timefmt import MONTHS, weekday_name
from ventude.services import apps, power
from ventude.ui import brand, motion, theme
from ventude.ui.shell.common import shell_font

log = logging.getLogger(__name__)

DIM = 0.9
INTRO_MS = 450
BATON_MS = 120
TEXT_DELAY_MS = 180
TEXT_MS = 520
GO_MS = 320
OUT_MS = 650
CANCEL_MS = 380
SAFETY_MS = 15_000

SWITCH_W = 184.0
SWITCH_H = 92.0
MIRROR_W = 44.0
MIRROR_H = 22.0


@dataclass(frozen=True)
class Farewell:
    key: str
    title: str
    subtitle: str
    icon: str
    hold_ms: int
    resumes: bool
    rest_ms: int = 900


FAREWELLS: dict[str, Farewell] = {
    "shutdown": Farewell("shutdown", "Até logo!", "Desligando o computador…", "power", 1900, False),
    "restart": Farewell("restart", "Já volto!", "Reiniciando o computador…", "rotate-cw", 1700, False),
    "update_restart": Farewell(
        "update_restart",
        "Já volto!",
        "Instalando as atualizações e reiniciando · não desligue o PC",
        "refresh-badge",
        2200,
        False,
    ),
    "update_shutdown": Farewell(
        "update_shutdown",
        "Até logo!",
        "Instalando as atualizações e desligando · não desligue o PC",
        "power-badge",
        2200,
        False,
    ),
    "sleep": Farewell("sleep", "Até daqui a pouco!", "Suspendendo…", "moon", 1300, True, 1500),
    "hibernate": Farewell(
        "hibernate",
        "Até mais tarde!",
        "Hibernando · tudo fica do jeito que está",
        "hibernation",
        1500,
        True,
        1800,
    ),
    "lock": Farewell("lock", "Até já!", "Bloqueando…", "lock", 650, True, 700),
    "logoff": Farewell("logoff", "Até a próxima, {name}!", "Saindo da sessão…", "log-out", 1700, False),
}

ease_out = motion.out_cubic
ease_in_out = motion.in_out_cubic


def greeting(now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    if 5 <= hour < 12:
        return "Bom dia"
    if 12 <= hour < 18:
        return "Boa tarde"
    return "Boa noite"


def with_name(template: str, name: str) -> str:
    """'Até a próxima, {name}!' com o primeiro nome; sem nome conhecido, só 'Até a próxima!'."""
    first = apps.first_name(name or "")
    if not name or first in ("Você", "Voce"):
        return template.replace(", {name}", "").replace("{name}", "").strip()
    return template.format(name=first)


def uptime_seconds() -> float:
    """Há quanto tempo o Windows ligou (para saudar só logo depois de ligar o PC)."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return kernel32.GetTickCount64() / 1000
    except (AttributeError, OSError):
        return 1e9


class Curtain(QWidget):
    """Uma cortina por monitor. A principal desenha o Interruptor; as outras espelham em 44×22."""

    def __init__(self, fx: PowerFx, geometry: QRectF, primary: bool) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool,
        )
        self.fx, self.primary = fx, primary
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Ventude")
        self.setGeometry(geometry.toRect())
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def cancel_rect(self) -> QRectF:
        center = QPointF(self.width() / 2, self.height() / 2)
        return QRectF(center.x() - 62, center.y() + 132, 124, 36)

    def paintEvent(self, _event: object) -> None:
        fx = self.fx
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        level = max(0.0, min(1.0, fx.level))
        center = QPointF(self.width() / 2, self.height() / 2)
        glow = QRadialGradient(center, max(self.width(), self.height()) * 0.72)
        glow.setColorAt(0.0, QColor(18, 22, 26, int(255 * level)))
        glow.setColorAt(1.0, QColor(0, 0, 0, int(255 * level)))
        p.fillRect(self.rect(), glow)

        if not self.primary:
            if fx.switch_alpha > 0.01:
                mirror_rect = QRectF(
                    center.x() - MIRROR_W / 2.0,
                    center.y() - MIRROR_H / 2.0,
                    MIRROR_W,
                    MIRROR_H,
                )
                p.save()
                p.setOpacity(max(0.0, min(1.0, fx.switch_alpha)))
                brand.paint_switch(
                    p,
                    mirror_rect,
                    on=fx.switch_on,
                    outline=fx.switch_outline,
                    dim=fx.switch_dim,
                    accent=theme.DANGER if fx.error else theme.ACCENT,
                    outline_color=theme.DANGER if fx.error else None,
                    crt=fx.crt,
                    sink_y=fx.sink_y * 0.25,
                    breath=fx.breath,
                )
                p.restore()
            return

        # Passagem de bastão: foto da ilha sumindo nos primeiros 120 ms
        if fx.island_pixmap is not None and not fx.island_pixmap.isNull() and fx.baton_alpha > 0.01:
            p.save()
            p.setOpacity(fx.baton_alpha)
            ir = QRectF(fx.island_local_rect)
            start_c = ir.center()
            end_c = QPointF(center.x(), center.y() - 78 + fx.rise)
            cur_c = start_c + (end_c - start_c) * (1.0 - fx.baton_alpha)
            scale = 1.0 + 0.15 * (1.0 - fx.baton_alpha)
            tw, th = ir.width() * scale, ir.height() * scale
            p.drawPixmap(QRectF(cur_c.x() - tw / 2, cur_c.y() - th / 2, tw, th).toRect(), fx.island_pixmap)
            p.restore()

        # Grande Interruptor no centro (ou voando em direção à ilha no cancelamento/saudação)
        if fx.switch_alpha > 0.01:
            sw_center = QPointF(
                center.x() + fx.shake_x,
                center.y() - 78 + fx.rise,
            )
            if fx.island_local_rect.isValid() and fx.return_t > 0.0:
                ic = QRectF(fx.island_local_rect).center()
                sw_center = sw_center + (ic - sw_center) * fx.return_t
            scale = 1.0 - 0.65 * fx.return_t
            sw_rect = QRectF(
                sw_center.x() - (SWITCH_W * scale) / 2.0,
                sw_center.y() - (SWITCH_H * scale) / 2.0,
                SWITCH_W * scale,
                SWITCH_H * scale,
            )
            p.save()
            p.setOpacity(max(0.0, min(1.0, fx.switch_alpha)))
            brand.paint_switch(
                p,
                sw_rect,
                on=fx.switch_on,
                outline=fx.switch_outline,
                dim=fx.switch_dim,
                accent=theme.DANGER if fx.error else theme.ACCENT,
                outline_color=theme.DANGER if fx.error else None,
                crt=fx.crt,
                sink_y=fx.sink_y,
                breath=fx.breath,
            )
            # Borda pontilhada de contagem no perímetro do Interruptor
            if fx.show_progress and fx.progress > 0.01:
                self._paint_perimeter_progress(p, sw_rect.adjusted(-5, -5, 5, 5), fx.progress)
            p.restore()

        if fx.text_alpha <= 0.01:
            return
        alpha = max(0.0, min(1.0, fx.text_alpha))
        rise = fx.rise
        # Título
        p.setOpacity(1.0)
        p.setPen(theme.color(theme.TEXT, alpha))
        title_font = shell_font(48, QFont.Weight.DemiBold, display=True)
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.5 + 1.5 * (1 - alpha))
        p.setFont(title_font)
        p.drawText(
            QRectF(0, center.y() - 6 + rise, self.width(), 68), Qt.AlignmentFlag.AlignCenter, fx.title
        )
        # Subtítulo
        sub_alpha = max(0.0, min(1.0, fx.sub_alpha))
        p.setPen(theme.color(theme.TEXT_2, sub_alpha))
        p.setFont(shell_font(16))
        p.drawText(
            QRectF(0, center.y() + 62 + rise * 0.6, self.width(), 28),
            Qt.AlignmentFlag.AlignCenter,
            fx.subtitle,
        )
        # Pulsos discretos pós-disparo (ou linha de progresso)
        if fx.pulse_on:
            for index in range(3):
                phase = (time.monotonic() * 2.2 - index * 0.22) % 1.0
                dot_alpha = 0.25 + 0.75 * max(0.0, 1 - abs(phase - 0.5) * 2)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.color(theme.TEXT_2, dot_alpha * sub_alpha))
                p.drawEllipse(QPointF(center.x() - 14 + index * 14, center.y() + 104), 3, 3)

        if fx.cancellable:
            button = self.cancel_rect()
            hover = button.contains(self.mapFromGlobal(QCursor.pos()).toPointF())
            p.setPen(QPen(QColor(255, 255, 255, int((70 if hover else 40) * sub_alpha)), 1))
            p.setBrush(QColor(255, 255, 255, int((26 if hover else 10) * sub_alpha)))
            p.drawRoundedRect(button, 18, 18)
            p.setPen(theme.color(theme.TEXT, sub_alpha))
            p.setFont(shell_font(13, QFont.Weight.DemiBold))
            p.drawText(button, Qt.AlignmentFlag.AlignCenter, "Cancelar")
            p.setPen(theme.color(theme.MUTED, sub_alpha * 0.9))
            p.setFont(shell_font(12))
            p.drawText(
                QRectF(0, self.height() - 60, self.width(), 24), Qt.AlignmentFlag.AlignCenter, "Esc cancela"
            )

    def _paint_perimeter_progress(self, p: QPainter, rect: QRectF, progress: float) -> None:
        radius = rect.height() / 2.0
        straight = max(0.0, rect.width() - rect.height())
        perim = 2.0 * straight + math.pi * rect.height()
        stroke_len = max(0.01, min(1.0, progress)) * perim
        pen = QPen(QColor(theme.ACCENT), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        pen.setDashPattern([stroke_len / 2.5, perim / 2.5 + 10.0])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, radius, radius)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.fx.cancel()

    def mouseMoveEvent(self, _event: QMouseEvent) -> None:
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.primary and self.cancel_rect().contains(event.position()):
            self.fx.cancel()


class PowerFx(QObject):
    """Linha do tempo da cortina Interruptor: entrada -> contagem -> ação -> saída."""

    finished = Signal(str)
    greet_landed = Signal()

    def __init__(
        self,
        ctx: object,
        runner: Callable[[str], tuple[bool, str]] | None = None,
        time_scale: float = 1.0,
        island: object | None = None,
    ) -> None:
        super().__init__()
        self.ctx = ctx
        self._runner = runner or power.run
        self.time_scale = max(0.001, time_scale)
        self.island = island
        self.curtains: list[Curtain] = []
        self.timer = QTimer(self, interval=16)
        self.timer.timeout.connect(self._tick)
        self.clock = QElapsedTimer()
        self.phase = ""
        self.key = ""
        self.farewell: Farewell | None = None
        self.island_pixmap: QPixmap | None = None
        self.island_local_rect = QRect()
        self._hid_island = False
        self._reset_visuals()

    def _reset_visuals(self) -> None:
        self.level = 0.0
        self.text_alpha = 0.0
        self.sub_alpha = 0.0
        self.rise = 18.0
        self.progress = 0.0
        self.title = ""
        self.subtitle = ""
        self.icon = "power"
        self.error = False
        self.cancellable = False
        self.show_progress = True
        self.pulse_on = False
        self.switch_on = 1.0
        self.switch_outline = 0.0
        self.switch_dim = 1.0
        self.switch_alpha = 1.0
        self.baton_alpha = 0.0
        self.crt = 0.0
        self.sink_y = 0.0
        self.breath = 1.0
        self.shake_x = 0.0
        self.return_t = 0.0
        self._start_level = 0.0
        self._start_text = 0.0
        self._ran = False
        self.island_pixmap = None

    @property
    def active(self) -> bool:
        return bool(self.phase)

    def _capture_island(self) -> None:
        island = self.island
        self.island_pixmap = None
        self.island_local_rect = QRect()
        if island is None or not isinstance(island, QWidget):
            return
        try:
            screen = island.screen() or QGuiApplication.primaryScreen()
            sgeom = screen.geometry() if screen else QRect()
            geom = island.geometry()
            self.island_local_rect = QRect(
                geom.left() - sgeom.left(),
                geom.top() - sgeom.top(),
                geom.width(),
                geom.height(),
            )
            if island.isVisible():
                self.island_pixmap = island.grab()
                self.baton_alpha = 1.0
                QTimer.singleShot(0, self._hide_island)
        except Exception:  # noqa: BLE001
            self.island_pixmap = None

    def _hide_island(self) -> None:
        if self.active and isinstance(self.island, QWidget) and self.island.isVisible():
            self._hid_island = True
            self.island.hide()

    def _restore_island(self) -> None:
        if self._hid_island and isinstance(self.island, QWidget):
            self._hid_island = False
            self.island.show()
            self.island.raise_()

    # ---------- começar ----------
    def play(self, key: str) -> bool:
        farewell = FAREWELLS.get(key)
        if farewell is None or self.active:
            return False
        self._reset_visuals()
        self.key, self.farewell = key, farewell
        self.title = with_name(farewell.title, apps.display_name())
        self.subtitle = farewell.subtitle
        self.icon = farewell.icon
        self.cancellable = True
        self._capture_island()
        self._open_curtains(focus=True)
        self._enter("intro")
        return True

    def greet(self, name: str, detail: str = "") -> bool:
        """'Bom dia, Fulano!' ao ligar o PC: o Interruptor acende com mola e volta à ilha."""
        if self.active:
            return False
        self._reset_visuals()
        self.key, self.farewell = "greet", None
        now = datetime.now()
        self.title = with_name(f"{greeting(now)}, {{name}}!", name)
        when = f"{weekday_name(now.date())}, {now.day} de {MONTHS[now.month - 1]}"
        self.subtitle = f"{when} · {detail}" if detail else when
        self.icon = "sun" if 5 <= now.hour < 18 else "moon"
        self.level = 1.0
        self.switch_on = 0.0
        self.show_progress = False
        if isinstance(self.island, QWidget):
            screen = self.island.screen() or QGuiApplication.primaryScreen()
            sgeom = screen.geometry() if screen else QRect()
            geom = self.island.geometry()
            self.island_local_rect = QRect(
                geom.left() - sgeom.left(),
                geom.top() - sgeom.top(),
                geom.width(),
                geom.height(),
            )
            if self.island.isVisible():
                self._hid_island = True
                self.island.hide()
        self._open_curtains(focus=False)
        self._enter("greet")
        return True

    def cancel(self) -> None:
        if not self.cancellable or self.phase not in ("intro",):
            return
        self.cancellable = False
        self._start_level, self._start_text = self.level, self.text_alpha
        self._enter("cancel")

    def _open_curtains(self, focus: bool) -> None:
        self._close_curtains()
        if isinstance(self.island, QWidget):
            primary = self.island.screen() or QGuiApplication.primaryScreen()
        else:
            primary = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        for screen in QGuiApplication.screens() or []:
            curtain = Curtain(self, QRectF(screen.geometry()), screen is primary)
            curtain.show()
            curtain.raise_()
            self.curtains.append(curtain)
        if focus:
            for curtain in self.curtains:
                if curtain.primary:
                    curtain.activateWindow()
                    curtain.setFocus()
                    try:
                        from ventude.services.winutil import force_foreground

                        force_foreground(int(curtain.winId()))
                    except Exception:  # noqa: BLE001
                        pass

    def _close_curtains(self) -> None:
        for curtain in self.curtains:
            curtain.close()
        self.curtains = []

    def _enter(self, phase: str) -> None:
        self.phase = phase
        self.clock.restart()
        if not self.timer.isActive():
            self.timer.start()
        self._tick()

    def _elapsed(self) -> float:
        return self.clock.elapsed() / self.time_scale

    # ---------- linha do tempo ----------
    def _tick(self) -> None:
        t = self._elapsed()
        phase = self.phase
        farewell = self.farewell

        if phase == "intro" and farewell is not None:
            self.level = DIM * ease_out(t / INTRO_MS)
            self.baton_alpha = max(0.0, 1.0 - (t / BATON_MS)) if self.island_pixmap else 0.0
            self.switch_alpha = 1.0 - self.baton_alpha
            k = ease_out((t - TEXT_DELAY_MS) / TEXT_MS)
            self.text_alpha, self.rise = k, 18 * (1 - k)
            self.sub_alpha = ease_out((t - TEXT_DELAY_MS - 140) / TEXT_MS)
            self.progress = max(0.0, min(1.0, (t - TEXT_DELAY_MS) / farewell.hold_ms))
            self.switch_on = max(0.0, 1.0 - self.progress)
            self.switch_outline = min(1.0, self.progress * 1.4)
            if t >= TEXT_DELAY_MS + farewell.hold_ms:
                self.cancellable = False
                self._start_level = self.level
                self._enter("go")
                return

        elif phase == "go" and farewell is not None:
            k = ease_out(t / GO_MS)
            self.level = self._start_level + (1.0 - self._start_level) * k
            self.switch_on = 0.0
            self.switch_outline = 1.0
            if self.key in ("shutdown", "update_shutdown"):
                self.crt = k
            elif self.key in ("restart", "update_restart"):
                self.breath = 0.55 + 0.45 * math.sin(k * math.pi * 2)
            elif self.key == "sleep":
                self.breath = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(k * math.pi * 2))
                self.switch_dim = 0.6
            elif self.key == "hibernate":
                self.sink_y = 20.0 * k
                self.switch_dim = 1.0 - 0.5 * k
            elif self.key == "lock":
                self.switch_dim = 1.0 - 0.35 * motion.mola(k)
            elif self.key == "logoff":
                self.rise = -36.0 * k
                self.switch_alpha = max(0.0, 1.0 - k * 0.85)
            if t >= GO_MS and not self._ran:
                self._ran = True
                self._run()
                return

        elif phase == "rest" and farewell is not None:
            if self.key == "sleep":
                self.breath = 0.3 + 0.7 * (0.5 + 0.5 * math.sin(t / 450.0 * math.pi))
            if t >= farewell.rest_ms:
                self._start_level, self._start_text = self.level, self.text_alpha
                self._enter("out")
                return

        elif phase == "gone":
            if self.key in ("restart", "update_restart"):
                self.breath = 0.5 + 0.5 * math.sin(t / 350.0 * math.pi)
            if t >= SAFETY_MS:
                self._start_level, self._start_text = self.level, self.text_alpha
                msg = "O Windows não concluiu a ação. Algum app pode ter segurado."
                toaster = getattr(self.ctx, "toast_requested", None)
                if toaster is not None:
                    from ventude.ui.toast import Toast

                    toaster.emit(Toast("Energia", msg, icon="alert", color=theme.WARN, seconds=5.0))
                tray = getattr(self.ctx, "tray_message", None)
                if tray is not None:
                    tray.emit("Energia", msg)
                self._enter("out")
                return

        elif phase == "error":
            self.shake_x = 6.0 * math.sin(t / 55.0 * math.pi * 2) * max(0.0, 1.0 - t / 450.0)
            self.switch_outline = 1.0
            if t >= 2600:
                self._start_level, self._start_text = self.level, self.text_alpha
                self._enter("out")
                return

        elif phase in ("out", "cancel"):
            span = CANCEL_MS if phase == "cancel" else OUT_MS
            k = ease_in_out(t / span)
            self.level = self._start_level * (1 - k)
            self.text_alpha = self._start_text * (1 - min(1.0, k * 1.6))
            self.sub_alpha = min(self.sub_alpha, self.text_alpha)
            if phase == "cancel":
                self.switch_on = min(1.0, k * 1.4)
                self.switch_outline = max(0.0, 1.0 - k * 2.0)
                self.return_t = k
                self.switch_alpha = max(0.0, 1.0 - max(0.0, (k - 0.65) / 0.35))
            else:
                self.return_t = k if self.key == "greet" else 0.0
                self.switch_alpha = 1.0 - k
            if t >= span:
                self._finish("cancel" if phase == "cancel" else self.key)
                return

        elif phase == "greet":
            on_k = max(0.0, min(1.0, (t - 80) / 380))
            self.switch_on = motion.mola(on_k) if on_k > 0 else 0.0
            k = ease_out((t - 150) / 600)
            self.text_alpha, self.rise = k, 14 * (1 - k)
            self.sub_alpha = ease_out((t - 330) / 600)
            if t >= 1900:
                self._start_level, self._start_text = self.level, self.text_alpha
                self.key = "greet"
                self._enter("out")
                return

        for curtain in self.curtains:
            curtain.update()

    def _run(self) -> None:
        farewell = self.farewell
        assert farewell is not None
        for curtain in self.curtains:
            curtain.setCursor(Qt.CursorShape.BlankCursor)
            curtain.repaint()
        try:
            ok, message = self._runner(self.key)
        except Exception as exc:  # noqa: BLE001
            log.exception("Ação de energia falhou")
            ok, message = False, str(exc)
        for curtain in self.curtains:
            curtain.unsetCursor()
        if not ok:
            self.error = True
            self.title = "Não deu"
            self.subtitle = message or "O Windows recusou o pedido."
            self.icon = "alert"
            self.show_progress = False
            self.crt = 0.0
            self.sink_y = 0.0
            self.key = f"erro:{self.subtitle}"
            self._enter("error")
            return
        if farewell.resumes:
            self._enter("rest")
        else:
            self.show_progress = False
            self.pulse_on = True
            self._enter("gone")

    def _finish(self, result: str) -> None:
        self.timer.stop()
        was_greet = result == "greet"
        self.phase = ""
        self._close_curtains()
        self._restore_island()
        self._reset_visuals()
        if was_greet:
            self.greet_landed.emit()
        self.finished.emit(result)

    def skip(self) -> None:
        """Testes e prévias: encerra na hora, sem executar nada."""
        self._finish("cancel")
