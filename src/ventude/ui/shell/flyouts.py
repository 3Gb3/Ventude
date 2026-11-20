"""Painéis que sobem da barra de tarefas: painel rápido (rede, volume, cafeína, ferramentas), calendário com
a agenda do dia, ícones ocultos da bandeja e o menu de Energia do Iniciar."""

from __future__ import annotations

import calendar
import time
from collections.abc import Callable
from datetime import date

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ventude.core.timefmt import MONTHS, weekday_name
from ventude.services import apps, hardware, power
from ventude.ui import icons, theme
from ventude.ui.shell.common import HOVER, HoverButton, ShellWindow, app_pixmap, draw_pixmap, shell_font

GAP = 12  # distância da barra de tarefas


class Flyout(ShellWindow):
    """Painel flutuante acima da barra: abre deslizando e fecha ao clicar fora (ou Esc)."""

    def __init__(self, ctx: object, shell: object, width: int) -> None:
        super().__init__(ctx)
        self.shell = shell
        self.setFixedWidth(width)
        self.opened_at = 0.0

    def open_above(self, anchor: QRect, screen_rect: QRect, taskbar_top: int, align: str = "right") -> None:
        self.opened_at = time.monotonic()
        self.adjustSize()
        size = self.sizeHint().expandedTo(self.minimumSizeHint())
        width, height = self.width(), max(size.height(), self.minimumHeight())
        if align == "center":
            x = anchor.center().x() - width // 2
        else:
            x = anchor.right() - width + 8
        x = max(screen_rect.left() + GAP, min(x, screen_rect.right() - width - GAP + 1))
        y = taskbar_top - height - GAP
        self.open_at(QRect(x, y, width, height), QPoint(0, 18))
        self.activateWindow()
        try:
            from ventude.services.winutil import force_foreground

            force_foreground(int(self.winId()))
        except Exception:  # noqa: BLE001
            pass

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self.isVisible():
            if time.monotonic() - self.opened_at > 0.3:
                QTimer.singleShot(80, self._close_if_inactive)

    def _close_if_inactive(self) -> None:
        if self.isVisible() and not self.isActiveWindow() and not self.closing:
            if QApplication.activePopupWidget() is None:
                self.close_animated()

    def keyPressEvent(self, event: object) -> None:
        if event.key() == Qt.Key.Key_Escape:  # type: ignore[attr-defined]
            self.close_animated()


