"""Barra de tarefas do Ventude Shell, no lugar da barra do Windows.

Esquerda: clima de agora (com aviso de chuva). Centro: botão Iniciar (logo do Ventude), Pesquisa, Visão de
tarefas, apps fixados e janelas abertas agrupadas como o Windows faz (AppUserModelID, pacote da Loja ou
.exe). Direita: ícones ocultos da bandeja, rede/volume/bateria (painel rápido), relógio (calendário) e o
cantinho de "mostrar a área de trabalho".

Indicador embaixo do ícone: traço curto = aberto, traço longo na cor do Ventude = em foco. Clique foca ou
minimiza, clique do meio abre outra janela, botão direito mostra o menu e parar o mouse mostra as prévias
ao vivo das janelas. Ao abrir um app pelo ícone, ele "pula" até a janela aparecer.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QCursor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QHBoxLayout, QMenu, QWidget

from ventude.core.store import Pin
from ventude.core.timefmt import MONTHS, clock, weekday_name
from ventude.services import apps
from ventude.services.taskbar import WindowInfo
from ventude.ui import icons, theme
from ventude.ui.shell.common import (
    ACTIVE,
    HOVER,
    PRESSED,
    TASKBAR_BG,
    HoverButton,
    ShellWindow,
    app_pixmap,
    draw_pixmap,
    shell_font,
)

HEIGHT = 48
BUTTON = QSize(44, 40)
ICON = 24
EDGE_PX = 2


def norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)) if path else ""


@dataclass
class TaskItem:
    key: str
    label: str
    kind: str  # app | uwp | folder | file | url | system
    target: str  # o que abrir quando não há janela (ou para abrir outra)
    icon_kind: str
    icon_target: str
    pin_id: int = 0
    exe: str = ""
    app_id: str = ""
    windows: list[WindowInfo] = field(default_factory=list)

    @property
    def pinned(self) -> bool:
        return self.pin_id > 0

    @property
    def active(self) -> bool:
        return any(w.foreground for w in self.windows)

    @property
    def launchable(self) -> bool:
        return bool(self.target)


def pin_identity(pin: Pin) -> tuple[str, str]:
    """(.exe normalizado, AppID) do que o fixado abre, para casar com as janelas abertas."""
    if pin.kind == "uwp":
        return "", pin.target
    if pin.kind == "app":
        exe = pin.target
        if exe.lower().endswith(".lnk"):
            from ventude.services.winutil import lnk_target

            exe = lnk_target(exe) or ""
        return (norm(exe) if exe.lower().endswith(".exe") else ""), ""
    return "", ""


HELPER_WORDS = ("helper", "host", "broker", "runtime", "renderer", "launcher")


def friendly_label(exe_label: str, title: str) -> str:
    """Nome do app na barra: 'Steam Client WebHelper' vira o título da janela ('Steam')."""
    if not exe_label:
        return title
    if title and any(word in exe_label.lower() for word in HELPER_WORDS):
        return title.split(" - ")[-1].strip()[:40] or exe_label
    return exe_label


def store_families(entries: list[object]) -> dict[str, str]:
    """Família do pacote (em minúsculas) -> AppID, dos apps da Loja do índice."""
    out: dict[str, str] = {}
    for entry in entries:
        if getattr(entry, "kind", "") == "uwp" and "!" in entry.path:  # type: ignore[attr-defined]
            out.setdefault(entry.path.split("!", 1)[0].lower(), entry.path)  # type: ignore[attr-defined]
    return out


def build_items(
    pins: list[Pin], windows: list[WindowInfo], families: dict[str, str] | None = None
) -> list[TaskItem]:
    """Fixados primeiro (na ordem), depois os apps abertos, juntando as janelas do mesmo app."""
    from ventude.services.winutil import app_name

    families = families or {}
    items: list[TaskItem] = []
    by_exe: dict[str, TaskItem] = {}
    by_app: dict[str, TaskItem] = {}
    by_family: dict[str, TaskItem] = {}
    for pin in pins:
        exe, app_id = pin_identity(pin)
        item = TaskItem(
            key=f"pin:{pin.id}",
            label=pin.label,
            kind=pin.kind,
            target=pin.target,
            icon_kind=pin.kind,
            icon_target=pin.target,
            pin_id=pin.id,
            exe=exe,
            app_id=app_id,
        )
        items.append(item)
        if exe:
            by_exe.setdefault(exe, item)
        if app_id:
            by_app.setdefault(app_id.lower(), item)
            by_family.setdefault(app_id.split("!", 1)[0].lower(), item)
    for window in windows:
        exe = norm(window.exe)
        family = apps.package_family(window.exe).lower()
        app_id = window.app_id.lower()
        item = (
            (by_app.get(app_id) if app_id else None)
            or (by_family.get(family) if family else None)
            or (by_exe.get(exe) if exe else None)
        )
        if item is None:
            store_id = families.get(family, "") if family else ""
            if store_id:
                kind, target = "uwp", store_id
            else:
                kind, target = "app", window.exe
            key = f"app:{app_id}" if app_id else (f"exe:{exe}" if exe else f"hwnd:{window.hwnd}")
            item = TaskItem(
                key=key,
                label=friendly_label(app_name(window.exe), window.title),
                kind=kind,
                target=target,
                icon_kind=kind,
                icon_target=target,
                exe=exe,
                app_id=window.app_id,
            )
            items.append(item)
            if exe:
                by_exe.setdefault(exe, item)
            if app_id:
                by_app[app_id] = item
            if family:
                by_family.setdefault(family, item)
        item.windows.append(window)
    return items


# ---------- botões ----------
class TaskButton(QWidget):
    """Um app na barra: ícone real, fundo no hover/foco e indicador animado embaixo."""

    hovered = Signal(object, bool)  # (botão, entrou?)

    def __init__(self, taskbar: Taskbar, item: TaskItem) -> None:
        super().__init__(taskbar)
        self.taskbar, self.item = taskbar, item
        self.setFixedSize(BUTTON)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._pressed = False
        self._indicator = self._target_indicator()
        self.ind_anim = QVariantAnimation(self)
        self.ind_anim.setDuration(200)
        self.ind_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.ind_anim.valueChanged.connect(self._set_indicator)
        self.appear = 0.0
        self.appear_anim = QVariantAnimation(self)
        self.appear_anim.setDuration(260)
        self.appear_anim.setStartValue(0.0)
        self.appear_anim.setEndValue(1.0)
        self.appear_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self.appear_anim.valueChanged.connect(self._set_appear)
        self.bounce = 0.0
        self.bounce_anim = QVariantAnimation(self)
        self.bounce_anim.setDuration(900)
        self.bounce_anim.setStartValue(0.0)
        self.bounce_anim.setEndValue(1.0)
        self.bounce_anim.valueChanged.connect(self._set_bounce)
        self.update_item(item)

    def _target_indicator(self) -> float:
        if not self.item.windows:
            return 0.0
        return 16.0 if self.item.active else 6.0

    def _set_indicator(self, value: object) -> None:
        self._indicator = float(value)  # type: ignore[arg-type]
        self.update()

    def _set_appear(self, value: object) -> None:
        self.appear = float(value)  # type: ignore[arg-type]
        self.update()

    def _set_bounce(self, value: object) -> None:
        t = float(value)  # type: ignore[arg-type]
        self.bounce = -7 * abs(math.sin(t * math.pi * 2)) * (1 - t)
        self.update()

    def pop_in(self) -> None:
        self.appear_anim.start()

    def jump(self) -> None:
        """App abrindo: o ícone pula (para quando a janela aparece)."""
        self.bounce_anim.stop()
        self.bounce_anim.setLoopCount(3)
        self.bounce_anim.start()

    def update_item(self, item: TaskItem) -> None:
        had_windows = bool(self.item.windows)
        self.item = item
        if item.windows and not had_windows and self.bounce_anim.state() == QVariantAnimation.State.Running:
            self.bounce_anim.stop()
            self.bounce = 0.0
        titles = [w.title for w in item.windows]
        tip = item.label if not titles else (item.label + "\n" + "\n".join(t[:70] for t in titles[:5]))
        self.setToolTip("" if item.windows else tip)
        target = self._target_indicator()
        if abs(target - self._indicator) > 0.1 and self.ind_anim.endValue() != target:
            self.ind_anim.stop()
            self.ind_anim.setStartValue(self._indicator)
            self.ind_anim.setEndValue(target)
            self.ind_anim.start()
        self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 0, -2, 0)
        bg = (
            PRESSED
            if self._pressed
            else (HOVER if self.underMouse() else (ACTIVE if self.item.active else None))
        )
        if bg is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(rect, 6, 6)
        scale = (
            (0.55 + 0.45 * self.appear)
            if self.appear_anim.state() == QVariantAnimation.State.Running
            else 1.0
        )
        if self._pressed:
            scale *= 0.88
        size = ICON * scale
        pixmap = app_pixmap(self.taskbar.ctx, self.item.icon_kind, self.item.icon_target, ICON, "window")
        top = (self.height() - ICON) / 2 - 2 + self.bounce
        p.setOpacity(
            min(1.0, 0.2 + self.appear)
            if self.appear_anim.state() == QVariantAnimation.State.Running
            else 1.0
        )
        draw_pixmap(p, pixmap, QRectF((self.width() - size) / 2, top + (ICON - size) / 2, size, size))
        p.setOpacity(1.0)
        if self._indicator > 0.5:
            width = self._indicator
            color = QColor(theme.ACCENT) if self.item.active else QColor(theme.TEXT_2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF((self.width() - width) / 2, self.height() - 4, width, 3), 1.5, 1.5)

    def enterEvent(self, _event: object) -> None:
        self.update()
        self.hovered.emit(self, True)

    def leaveEvent(self, _event: object) -> None:
        self._pressed = False
        self.update()
        self.hovered.emit(self, False)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        inside = self.rect().contains(event.position().toPoint())
        was = self._pressed
        self._pressed = False
        self.update()
        if not inside:
            return
        if event.button() == Qt.MouseButton.LeftButton and was:
            self.taskbar.shell.item_clicked(self)
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.taskbar.shell.launch_item(self.item, self)
        elif event.button() == Qt.MouseButton.RightButton:
            self.taskbar.shell.item_menu(self, event.globalPosition().toPoint())


class StartButton(HoverButton):
    """Botão Iniciar com o logo do Ventude (pílula com o ponto verde)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", "", "Iniciar", BUTTON, ICON, 6, parent)
        self.open = False

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = self.background()
        if bg is not None or self.open:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg or ACTIVE)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(3, 0, -3, 0), 6, 6)
        scale = 0.86 if self._pressed else 1.0
        w, h = 24 * scale, 13 * scale
        body = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        p.setPen(QPen(QColor("#5A616D"), 1.6))
        p.setBrush(QColor(theme.BG))
        p.drawRoundedRect(body, h / 2, h / 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        dot = h * 0.52
        p.drawEllipse(QRectF(body.right() - dot - h * 0.24, body.center().y() - dot / 2, dot, dot))
        p.setBrush(QColor(theme.TEXT_2))
        p.drawRoundedRect(QRectF(body.left() + h * 0.32, body.center().y() - 1.2, w * 0.34, 2.4), 1.2, 1.2)


class WeatherChip(QWidget):
    """Clima de agora no canto esquerdo, como o widget do Windows (com aviso de chuva)."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.glyph, self.color, self.line1, self.line2 = "cloud-sun", theme.SUN, "", ""
        self.setFixedHeight(BUTTON.height())
        self.hide()

    def set_forecast(self, forecast: object | None) -> None:
        if forecast is None:
            self.hide()
            return
        rain = forecast.rain_soon()  # type: ignore[attr-defined]
        self.glyph = "rain" if rain else forecast.icon  # type: ignore[attr-defined]
        self.color = theme.SKY if (rain or self.glyph not in ("sun", "cloud-sun")) else theme.SUN
        self.line1 = f"{forecast.temp:.0f}°C"  # type: ignore[attr-defined]
        self.line2 = f"Chuva provável · {rain}%" if rain else forecast.description  # type: ignore[attr-defined]
        place = forecast.place.split(",")[0]  # type: ignore[attr-defined]
        self.setToolTip(f"{place}: {self.line1}, {forecast.description}\nClique para ver a previsão")  # type: ignore[attr-defined]
        width = max(
            QFontMetrics(shell_font(13, QFont.Weight.DemiBold)).horizontalAdvance(self.line1),
            QFontMetrics(shell_font(12)).horizontalAdvance(self.line2),
        )
        self.setFixedWidth(min(220, 46 + width + 14))
        self.show()
        self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(HOVER)
            p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        draw_pixmap(p, icons.pixmap(self.glyph, self.color, 26), QRectF(10, (self.height() - 26) / 2, 26, 26))
        x = 46.0
        p.setPen(QColor(theme.TEXT))
        p.setFont(shell_font(13, QFont.Weight.DemiBold))
        p.drawText(QRectF(x, 3, self.width() - x - 6, 18), Qt.AlignmentFlag.AlignBottom, self.line1)
        p.setPen(QColor(theme.TEXT_2))
        p.setFont(shell_font(12))
        p.drawText(
            QRectF(x, 21, self.width() - x - 6, 16),
            Qt.AlignmentFlag.AlignTop,
            p.fontMetrics().elidedText(self.line2, Qt.TextElideMode.ElideRight, int(self.width() - x - 6)),
        )

    def enterEvent(self, _event: object) -> None:
        self.update()

    def leaveEvent(self, _event: object) -> None:
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()


class QuickButton(HoverButton):
    """Rede, volume e bateria juntos (abre o painel rápido). A roda do mouse muda o volume."""

    wheel = Signal(bool, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", "", "Rede e volume", QSize(84, BUTTON.height()), 16, 6, parent)
        self.network, self.volume, self.battery = "ethernet", "volume", ""
        self.battery_text = ""

    def set_state(self, network: str, volume: str, battery: str = "", battery_text: str = "") -> None:
        changed = (network, volume, battery, battery_text) != (
            self.network,
            self.volume,
            self.battery,
            self.battery_text,
        )
        self.network, self.volume, self.battery, self.battery_text = network, volume, battery, battery_text
        width = 26 * (3 if battery else 2) + 16
        if self.width() != width:
            self.setFixedWidth(width)
        if changed:
            self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = self.background()
        if bg is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 4, -1, -4), 6, 6)
        glyphs = [self.network or "wifi-off", self.volume]
        if self.battery:
            glyphs.append(self.battery)
        x = 8.0
        for glyph in glyphs:
            color = theme.WARN if glyph == "wifi-off" else theme.TEXT
            draw_pixmap(p, icons.pixmap(glyph, color, 16), QRectF(x + 5, (self.height() - 16) / 2, 16, 16))
            x += 26

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta:
            self.wheel.emit(delta > 0, max(1, abs(delta) // 120))
        event.accept()


class ClockButton(HoverButton):
    """Hora e data (como no Windows); abre o calendário."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", "", "", QSize(84, BUTTON.height()), 16, 6, parent)
        self.time_text, self.date_text = "", ""

    def set_time(self, now: datetime, h24: bool) -> None:
        time_text, date_text = clock(now, h24), f"{now:%d/%m/%Y}"
        if (time_text, date_text) != (self.time_text, self.date_text):
            self.time_text, self.date_text = time_text, date_text
            self.setToolTip(f"{weekday_name(now.date())}, {now.day} de {MONTHS[now.month - 1]} de {now.year}")
            self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = self.background()
        if bg is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 4, -1, -4), 6, 6)
        p.setPen(QColor(theme.TEXT))
        p.setFont(shell_font(12))
        right = Qt.AlignmentFlag.AlignRight
        p.drawText(QRectF(0, 4, self.width() - 12, 16), right | Qt.AlignmentFlag.AlignBottom, self.time_text)
        p.drawText(QRectF(0, 21, self.width() - 12, 16), right | Qt.AlignmentFlag.AlignTop, self.date_text)


