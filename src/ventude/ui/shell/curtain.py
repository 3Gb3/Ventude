"""Animações de energia do Ventude Shell: antes de desligar, reiniciar, suspender, bloquear ou sair, todas as
telas escurecem, aparece uma despedida ("Até logo!") com uma linha de contagem e só então o Windows age.

Durante a contagem dá para desistir com Esc ou no botão "Cancelar". Depois de bloquear, suspender ou
hibernar, a cortina some sozinha (quando você volta, a área de trabalho já está lá). Ao ligar o PC, a
mesma cortina faz o caminho inverso com "Bom dia, Fulano!".
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QElapsedTimer, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from ventude.core.timefmt import MONTHS, weekday_name
from ventude.services import apps, power
from ventude.ui import icons, theme
from ventude.ui.shell.common import shell_font

log = logging.getLogger(__name__)

DIM = 0.9  # escuridão durante a mensagem
INTRO_MS = 450
TEXT_DELAY_MS = 180
TEXT_MS = 520
GO_MS = 280
OUT_MS = 650
CANCEL_MS = 380
SAFETY_MS = 15_000  # desligar/reiniciar que não aconteceu: a cortina sai e avisa


@dataclass(frozen=True)
class Farewell:
    key: str
    title: str
    subtitle: str
    icon: str
    hold_ms: int  # tempo da contagem (dá para cancelar)
    resumes: bool  # bloquear/suspender/hibernar: a cortina some depois
    rest_ms: int = 900  # quanto esperar depois da ação antes de sumir (quando resumes)


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


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 4 * t**3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


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
    """Uma cortina por monitor. Só a principal mostra a mensagem."""

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
        return QRectF(center.x() - 62, center.y() + 112, 124, 36)

    def paintEvent(self, _event: object) -> None:
        fx = self.fx
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        level = max(0.0, min(1.0, fx.level))
        center = QPointF(self.width() / 2, self.height() / 2)
        glow = QRadialGradient(center, max(self.width(), self.height()) * 0.7)
        glow.setColorAt(0.0, QColor(18, 22, 26, int(255 * level)))
        glow.setColorAt(1.0, QColor(0, 0, 0, int(255 * level)))
        p.fillRect(self.rect(), glow)
        if not self.primary or fx.text_alpha <= 0.01:
            return
        alpha = max(0.0, min(1.0, fx.text_alpha))
        rise = fx.rise
        accent = theme.color(theme.DANGER if fx.error else theme.ACCENT, alpha)
        # Selo com o ícone da ação
        badge = QRectF(center.x() - 30, center.y() - 118 + rise, 60, 60)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color(theme.ACCENT_SOFT if not fx.error else "#2E1B1F", alpha * 0.9))
        p.drawEllipse(badge)
        ring = QPen(accent, 2)
        p.setPen(ring)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(badge.adjusted(1, 1, -1, -1))
        p.setOpacity(alpha)
        glyph = icons.pixmap(fx.icon or "power", theme.DANGER if fx.error else theme.ACCENT, 28)
        p.drawPixmap(badge.center() - QPointF(14, 14), glyph)
        # Título
        p.setOpacity(1.0)
        p.setPen(theme.color(theme.TEXT, alpha))
        title_font = shell_font(54, QFont.Weight.DemiBold, display=True)
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.5 + 1.5 * (1 - alpha))
        p.setFont(title_font)
        p.drawText(
            QRectF(0, center.y() - 46 + rise, self.width(), 76), Qt.AlignmentFlag.AlignCenter, fx.title
        )
        # Subtítulo
        sub_alpha = max(0.0, min(1.0, fx.sub_alpha))
        p.setPen(theme.color(theme.TEXT_2, sub_alpha))
        p.setFont(shell_font(16))
        p.drawText(
            QRectF(0, center.y() + 34 + rise * 0.6, self.width(), 30),
            Qt.AlignmentFlag.AlignCenter,
            fx.subtitle,
        )
        # Linha de contagem
        if fx.show_progress:
            track = QRectF(center.x() - 110, center.y() + 84, 220, 3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, int(28 * sub_alpha)))
            p.drawRoundedRect(track, 1.5, 1.5)
            p.setBrush(theme.color(theme.ACCENT, sub_alpha))
            p.drawRoundedRect(QRectF(track.left(), track.top(), track.width() * fx.progress, 3), 1.5, 1.5)
        elif fx.pulse_on:
            for index in range(3):
                phase = (time.monotonic() * 2.2 - index * 0.22) % 1.0
                dot_alpha = 0.25 + 0.75 * max(0.0, 1 - abs(phase - 0.5) * 2)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.color(theme.TEXT_2, dot_alpha * sub_alpha))
                p.drawEllipse(QPointF(center.x() - 14 + index * 14, center.y() + 86), 3, 3)
        # Cancelar
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
                QRectF(0, self.height() - 64, self.width(), 24), Qt.AlignmentFlag.AlignCenter, "Esc cancela"
            )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.fx.cancel()

    def mouseMoveEvent(self, _event: QMouseEvent) -> None:
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.primary and self.cancel_rect().contains(event.position()):
            self.fx.cancel()


class PowerFx(QObject):
    """Linha do tempo da cortina: entrada → contagem → ação → (volta ou fica preto)."""

    finished = Signal(str)  # chave da ação, "cancel", "greet" ou "erro:..."

    def __init__(
        self,
        ctx: object,
        runner: Callable[[str], tuple[bool, str]] | None = None,
        time_scale: float = 1.0,
    ) -> None:
        super().__init__()
        self.ctx = ctx
        self._runner = runner or power.run
        self.time_scale = max(0.001, time_scale)  # testes aceleram a animação
        self.curtains: list[Curtain] = []
        self.timer = QTimer(self, interval=16)
        self.timer.timeout.connect(self._tick)
        self.clock = QElapsedTimer()
        self.phase = ""
        self.key = ""
        self.farewell: Farewell | None = None
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
        self._start_level = 0.0
        self._start_text = 0.0
        self._ran = False

    @property
    def active(self) -> bool:
        return bool(self.phase)

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
        self._open_curtains(focus=True)
        self._enter("intro")
        return True

    def greet(self, name: str, detail: str = "") -> bool:
        """'Bom dia, Fulano!' ao ligar o PC: a cortina começa preta e se abre."""
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
        self.show_progress = False
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
            k = ease_out((t - TEXT_DELAY_MS) / TEXT_MS)
            self.text_alpha, self.rise = k, 18 * (1 - k)
            self.sub_alpha = ease_out((t - TEXT_DELAY_MS - 140) / TEXT_MS)
            self.progress = max(0.0, min(1.0, (t - TEXT_DELAY_MS) / farewell.hold_ms))
            if t >= TEXT_DELAY_MS + farewell.hold_ms:
                self.cancellable = False
                self._start_level = self.level
                self._enter("go")
                return
        elif phase == "go":
            self.level = self._start_level + (1.0 - self._start_level) * ease_out(t / GO_MS)
            if t >= GO_MS and not self._ran:
                self._ran = True
                self._run()
                return
        elif phase == "rest" and farewell is not None:
            if t >= farewell.rest_ms:
                self._start_level, self._start_text = self.level, self.text_alpha
                self._enter("out")
                return
        elif phase == "gone":
            if t >= SAFETY_MS:  # o Windows não desligou (um app segurou?): devolve a tela
                self._start_level, self._start_text = self.level, self.text_alpha
                tray = getattr(self.ctx, "tray_message", None)
                if tray is not None:
                    tray.emit("Energia", "O Windows não concluiu a ação. Algum app pode ter segurado.")
                self._enter("out")
                return
        elif phase == "error":
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
            if t >= span:
                self._finish("cancel" if phase == "cancel" else self.key)
                return
        elif phase == "greet":
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
        self.phase = ""
        self._close_curtains()
        self._reset_visuals()
        self.finished.emit(result)

    def skip(self) -> None:
        """Testes e prévias: encerra na hora, sem executar nada."""
        self._finish("cancel")