# ---------- painel rápido ----------
class QuickTile(QWidget):
    """Bloco do painel rápido: ícone, nome e estado. Ligado = cor do Ventude."""

    clicked = Signal()

    def __init__(self, icon: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon_name, self.title, self.detail, self.on = icon, title, "", False
        self.setFixedSize(112, 70)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def set_state(
        self, on: bool, detail: str = "", icon: str | None = None, title: str | None = None
    ) -> None:
        self.on, self.detail = on, detail
        if icon:
            self.icon_name = icon
        if title:
            self.title = title
        self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.on:
            base = QColor(theme.ACCENT)
            if self.underMouse():
                base = base.lighter(108)
        else:
            base = QColor(255, 255, 255, 30 if self.underMouse() else 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(base)
        p.drawRoundedRect(rect, 8, 8)
        color = theme.ACCENT_TEXT if self.on else theme.TEXT
        draw_pixmap(p, icons.pixmap(self.icon_name, color, 18), QRectF(12, 11, 18, 18))
        p.setPen(QColor(color))
        p.setFont(shell_font(12, QFont.Weight.DemiBold))
        p.drawText(QRectF(12, 34, rect.width() - 16, 16), Qt.AlignmentFlag.AlignLeft, self.title)
        if self.detail:
            p.setFont(shell_font(11))
            detail_color = QColor(theme.ACCENT_TEXT) if self.on else QColor(theme.TEXT_2)
            p.setPen(detail_color)
            text = p.fontMetrics().elidedText(
                self.detail, Qt.TextElideMode.ElideRight, int(rect.width() - 18)
            )
            p.drawText(QRectF(12, 50, rect.width() - 16, 15), Qt.AlignmentFlag.AlignLeft, text)

    def enterEvent(self, _event: object) -> None:
        self.update()

    def leaveEvent(self, _event: object) -> None:
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()


def volume_icon(level: float | None, muted: bool) -> str:
    if muted or level is None or level <= 0.001:
        return "volume-x"
    return "volume-1" if level < 0.5 else "volume"


class QuickSettings(Flyout):
    def __init__(self, ctx: object, shell: object) -> None:
        super().__init__(ctx, shell, 392)
        self.setWindowTitle("Ventude: painel rápido")
        self.network = QuickTile("ethernet", "Rede")
        self.network.clicked.connect(lambda: self._open("ms-settings:network-status"))
        self.bluetooth = QuickTile("bluetooth", "Bluetooth")
        self.bluetooth.set_state(False, "Configurar")
        self.bluetooth.clicked.connect(lambda: self._open("ms-settings:bluetooth"))
        self.caffeine = QuickTile("coffee", "Cafeína")
        self.caffeine.clicked.connect(ctx.caffeine.toggle)  # type: ignore[attr-defined]
        self.mic = QuickTile("mic", "Microfone")
        self.mic.clicked.connect(self._toggle_mic)
        self.color = QuickTile("pipette", "Conta-gotas")
        self.color.set_state(False, "Copia a cor")
        self.color.clicked.connect(lambda: self._tool("color"))
        self.ocr = QuickTile("scan", "Capturar texto")
        self.ocr.set_state(False, "OCR da tela")
        self.ocr.clicked.connect(lambda: self._tool("ocr"))
        self.focus = QuickTile("focus", "Foco")
        self.focus.clicked.connect(self._toggle_focus)
        self.night = QuickTile("night", "Luz noturna")
        self.night.set_state(False, "Configurar")
        self.night.clicked.connect(lambda: self._open("ms-settings:nightlight"))
        self.project = QuickTile("monitor", "Projetar")
        self.project.set_state(False, "Outra tela")
        self.project.clicked.connect(self._project)
        grid = QGridLayout()
        grid.setSpacing(8)
        for index, tile in enumerate(
            (
                self.network,
                self.bluetooth,
                self.caffeine,
                self.mic,
                self.color,
                self.ocr,
                self.focus,
                self.night,
                self.project,
            )
        ):
            grid.addWidget(tile, index // 3, index % 3)
        self.mute = HoverButton("volume", "", "Mudo", QSize(34, 34), 18, 6)
        self.mute.clicked.connect(self._toggle_mute)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,0.18);"
            " border-radius: 2px; }"
            f"QSlider::sub-page:horizontal {{ background: {theme.ACCENT}; border-radius: 2px; }}"
            "QSlider::handle:horizontal { width: 16px; height: 16px; margin: -6px 0;"
            f" background: {theme.ACCENT}; border: 3px solid {theme.SURFACE_3}; border-radius: 8px; }}"
        )
        self.slider.valueChanged.connect(self._volume_moved)
        self.level = QLabel("")
        self.level.setFixedWidth(36)
        self.level.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 12px;")
        self.devices_btn = HoverButton("chevron-right", "", "Escolher a saída de áudio", QSize(34, 34), 16, 6)
        self.devices_btn.clicked.connect(self._toggle_devices)
        volume = QHBoxLayout()
        volume.setSpacing(6)
        volume.addWidget(self.mute)
        volume.addWidget(self.slider, 1)
        volume.addWidget(self.level)
        volume.addWidget(self.devices_btn)
        self.devices = QVBoxLayout()
        self.devices.setSpacing(2)
        self.devices_box = QWidget()
        self.devices_box.setLayout(self.devices)
        self.devices_box.hide()
        self.battery = QLabel("")
        self.battery.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 12px;")
        ventude = HoverButton("ventude", "", "Ajustes do Ventude Shell", QSize(34, 34), 18, 6)
        ventude.icon_color = theme.ACCENT
        ventude.clicked.connect(lambda: (self.close_animated(), shell.open_settings()))  # type: ignore[attr-defined]
        windows = HoverButton("settings", "", "Configurações do Windows", QSize(34, 34), 17, 6)
        windows.clicked.connect(lambda: self._open("ms-settings:"))
        bottom = QHBoxLayout()
        bottom.addWidget(self.battery)
        bottom.addStretch(1)
        bottom.addWidget(ventude)
        bottom.addWidget(windows)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(255,255,255,0.08);")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 16, 12)
        root.setSpacing(14)
        root.addLayout(grid)
        root.addLayout(volume)
        root.addWidget(self.devices_box)
        root.addWidget(line)
        root.addLayout(bottom)
        self._updating = False
        ctx.audio.changed.connect(self._audio)  # type: ignore[attr-defined]
        ctx.caffeine.changed.connect(lambda _on: self.refresh())  # type: ignore[attr-defined]
        ctx.timer.changed.connect(lambda _s: self.refresh())  # type: ignore[attr-defined]
        self.network_kind = ""
        self.wifi = ""

    def refresh(self) -> None:
        ctx = self.ctx
        kind = self.network_kind
        if kind == "wifi":
            self.network.set_state(True, self.wifi or "Conectado", "wifi", "Wi-Fi")
        elif kind == "ethernet":
            self.network.set_state(True, "Conectado", "ethernet", "Ethernet")
        else:
            self.network.set_state(False, "Sem conexão", "wifi-off", "Rede")
        caffeine = ctx.caffeine  # type: ignore[attr-defined]
        self.caffeine.set_state(caffeine.active, "Tela acesa" if caffeine.active else "Desligada")
        timer = ctx.timer.state  # type: ignore[attr-defined]
        self.focus.set_state(timer is not None, "Em andamento" if timer is not None else "25 min")
        self._audio(ctx.audio.state)  # type: ignore[attr-defined]
        battery = hardware.battery() if hasattr(hardware, "battery") else None
        if battery is not None:
            self.battery.setText(
                f"Bateria {battery.percent:.0f}%" + (" · carregando" if battery.charging else "")
            )
        else:
            self.battery.setText("")

    def set_network(self, kind: str, wifi: str = "") -> None:
        self.network_kind, self.wifi = kind, wifi
        self.refresh()

    def _audio(self, state: object) -> None:
        master = getattr(state, "master", None)
        muted = bool(getattr(state, "master_muted", False))
        self._updating = True
        self.slider.setEnabled(master is not None)
        self.slider.setValue(round((master or 0) * 100))
        self._updating = False
        self.level.setText(f"{round((master or 0) * 100)}%" if master is not None else "—")
        self.mute.set_icon(volume_icon(master, muted), theme.DANGER if muted else theme.TEXT)
        mic_muted = bool(getattr(state, "mic_muted", False))
        has_mic = getattr(state, "mic", None) is not None
        self.mic.set_state(
            has_mic and not mic_muted,
            "Mudo" if mic_muted else ("Ligado" if has_mic else "Nenhum"),
            "mic-off" if mic_muted else "mic",
        )
        self._fill_devices(getattr(state, "outputs", ()))

    def _fill_devices(self, outputs: tuple) -> None:
        while self.devices.count():
            item = self.devices.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for device in outputs:
            button = HoverButton(
                "check" if device.default else "speaker", device.name, "", QSize(10, 34), 15, 6
            )
            button.setMinimumWidth(10)
            button.setMaximumWidth(16777215)
            if device.default:
                button.icon_color = theme.ACCENT
            button.clicked.connect(lambda d=device.id: self.ctx.audio.set_default(d))  # type: ignore[attr-defined]
            self.devices.addWidget(button)

    def _volume_moved(self, value: int) -> None:
        if self._updating:
            return
        self.ctx.audio.set_master(value / 100)  # type: ignore[attr-defined]
        self.level.setText(f"{value}%")

    def _toggle_mute(self) -> None:
        state = self.ctx.audio.state  # type: ignore[attr-defined]
        self.ctx.audio.set_master_mute(not state.master_muted)  # type: ignore[attr-defined]

    def _toggle_mic(self) -> None:
        self.ctx.audio.toggle_mic_mute()  # type: ignore[attr-defined]

    def _toggle_devices(self) -> None:
        show = self.devices_box.isHidden()
        self.devices_box.setVisible(show)
        self.devices_btn.set_icon("chevron-down" if show else "chevron-right")
        self.adjustSize()
        bottom = self.geometry().bottom()
        self.setGeometry(
            self.x(), bottom - self.sizeHint().height() + 1, self.width(), self.sizeHint().height()
        )

    def _toggle_focus(self) -> None:
        timer = self.ctx.timer  # type: ignore[attr-defined]
        if timer.state is not None:
            timer.stop()
        else:
            timer.start_pomodoro()
        self.refresh()

    def _tool(self, name: str) -> None:
        self.close_animated()
        QTimer.singleShot(220, lambda: self.ctx.tool_requested.emit(name))  # type: ignore[attr-defined]

    def _open(self, target: str) -> None:
        self.close_animated()
        apps.open_system(target)

    def _project(self) -> None:
        self.close_animated()
        apps.send_keys(0x5B, 0x50)  # Win + P


# ---------- calendário ----------
class MonthGrid(QWidget):
    picked = Signal(object)  # date

    def __init__(self, ctx: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        today = date.today()
        self.year, self.month, self.selected = today.year, today.month, today
        self.marks: set[int] = set()
        self.hover = -1
        self.setFixedSize(7 * 44, 7 * 38)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def days(self) -> list[date | None]:
        weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(self.year, self.month)
        out: list[date | None] = []
        for week in weeks[:6]:
            out += [d if d.month == self.month else None for d in week]
        return out

    def shift(self, months: int) -> None:
        month = self.month - 1 + months
        self.year, self.month = self.year + month // 12, month % 12 + 1
        self.refresh()

    def refresh(self) -> None:
        self.marks = self.ctx.store.days_with_events(self.year, self.month)  # type: ignore[attr-defined]
        self.setFixedHeight(38 + (len(self.days()) // 7) * 38)  # 4 a 6 semanas, sem linha vazia
        self.update()

    def cell(self, index: int) -> QRectF:
        row, col = divmod(index, 7)
        return QRectF(col * 44, 38 + row * 38, 44, 38)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(shell_font(12, QFont.Weight.DemiBold))
        p.setPen(QColor(theme.TEXT_2))
        for col, letter in enumerate("DSTQQSS"):
            p.drawText(QRectF(col * 44, 0, 44, 34), Qt.AlignmentFlag.AlignCenter, letter)
        today = date.today()
        for index, day in enumerate(self.days()):
            if day is None:
                continue
            rect = self.cell(index)
            circle = QRectF(rect.center().x() - 16, rect.center().y() - 16, 32, 32)
            p.setPen(Qt.PenStyle.NoPen)
            if day == today:
                p.setBrush(QColor(theme.ACCENT))
                p.drawEllipse(circle)
            elif day == self.selected:
                p.setBrush(QColor(255, 255, 255, 30))
                p.drawEllipse(circle)
            elif index == self.hover:
                p.setBrush(HOVER)
                p.drawEllipse(circle)
            p.setPen(QColor(theme.ACCENT_TEXT if day == today else theme.TEXT))
            p.setFont(shell_font(13, QFont.Weight.DemiBold if day == today else QFont.Weight.Normal))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(day.day))
            if day.day in self.marks:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.ACCENT_TEXT if day == today else theme.ACCENT))
                p.drawEllipse(QRectF(rect.center().x() - 2, rect.center().y() + 9, 4, 4))

    def index_at(self, pos: QPoint) -> int:
        days = self.days()
        for index in range(len(days)):
            if days[index] is not None and self.cell(index).contains(pos.toPointF()):
                return index
        return -1

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        index = self.index_at(event.position().toPoint())
        if index != self.hover:
            self.hover = index
            self.update()

    def leaveEvent(self, _event: object) -> None:
        self.hover = -1
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        index = self.index_at(event.position().toPoint())
        if index >= 0:
            day = self.days()[index]
            self.selected = day  # type: ignore[assignment]
            self.update()
            self.picked.emit(day)


class CalendarFlyout(Flyout):
    def __init__(self, ctx: object, shell: object) -> None:
        super().__init__(ctx, shell, 356)
        self.setWindowTitle("Ventude: calendário")
        self.heading = QLabel("")
        self.heading.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.month_label = QLabel("")
        self.month_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        up = HoverButton("chevron-up", "", "Mês anterior", QSize(30, 30), 15, 6)
        down = HoverButton("chevron-down", "", "Próximo mês", QSize(30, 30), 15, 6)
        self.grid = MonthGrid(ctx)
        up.clicked.connect(lambda: self._shift(-1))
        down.clicked.connect(lambda: self._shift(1))
        self.grid.picked.connect(self._picked)
        month_row = QHBoxLayout()
        month_row.addWidget(self.month_label)
        month_row.addStretch(1)
        month_row.addWidget(up)
        month_row.addWidget(down)
        self.day_label = QLabel("Hoje")
        self.day_label.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 12px; font-weight: 600;")
        self.events = QVBoxLayout()
        self.events.setSpacing(4)
        agenda = HoverButton("calendar", "Abrir a Agenda", "", QSize(10, 34), 15, 6)
        agenda.setMinimumWidth(10)
        agenda.setMaximumWidth(16777215)
        agenda.clicked.connect(lambda: (self.close_animated(), ctx.navigate_requested.emit("agenda")))  # type: ignore[attr-defined]
        focus = HoverButton("focus", "Foco de 25 min (Pomodoro)", "", QSize(10, 34), 15, 6)
        focus.setMinimumWidth(10)
        focus.setMaximumWidth(16777215)
        focus.icon_color = theme.ACCENT
        focus.clicked.connect(lambda: (ctx.timer.start_pomodoro(), self.close_animated()))  # type: ignore[attr-defined]
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 14)
        root.setSpacing(10)
        root.addWidget(self.heading)
        root.addLayout(month_row)
        root.addWidget(self.grid, 0, Qt.AlignmentFlag.AlignHCenter)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(255,255,255,0.08);")
        root.addWidget(line)
        root.addWidget(self.day_label)
        root.addLayout(self.events)
        root.addWidget(agenda)
        root.addWidget(focus)
        self._event_widgets: list[QWidget] = []

    def refresh(self) -> None:
        today = date.today()
        self.heading.setText(f"{weekday_name(today)}, {today.day} de {MONTHS[today.month - 1]}")
        self.grid.year, self.grid.month, self.grid.selected = today.year, today.month, today
        self.grid.refresh()
        self._month_title()
        self._picked(today)

    def _month_title(self) -> None:
        self.month_label.setText(f"{MONTHS[self.grid.month - 1].capitalize()} de {self.grid.year}")

    def _shift(self, months: int) -> None:
        self.grid.shift(months)
        self._month_title()
        self.adjustSize()
        bottom = self.geometry().bottom()
        height = self.sizeHint().height()
        self.setGeometry(self.x(), bottom - height + 1, self.width(), height)

    def _picked(self, day: date) -> None:
        for widget in self._event_widgets:
            self.events.removeWidget(widget)
            widget.deleteLater()
        self._event_widgets = []
        today = date.today()
        self.day_label.setText(
            "Hoje" if day == today else f"{weekday_name(day)}, {day.day} de {MONTHS[day.month - 1]}"
        )
        events = self.ctx.store.events_on(day)  # type: ignore[attr-defined]
        if not events:
            empty = QLabel("Nada marcado. Digite “a: reunião 15h” na busca para agendar.")
            empty.setWordWrap(True)
            empty.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;")
            self.events.addWidget(empty)
            self._event_widgets.append(empty)
        for event in events[:5]:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            bar = QWidget()
            bar.setFixedSize(3, 30)
            bar.setStyleSheet(f"background: {theme.ACCENT}; border-radius: 1px;")
            text = QLabel(f"<b>{event.title}</b><br><span style='color:{theme.TEXT_2}'>{event.when}</span>")
            text.setStyleSheet("font-size: 12px;")
            layout.addWidget(bar)
            layout.addWidget(text, 1)
            self.events.addWidget(row)
            self._event_widgets.append(row)
        self.adjustSize()