class DesktopCorner(QWidget):
    """Cantinho da direita: clique mostra a área de trabalho (Win + D)."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(10, HEIGHT)
        self.setToolTip("Mostrar a área de trabalho")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setPen(QPen(QColor(255, 255, 255, 60 if self.underMouse() else 26), 1))
        p.drawLine(1, 12, 1, self.height() - 12)

    def enterEvent(self, _event: object) -> None:
        self.update()

    def leaveEvent(self, _event: object) -> None:
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


# ---------- a barra ----------
class Taskbar(ShellWindow):
    """Uma barra por monitor. O ShellController manda os itens e cuida das ações."""

    def __init__(self, ctx: object, shell: object, screen: object) -> None:
        super().__init__(ctx, radius=0, focus=False, bg=TASKBAR_BG)
        self.shell = shell
        self.screen_ = screen
        self.setWindowTitle("Ventude: barra de tarefas")
        self.slid = False
        self.items: list[TaskItem] = []
        self.buttons: dict[str, TaskButton] = {}
        # Esquerda
        self.weather = WeatherChip(self)
        # Centro
        self.center = QWidget(self)
        row = QHBoxLayout(self.center)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.start = StartButton(self.center)
        self.search = HoverButton("search", "", "Pesquisar (tecla Windows)", BUTTON, 19, 6, self.center)
        self.view = HoverButton("task-view", "", "Visão de tarefas", BUTTON, 19, 6, self.center)
        row.addWidget(self.start)
        row.addWidget(self.search)
        row.addWidget(self.view)
        self.apps_row = QHBoxLayout()
        self.apps_row.setSpacing(2)
        row.addLayout(self.apps_row)
        # O Qt recalcula o tamanho do grupo central depois: quando ele pedir, reposiciona tudo.
        self.center.installEventFilter(self)
        # Direita
        self.right = QWidget(self)
        tray = QHBoxLayout(self.right)
        tray.setContentsMargins(0, 0, 0, 0)
        tray.setSpacing(2)
        self.chevron = HoverButton(
            "chevron-up", "", "Mostrar ícones ocultos", QSize(32, BUTTON.height()), 16, 6
        )
        self.quick = QuickButton()
        self.clock = ClockButton()
        self.corner = DesktopCorner()
        for widget in (self.chevron, self.quick, self.clock):
            tray.addWidget(widget)
        tray.addSpacing(4)
        tray.addWidget(self.corner)
        self.anim = QPropertyAnimation(self, b"pos", self)
        self.anim.setDuration(240)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(self._slide_done)
        self.clock_timer = QTimer(self, interval=1000)
        self.clock_timer.timeout.connect(self.tick)
        self.tick()

    # ---------- geometria ----------
    def screen_rect(self) -> QRect:
        return self.screen_.geometry()  # type: ignore[attr-defined]

    def shown_rect(self) -> QRect:
        area = self.screen_rect()
        return QRect(area.left(), area.bottom() - HEIGHT + 1, area.width(), HEIGHT)

    def hidden_pos(self) -> QPoint:
        area = self.screen_rect()
        return QPoint(area.left(), area.bottom() - EDGE_PX + 1)

    def place(self) -> None:
        rect = self.shown_rect()
        self.resize(rect.size())
        if self.anim.state() != QPropertyAnimation.State.Running:
            self.move(self.hidden_pos() if self.slid else rect.topLeft())
        self.relayout()

    def start_bar(self, entrance: bool = True) -> None:
        self.slid = False
        rect = self.shown_rect()
        self.resize(rect.size())
        self.relayout()
        if entrance and self.animation_ms() > 0:
            self.move(rect.topLeft() + QPoint(0, HEIGHT))
            self.show()
            self.anim.stop()
            self.anim.setStartValue(self.pos())
            self.anim.setEndValue(rect.topLeft())
            self.anim.start()
        else:
            self.move(rect.topLeft())
            self.show()
        self.clock_timer.start()

    def stop_bar(self) -> None:
        self.clock_timer.stop()
        self.anim.stop()
        self.hide()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.center and event.type() == QEvent.Type.LayoutRequest:
            QTimer.singleShot(0, self.relayout)
        return super().eventFilter(watched, event)

    def relayout(self) -> None:
        width = self.width()
        for widget in (self.center, self.right):
            layout = widget.layout()
            if layout is not None:
                layout.invalidate()
                layout.activate()  # aplica já os botões novos (senão a largura fica a antiga)
        self.center.adjustSize()
        self.right.adjustSize()
        top = (HEIGHT - BUTTON.height()) // 2
        right_w = self.right.sizeHint().width()
        self.right.setGeometry(width - right_w - 2, top, right_w, BUTTON.height())
        center_w = self.center.sizeHint().width()
        align_left = getattr(self.ctx.settings, "taskbar_align", "center") == "left"  # type: ignore[attr-defined]
        weather_w = self.weather.width() if not self.weather.isHidden() else 0
        if align_left:
            x = 8
            self.weather.move(width - right_w - weather_w - 10, top)
        else:
            x = (width - center_w) // 2
            self.weather.move(8, top)
            min_x = 8 + weather_w + 8
            max_x = width - right_w - center_w - 10
            x = max(min_x, min(x, max_x))
        self.center.setGeometry(x, top, center_w, BUTTON.height())

    # ---------- itens ----------
    def set_items(self, items: list[TaskItem]) -> None:
        keys = [i.key for i in items]
        old = self.buttons
        if keys != [i.key for i in self.items]:
            for key, button in list(old.items()):
                if key not in keys:
                    self.apps_row.removeWidget(button)
                    button.deleteLater()
                    old.pop(key)
            new_buttons: dict[str, TaskButton] = {}
            for index, item in enumerate(items):
                button = old.get(item.key)
                if button is None:
                    button = TaskButton(self, item)
                    button.hovered.connect(self.shell.button_hovered)  # type: ignore[attr-defined]
                    if self.isVisible():
                        button.pop_in()
                else:
                    self.apps_row.removeWidget(button)
                    button.update_item(item)
                self.apps_row.insertWidget(index, button)
                new_buttons[item.key] = button
            self.buttons = new_buttons
            self.items = items
            self.center.adjustSize()
            self.relayout()
        else:
            self.items = items
            for item in items:
                self.buttons[item.key].update_item(item)

    def button_for(self, key: str) -> TaskButton | None:
        return self.buttons.get(key)

    # ---------- relógio, clima, rede e volume ----------
    def tick(self) -> None:
        settings = self.ctx.settings  # type: ignore[attr-defined]
        self.clock.set_time(datetime.now(), settings.clock_24h)

    def set_weather(self, forecast: object | None) -> None:
        enabled = getattr(self.ctx.settings, "taskbar_weather", True)  # type: ignore[attr-defined]
        self.weather.set_forecast(forecast if enabled else None)
        self.relayout()

    # ---------- some e volta (tela cheia / ocultar automaticamente) ----------
    def slide(self, away: bool) -> None:
        if away == self.slid and self.anim.state() != QPropertyAnimation.State.Running:
            return
        self.slid = away
        self.anim.stop()
        if not away and not self.isVisible():
            self.move(self.hidden_pos())
            self.show()
        self.anim.setStartValue(self.pos())
        self.anim.setEndValue(self.hidden_pos() if away else self.shown_rect().topLeft())
        self.anim.start()

    def _slide_done(self) -> None:
        if self.slid and self.ctx.settings.taskbar_autohide is False:  # type: ignore[attr-defined]
            self.hide()  # tela cheia: some de vez até a janela sair

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """Menu da barra só nas áreas vazias (os apps têm o menu deles)."""
        child = self.childAt(event.pos())
        if child is None or child in (self.center, self.right):
            self.shell.taskbar_menu(self, QCursor.pos())  # type: ignore[attr-defined]

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.fillRect(
            self.rect(), theme.color(self.bg, 0.86 if (self.glass_on or self.force_glass_look) else 0.97)
        )
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.drawLine(0, 0, self.width(), 0)


def styled_menu(parent: QWidget | None = None) -> QMenu:
    menu = QMenu(parent)
    menu.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    menu.setStyleSheet(
        f"QMenu {{ background: {theme.SURFACE}; border: 1px solid {theme.STROKE_2}; border-radius: 10px;"
        " padding: 6px; }"
        "QMenu::item { padding: 8px 26px 8px 12px; border-radius: 6px; }"
        f"QMenu::item:selected {{ background: {theme.SURFACE_3}; }}"
        f"QMenu::item:disabled {{ color: {theme.MUTED}; }}"
        f"QMenu::separator {{ height: 1px; background: {theme.STROKE_2}; margin: 5px 8px; }}"
    )
    return menu


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path
