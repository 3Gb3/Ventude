"""Tela de bloqueio do Ventude: relógio grande, data, clima, música e avisos em todas as telas.

Como funciona (e por que é segura):
- Depois de N minutos sem mexer no PC (Ajustes > Bloqueio; padrão 5, "Nunca" desliga) ou ao escolher
  "Bloquear", a tela do Ventude cobre todos os monitores.
- O Windows não deixa nenhum app desenhar a tela de senha dele, então a senha continua com o Windows:
  ao primeiro toque no teclado/mouse a tela chama o bloqueio de verdade (LockWorkStation) e o Windows pede
  a senha/PIN/Windows Hello. Sem senha ("Pedir a senha do Windows" desligado), o toque só tira a tela.
- Se quem estava usando mexe nos primeiros segundos depois de a tela aparecer sozinha, ela só some
  (como o protetor de tela do Windows), sem pedir senha.
- Não bloqueia sozinha com Cafeína ligada, com jogo/vídeo em tela cheia nem quando algum app pede a tela
  acesa (vídeo tocando no navegador, apresentação...).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QWidget

from ventude.core.config import LOCK_PRESETS_MIN
from ventude.core.timefmt import clock, long_date
from ventude.ui import icons, motion, theme

POLL_IDLE_MS = 5_000
POLL_WAKE_MS = 120
GRACE_S = 5.0  # mexer logo depois que a tela aparece sozinha não pede senha
ARM_DELAY_S = 0.6  # ignora o "eco" do clique/tecla que pediu o bloqueio
ES_DISPLAY_REQUIRED = 0x00000002
ES_SYSTEM_REQUIRED = 0x00000001


# ---------- leituras do Windows ----------
def idle_seconds() -> float:
    """Segundos desde o último toque no teclado/mouse (GetLastInputInfo)."""
    if sys.platform != "win32":
        return 0.0
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):  # type: ignore[attr-defined]
        return 0.0
    now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF  # type: ignore[attr-defined]
    return max(0.0, ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0)


def last_input_tick() -> int:
    if sys.platform != "win32":
        return 0
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))  # type: ignore[attr-defined]
    return int(info.dwTime)


def display_required() -> bool:
    """Algum app pediu a tela acesa agora (vídeo tocando, apresentação)? CallNtPowerInformation(16)."""
    if sys.platform != "win32":
        return False
    import ctypes

    state = ctypes.c_ulong(0)
    try:
        status = ctypes.windll.powrprof.CallNtPowerInformation(  # type: ignore[attr-defined]
            16, None, 0, ctypes.byref(state), ctypes.sizeof(state)
        )
    except (OSError, AttributeError):
        return False
    return status == 0 and bool(state.value & ES_DISPLAY_REQUIRED)


def lock_windows() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes

    return bool(ctypes.windll.user32.LockWorkStation())  # type: ignore[attr-defined]


def timeout_label(minutes: int) -> str:
    if minutes <= 0:
        return "Nunca"
    if minutes % 60 == 0:
        hours = minutes // 60
        return "1 hora" if hours == 1 else f"{hours} horas"
    return "1 minuto" if minutes == 1 else f"{minutes} minutos"


def should_auto_lock(
    idle_s: float,
    minutes: int,
    *,
    caffeine: bool = False,
    fullscreen: bool = False,
    keep_awake: bool = False,
) -> bool:
    """Regra pura do bloqueio sozinho (testável sem Windows)."""
    if minutes <= 0 or caffeine or fullscreen or keep_awake:
        return False
    return idle_s >= minutes * 60


# ---------- tela ----------
class LockPane(QWidget):
    """Uma tela (um monitor). Só a principal mostra relógio, clima e música; as outras só escurecem."""

    def __init__(self, owner: LockScreen, screen: object, primary: bool) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool,
        )
        self.owner = owner
        self.primary = primary
        self.setWindowTitle("Ventude: bloqueio")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.setMouseTracking(True)
        self.setGeometry(screen.geometry())  # type: ignore[attr-defined]
        self.fade = 0.0

    # entrada: qualquer coisa acorda
    def keyPressEvent(self, event: QKeyEvent) -> None:
        self.owner.input_seen()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.owner.input_seen()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.owner.mouse_moved(event.globalPosition())

    def wheelEvent(self, event: object) -> None:
        self.owner.input_seen()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self.fade)
        w, h = float(self.width()), float(self.height())
        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0.0, QColor(10, 12, 16))
        bg.setColorAt(1.0, QColor(4, 5, 7))
        p.fillRect(self.rect(), bg)
        glow = QRadialGradient(QPointF(w * 0.5, h * 0.36), max(w, h) * 0.55)
        glow.setColorAt(0.0, theme.color(theme.ACCENT, 0.10))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), glow)
        if not self.primary:
            return
        self._paint_content(p, w, h)

    def _paint_content(self, p: QPainter, w: float, h: float) -> None:
        ctx = self.owner.ctx
        now = datetime.now()
        h24 = bool(getattr(getattr(ctx, "settings", None), "clock_24h", True))
        # relógio
        p.setPen(QColor(theme.TEXT))
        p.setFont(theme.font(max(64, int(h * 0.13)), QFont.Weight.Light, display=True, tabular=True))
        top = h * 0.20
        clock_h = h * 0.17
        p.drawText(QRectF(0, top, w, clock_h), Qt.AlignmentFlag.AlignCenter, clock(now, h24))
        p.setFont(theme.font(max(16, int(h * 0.024)), QFont.Weight.Medium, display=True))
        p.setPen(QColor(theme.TEXT_2))
        date_text = long_date(now.date())
        p.drawText(
            QRectF(0, top + clock_h, w, h * 0.05),
            Qt.AlignmentFlag.AlignCenter,
            date_text[:1].upper() + date_text[1:],
        )

        # clima
        y = top + clock_h + h * 0.07
        forecast = getattr(ctx, "forecast", None)
        if forecast is not None:
            text = f"{forecast.temp:.0f}°  ·  {forecast.description}"
            place = str(getattr(forecast, "place", "") or "")
            if place:
                text += f"  ·  {place}"
            p.setFont(theme.font(15, QFont.Weight.Medium))
            tw = p.fontMetrics().horizontalAdvance(text)
            x = (w - tw - 30) / 2
            p.drawPixmap(QPointF(x, y + 2), icons.weather(forecast.icon, 22))
            p.setPen(QColor(theme.TEXT_2))
            p.drawText(QRectF(x + 30, y, tw + 4, 26), Qt.AlignmentFlag.AlignVCenter, text)
            y += 40

        # música
        media = getattr(getattr(ctx, "media", None), "state", None)
        if media is not None and getattr(media, "title", ""):
            card_w = min(460.0, w * 0.4)
            card = QRectF((w - card_w) / 2, y + 8, card_w, 64)
            p.setPen(QColor(255, 255, 255, 22))
            p.setBrush(QColor(255, 255, 255, 12))
            p.drawRoundedRect(card, 18, 18)
            playing = bool(getattr(media, "is_playing", False))
            p.drawPixmap(
                QPointF(card.left() + 18, card.center().y() - 11),
                icons.pixmap("music" if playing else "pause", theme.ACCENT if playing else theme.MUTED, 22),
            )
            p.setPen(QColor(theme.TEXT))
            p.setFont(theme.font(13, theme.SEMI))
            inner = card.adjusted(56, 12, -18, -12)
            title = p.fontMetrics().elidedText(media.title, Qt.TextElideMode.ElideRight, int(inner.width()))
            p.drawText(
                QRectF(inner.left(), inner.top(), inner.width(), 20), Qt.AlignmentFlag.AlignVCenter, title
            )
            p.setPen(QColor(theme.TEXT_2))
            p.setFont(theme.font(12))
            artist = p.fontMetrics().elidedText(
                getattr(media, "artist", "") or "", Qt.TextElideMode.ElideRight, int(inner.width())
            )
            p.drawText(
                QRectF(inner.left(), inner.top() + 20, inner.width(), 20),
                Qt.AlignmentFlag.AlignVCenter,
                artist,
            )

        # rodapé: avisos + dica
        center = getattr(ctx, "notifications", None)
        unread = int(getattr(center, "unread", 0) or 0)
        hint = self.owner.hint_text()
        lines = [hint]
        if unread:
            lines.insert(0, f"{unread} notificação nova" if unread == 1 else f"{unread} notificações novas")
        p.setFont(theme.font(13))
        base = h - 54 - 26 * (len(lines) - 1)
        for i, line in enumerate(lines):
            p.setPen(QColor(theme.ACCENT if (unread and i == 0) else theme.MUTED))
            p.drawText(QRectF(0, base + i * 26, w, 24), Qt.AlignmentFlag.AlignCenter, line)
        # cadeado
        p.drawPixmap(QPointF(w / 2 - 10, base - 40), icons.pixmap("lock", theme.TEXT_2, 20))


class LockScreen(QObject):
    """Controla a tela de bloqueio (todas as telas) e o bloqueio por inatividade."""

    shown = Signal()
    hidden = Signal()

    def __init__(
        self,
        ctx: object,
        *,
        idle_fn: Callable[[], float] = idle_seconds,
        lock_fn: Callable[[], bool] = lock_windows,
        tick_fn: Callable[[], int] = last_input_tick,
        busy_fn: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__()
        self.ctx = ctx
        self.idle_fn, self.lock_fn, self.tick_fn = idle_fn, lock_fn, tick_fn
        self.busy_fn = busy_fn or self._busy
        self.panes: list[LockPane] = []
        self.active = False
        self.auto = False
        self.preview = False  # "Ver a tela de bloqueio" nos Ajustes: nunca pede senha
        self.shown_at = 0.0
        self._start_tick = 0
        self._mouse_origin: QPointF | None = None
        self._leaving = False
        self._fade = motion.Tween(self)
        self._fade.progressed.connect(self._on_fade)
        self._fade.done.connect(self._on_fade_done)

        self.idle_timer = QTimer(self, interval=POLL_IDLE_MS)
        self.idle_timer.timeout.connect(self.check_idle)
        self.wake_timer = QTimer(self, interval=POLL_WAKE_MS)
        self.wake_timer.timeout.connect(self._check_wake)
        self.clock_timer = QTimer(self, interval=1_000)
        self.clock_timer.timeout.connect(self._repaint)

    # ---------- ciclo ----------
    def start(self) -> None:
        self.idle_timer.start()

    def stop(self) -> None:
        self.idle_timer.stop()
        self.hide_now()

    @property
    def minutes(self) -> int:
        return int(getattr(getattr(self.ctx, "settings", None), "lock_after_min", 5) or 0)

    @property
    def require_password(self) -> bool:
        return bool(getattr(getattr(self.ctx, "settings", None), "lock_require_password", True))

    def _busy(self) -> bool:
        caffeine = bool(getattr(getattr(self.ctx, "caffeine", None), "active", False))
        if caffeine:
            return True
        fx = getattr(self.ctx, "power_fx", None)
        if fx is not None and getattr(fx, "active", False):
            return True
        try:
            from ventude.services import taskbar

            for screen in QGuiApplication.screens():
                g = screen.geometry()
                ratio = screen.devicePixelRatio()
                rect = (g.x(), g.y(), g.x() + round(g.width() * ratio), g.y() + round(g.height() * ratio))
                if taskbar.covering_window(rect, fullscreen_only=True):
                    return True
        except Exception:  # noqa: BLE001 - na dúvida, não bloqueia
            return True
        return display_required()

    def check_idle(self) -> None:
        if self.active:
            return
        minutes = self.minutes
        if minutes <= 0:
            return
        idle = self.idle_fn()
        if idle < minutes * 60:
            return
        if self.busy_fn():
            return
        self.show_lock(auto=True)

    # ---------- mostrar / esconder ----------
    def show_lock(self, auto: bool = False, preview: bool = False) -> None:
        if self.active:
            return
        app = QApplication.instance()
        if app is None:
            return
        self.active, self.auto, self.preview, self._leaving = True, auto, preview, False
        self.shown_at = time.monotonic()
        self._start_tick = self.tick_fn()
        self._mouse_origin = None
        primary = QGuiApplication.primaryScreen()
        for screen in QGuiApplication.screens() or ([primary] if primary else []):
            pane = LockPane(self, screen, screen is primary)
            pane.show()
            pane.raise_()
            self.panes.append(pane)
        if self.panes:
            main = next((p for p in self.panes if p.primary), self.panes[0])
            main.activateWindow()
            main.setFocus()
        self.wake_timer.start()
        self.clock_timer.start()
        dur = motion.ms("desdobrar", getattr(self.ctx, "settings", None)) * 2
        if dur <= 0:
            self._on_fade(1.0)
        else:
            self._fade.play(dur, 0.0, 1.0, motion.named("suave"))
        self.shown.emit()

    def hint_text(self) -> str:
        if self.preview:
            return "Prévia · aperte uma tecla ou clique para voltar"
        if self.auto and time.monotonic() - self.shown_at < GRACE_S:
            return "Mexa o mouse para voltar"
        if self.require_password:
            return "Aperte uma tecla ou clique para desbloquear com a senha do Windows"
        return "Aperte uma tecla ou clique para voltar"

    def mouse_moved(self, pos: QPointF) -> None:
        if self._mouse_origin is None:
            self._mouse_origin = QPointF(pos)
            return
        delta = pos - self._mouse_origin
        if abs(delta.x()) + abs(delta.y()) > 12:
            self.input_seen()

    def _check_wake(self) -> None:
        """Toques que não chegam às janelas (outro app com foco): GetLastInputInfo mudou."""
        if not self.active or self._leaving:
            return
        tick = self.tick_fn()
        if tick and self._start_tick and tick != self._start_tick:
            self.input_seen()

    def input_seen(self) -> None:
        if not self.active or self._leaving:
            return
        elapsed = time.monotonic() - self.shown_at
        if elapsed < ARM_DELAY_S:
            self._start_tick = self.tick_fn()
            return
        in_grace = self.auto and elapsed < GRACE_S
        if self.require_password and not in_grace and not self.preview:
            self.lock_fn()
            # a tela de senha do Windows já está por cima; tira a nossa logo depois
            self._leaving = True
            QTimer.singleShot(900, self.hide_now)
            return
        self.dismiss()

    def dismiss(self) -> None:
        if not self.active or self._leaving:
            return
        self._leaving = True
        dur = motion.ms("desdobrar_close", getattr(self.ctx, "settings", None))
        if dur <= 0:
            self.hide_now()
        else:
            self._fade.play(dur, 1.0, 0.0, motion.in_cubic)

    def hide_now(self) -> None:
        self._fade.stop()
        self.wake_timer.stop()
        self.clock_timer.stop()
        for pane in self.panes:
            pane.hide()
            pane.close()
        self.panes = []
        was = self.active
        self.active = self._leaving = False
        if was:
            self.hidden.emit()

    def _on_fade(self, value: float) -> None:
        for pane in self.panes:
            pane.fade = max(0.0, min(1.0, value))
            pane.update()

    def _on_fade_done(self) -> None:
        if self._leaving:
            self.hide_now()

    def _repaint(self) -> None:
        for pane in self.panes:
            pane.update()


__all__ = [
    "LOCK_PRESETS_MIN",
    "LockPane",
    "LockScreen",
    "display_required",
    "idle_seconds",
    "should_auto_lock",
    "timeout_label",
]