# ---------- bandeja (ícones ocultos) ----------
class TrayIcon(QWidget):
    def __init__(self, app: apps.TrayApp, ctx: object, open_: Callable[[apps.TrayApp], None]) -> None:
        super().__init__()
        self.app, self.ctx, self._open = app, ctx, open_
        self.setFixedSize(44, 44)
        self.setToolTip(app.name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.pixmap = QPixmap()
        if app.icon:
            self.pixmap.loadFromData(app.icon)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(HOVER)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 6, 6)
        pixmap = self.pixmap if not self.pixmap.isNull() else app_pixmap(self.ctx, "app", self.app.exe, 20)
        draw_pixmap(p, pixmap, QRectF(12, 12, 20, 20))

    def enterEvent(self, _event: object) -> None:
        self.update()

    def leaveEvent(self, _event: object) -> None:
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._open(self.app)


class TrayFlyout(Flyout):
    def __init__(self, ctx: object, shell: object) -> None:
        super().__init__(ctx, shell, 228)
        self.setWindowTitle("Ventude: ícones ocultos")
        self.grid = QGridLayout()
        self.grid.setSpacing(2)
        self.note = QLabel("Procurando…")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        peek = HoverButton("monitor", "Bandeja do Windows", "", QSize(10, 34), 15, 6)
        peek.setMinimumWidth(10)
        peek.setMaximumWidth(16777215)
        peek.setToolTip("Mostra a barra do Windows por alguns segundos (menus dos ícones da bandeja)")
        peek.clicked.connect(lambda: (self.close_animated(), shell.peek_windows_taskbar()))  # type: ignore[attr-defined]
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(8)
        root.addLayout(self.grid)
        root.addWidget(self.note)
        root.addWidget(peek)
        self._icons: list[QWidget] = []

    def set_apps(self, tray: list[apps.TrayApp]) -> None:
        for widget in self._icons:
            self.grid.removeWidget(widget)
            widget.deleteLater()
        self._icons = []
        for index, app in enumerate(tray[:24]):
            icon = TrayIcon(app, self.ctx, self._launch)
            self.grid.addWidget(icon, index // 4, index % 4)
            self._icons.append(icon)
        self.note.setText("Clique para abrir o app." if tray else "Nenhum app na bandeja agora.")
        self.adjustSize()

    def _launch(self, app: apps.TrayApp) -> None:
        self.close_animated()
        self.shell.launch("app", app.exe, app.name, record=False)  # type: ignore[attr-defined]


# ---------- energia ----------
class PowerFlyout(Flyout):
    """Menu de Energia do Iniciar. Cada ação passa pela animação ("Até logo!") antes."""

    ORDER = (
        "lock",
        "logoff",
        "sleep",
        "hibernate",
        "update_restart",
        "update_shutdown",
        "restart",
        "shutdown",
    )

    def __init__(self, ctx: object, shell: object) -> None:
        super().__init__(ctx, shell, 236)
        self.setWindowTitle("Ventude: energia")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(8, 8, 8, 8)
        self.box.setSpacing(2)
        self._buttons: list[QWidget] = []

    def refresh(self) -> None:
        for button in self._buttons:
            self.box.removeWidget(button)
            button.deleteLater()
        self._buttons = []
        available = {a.key: a for a in self.ctx.power.available()}  # type: ignore[attr-defined]
        for key in self.ORDER:
            action = available.get(key)
            if action is None:
                continue
            if key == "restart" and self._buttons:
                line = QWidget()
                line.setFixedHeight(9)
                line.setStyleSheet("border-top: 1px solid rgba(255,255,255,0.08); margin-top: 4px;")
                self.box.addWidget(line)
                self._buttons.append(line)
            button = HoverButton(action.icon, action.title, "", QSize(10, 38), 17, 6)
            button.setMinimumWidth(10)
            button.setMaximumWidth(16777215)
            if action.needs_update:
                button.icon_color = theme.WARN
            button.clicked.connect(lambda k=key: self._choose(k))
            self.box.addWidget(button)
            self._buttons.append(button)
        self.adjustSize()

    def _choose(self, key: str) -> None:
        self.close_animated()
        self.shell.power(key)  # type: ignore[attr-defined]


def power_keys() -> tuple[str, ...]:
    return tuple(a.key for a in power.ACTIONS)
