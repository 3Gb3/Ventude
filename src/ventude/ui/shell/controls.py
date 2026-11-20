"""Corpo do modo Controles da ilha: volume, saída, microfone, teclas-cápsula, ferramentas e bandeja."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ventude.core.timefmt import clock
from ventude.services import apps
from ventude.ui import brand, icons, theme
from ventude.ui.shell.common import HoverButton, app_pixmap, draw_pixmap

CONTROLS_WIDTH = 440
CONTROLS_BASE_HEIGHT = 356


def volume_icon(level: float | None, muted: bool) -> str:
    if muted or level is None or level <= 0.001:
        return "volume-x"
    return "volume-1" if level < 0.5 else "volume"


class QuickTile(QWidget):
    """Tecla-cápsula 96×40 de Controles: ligado usa fundo ACCENT_SOFT + ícone/texto menta."""

    clicked = Signal()

    def __init__(self, icon: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon_name = icon
        self.title = title
        self.detail = ""
        self.on = False
        self._pressed = False
        self.setFixedSize(96, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setAccessibleName(title)

    def set_state(
        self, on: bool, detail: str = "", icon: str | None = None, title: str | None = None
    ) -> None:
        self.on = bool(on)
        self.detail = detail
        if icon:
            self.icon_name = icon
        if title:
            self.title = title
            self.setAccessibleName(title)
        tip = f"{self.title}: {detail}" if detail else self.title
        self.setToolTip(tip)
        self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._pressed:
            cx, cy = rect.center().x(), rect.center().y()
            p.translate(cx, cy)
            p.scale(0.95, 0.95)
            p.translate(-cx, -cy)
        radius = rect.height() / 2.0
        bg = theme.color(theme.TOGGLE_ON_BG if self.on else theme.INK_KEY)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, radius, radius)
        if self._pressed:
            p.setBrush(theme.HALO_PRESS)
            p.drawRoundedRect(rect, radius, radius)
        elif self.underMouse():
            p.setBrush(theme.HALO)
            p.drawRoundedRect(rect, radius, radius)
        fg = theme.TOGGLE_ON_FG if self.on else theme.TEXT_2
        draw_pixmap(p, icons.pixmap(self.icon_name, fg, 15), QRectF(12, (self.height() - 15) / 2, 15, 15))
        p.setPen(QColor(fg if self.on else theme.TEXT))
        p.setFont(theme.font(11, theme.SEMI if self.on else QFont.Weight.Normal))
        text = p.fontMetrics().elidedText(self.title, Qt.TextElideMode.ElideRight, int(rect.width() - 36))
        p.drawText(QRectF(31, 0, rect.width() - 38, rect.height()), Qt.AlignmentFlag.AlignVCenter, text)

    def enterEvent(self, _event: object) -> None:
        self.update()

    def leaveEvent(self, _event: object) -> None:
        self._pressed = False
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        was = self._pressed
        self._pressed = False
        self.update()
        if (
            was
            and event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()


class TrayIcon(QWidget):
    """Ícone de app rodando em segundo plano (linha 'em segundo plano' de Controles)."""

    def __init__(self, app: apps.TrayApp, ctx: object, open_: Callable[[apps.TrayApp], None]) -> None:
        super().__init__()
        self.app, self.ctx, self._open = app, ctx, open_
        self.setFixedSize(32, 32)
        self.setToolTip(app.name)
        self.setAccessibleName(app.name)
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
            p.setBrush(theme.HALO)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), theme.R_ITEM, theme.R_ITEM)
        pixmap = self.pixmap if not self.pixmap.isNull() else app_pixmap(self.ctx, "app", self.app.exe, 18)
        draw_pixmap(p, pixmap, QRectF(7, 7, 18, 18))

    def enterEvent(self, _event: object) -> None:
        self.update()

    def leaveEvent(self, _event: object) -> None:
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self._open(self.app)


def _capsule_btn(icon_name: str, text: str, tip: str = "") -> QPushButton:
    btn = QPushButton(f" {text}")
    btn.setIcon(icons.icon(icon_name, theme.TEXT_2, 14))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFixedHeight(30)
    btn.setToolTip(tip or text)
    btn.setAccessibleName(text)
    btn.setStyleSheet(
        f"QPushButton {{ background: {theme.INK_KEY}; border: none; border-radius: 15px; "
        f"padding: 0 12px; color: {theme.TEXT}; font-size: 12px; }}"
        "QPushButton:hover { background: rgba(255,255,255,0.10); }"
        "QPushButton:pressed { background: rgba(255,255,255,0.14); }"
    )
    return btn


def _link_btn(text: str, tip: str = "") -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setToolTip(tip or text)
    btn.setAccessibleName(text)
    btn.setStyleSheet(
        f"QPushButton {{ background: transparent; border: none; padding: 2px 4px; "
        f"color: {theme.TEXT_2}; font-size: 11px; }}"
        f"QPushButton:hover {{ color: {theme.TEXT}; }}"
    )
    return btn


class ControlsBody(QWidget):
    """Estado Controles da ilha (440 px): volume, saída, mic, teclas-cápsula, ferramentas e bandeja."""

    close_requested = Signal()
    size_changed = Signal()
    open_audio = Signal()
    caffeine_clicked = Signal()
    mic_clicked = Signal()

    def __init__(self, ctx: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._updating = False
        self.network_kind = ""
        self.wifi = ""
        self._tray_apps: list[apps.TrayApp] = []
        self._tray_widgets: list[QWidget] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 16, 14)
        root.setSpacing(10)

        # Cabeçalho: 'controles' + hora tabular + fechar
        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("controles")
        title.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 12px;")
        self.clock = QLabel("")
        self.clock.setFont(theme.font(13, theme.SEMI, display=True, tabular=True))
        self.close_btn = HoverButton("chevron-up", "", "Fechar (Esc)", QSize(28, 28), 15, 14)
        self.close_btn.clicked.connect(self.close_requested.emit)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.clock)
        header.addWidget(self.close_btn)
        root.addLayout(header)

        slider_qss = (
            "QSlider::groove:horizontal { height: 5px; background: rgba(255,255,255,0.14);"
            " border-radius: 2px; }"
            f"QSlider::sub-page:horizontal {{ background: {theme.ACCENT}; border-radius: 2px; }}"
            "QSlider::handle:horizontal { width: 14px; height: 14px; margin: -5px 0;"
            f" background: {theme.ACCENT}; border: 2px solid {theme.INK}; border-radius: 7px; }}"
        )

        # Volume + chip de saída
        self.mute = HoverButton("volume", "", "Mudo", QSize(30, 30), 16, 15)
        self.mute.clicked.connect(self._toggle_mute)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setAccessibleName("Volume")
        self.slider.setStyleSheet(slider_qss)
        self.slider.valueChanged.connect(self._volume_moved)
        self.level = QLabel("—")
        self.level.setFixedWidth(34)
        self.level.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.level.setFont(theme.font(12, theme.SEMI, tabular=True))
        self.level.setStyleSheet(f"color: {theme.TEXT_2};")
        self.output_chip = QPushButton("Saída ▾")
        self.output_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.output_chip.setFixedHeight(28)
        self.output_chip.setMaximumWidth(134)
        self.output_chip.setAccessibleName("Escolher saída de áudio")
        self.output_chip.setStyleSheet(
            f"QPushButton {{ background: {theme.INK_KEY}; border: none; border-radius: 14px; "
            f"padding: 0 10px; color: {theme.TEXT}; font-size: 11px; }}"
            "QPushButton:hover { background: rgba(255,255,255,0.10); }"
        )
        self.output_chip.clicked.connect(self._toggle_devices)
        vol_row = QHBoxLayout()
        vol_row.setSpacing(6)
        vol_row.addWidget(self.mute)
        vol_row.addWidget(self.slider, 1)
        vol_row.addWidget(self.level)
        vol_row.addWidget(self.output_chip)
        root.addLayout(vol_row)

        # Lista de saídas (abre inline quando clicado)
        self.devices = QVBoxLayout()
        self.devices.setContentsMargins(28, 0, 4, 0)
        self.devices.setSpacing(2)
        self.devices_box = QWidget()
        self.devices_box.setLayout(self.devices)
        self.devices_box.hide()
        root.addWidget(self.devices_box)

        # Microfone
        self.mic_mute = HoverButton("mic", "", "Mutar microfone", QSize(30, 30), 16, 15)
        self.mic_mute.clicked.connect(self._toggle_mic)
        self.mic_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_slider.setRange(0, 100)
        self.mic_slider.setAccessibleName("Ganho do microfone")
        self.mic_slider.setStyleSheet(slider_qss)
        self.mic_slider.valueChanged.connect(self._mic_moved)
        self.mic_level = QLabel("—")
        self.mic_level.setFixedWidth(44)
        self.mic_level.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.mic_level.setFont(theme.font(12, theme.SEMI, tabular=True))
        self.mic_level.setStyleSheet(f"color: {theme.TEXT_2};")
        mic_row = QHBoxLayout()
        mic_row.setSpacing(6)
        mic_row.addWidget(self.mic_mute)
        mic_row.addWidget(self.mic_slider, 1)
        mic_row.addWidget(self.mic_level)
        root.addLayout(mic_row)

        # Grade 4x2 de teclas-cápsula 96x40
        self.network = QuickTile("ethernet", "rede")
        self.network.clicked.connect(lambda: self._open("ms-settings:network-status"))
        self.bluetooth = QuickTile("bluetooth", "bluetooth")
        self.bluetooth.set_state(False, "Configurar no Windows")
        self.bluetooth.clicked.connect(lambda: self._open("ms-settings:bluetooth"))
        self.caffeine = QuickTile("coffee", "cafeína")
        self.caffeine.clicked.connect(self._toggle_caffeine)
        self.mic = QuickTile("mic", "microfone")
        self.mic.clicked.connect(self._toggle_mic)
        self.focus = QuickTile("focus", "foco 25")
        self.focus.clicked.connect(self._toggle_focus)
        self.night = QuickTile("night", "luz noturna")
        self.night.set_state(False, "Configurar no Windows")
        self.night.clicked.connect(lambda: self._open("ms-settings:nightlight"))
        self.project = QuickTile("monitor", "projetar")
        self.project.set_state(False, "Outra tela (Win + P)")
        self.project.clicked.connect(self._project)
        self.quiet = QuickTile("bell-off", "silêncio")
        self.quiet.clicked.connect(self._toggle_quiet)

        grid = QGridLayout()
        grid.setSpacing(8)
        for index, tile in enumerate(
            (
                self.network,
                self.bluetooth,
                self.caffeine,
                self.mic,
                self.focus,
                self.night,
                self.project,
                self.quiet,
            )
        ):
            grid.addWidget(tile, index // 4, index % 4)
        root.addLayout(grid)

        # Ferramentas
        tools_row = QHBoxLayout()
        tools_row.setSpacing(8)
        tools_lbl = QLabel("ferramentas")
        tools_lbl.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px;")
        self.color_btn = _capsule_btn("pipette", "conta-gotas", "Copiar a cor de qualquer ponto da tela")
        self.color_btn.clicked.connect(lambda: self._tool("color"))
        self.ocr_btn = _capsule_btn("scan", "texto da tela", "Capturar texto da tela (OCR)")
        self.ocr_btn.clicked.connect(lambda: self._tool("ocr"))
        tools_row.addWidget(tools_lbl)
        tools_row.addStretch(1)
        tools_row.addWidget(self.color_btn)
        tools_row.addWidget(self.ocr_btn)
        root.addLayout(tools_row)

        # Em segundo plano (apps da bandeja)
        tray_row = QHBoxLayout()
        tray_row.setSpacing(6)
        tray_lbl = QLabel("em segundo plano")
        tray_lbl.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px;")
        self.tray_icons = QHBoxLayout()
        self.tray_icons.setSpacing(4)
        self.tray_empty = QLabel("—")
        self.tray_empty.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        self.tray_peek = _link_btn("bandeja do Windows ›", "Mostrar a barra original por 12 s")
        self.tray_peek.clicked.connect(self._peek_windows_tray)
        tray_row.addWidget(tray_lbl)
        tray_row.addLayout(self.tray_icons)
        tray_row.addWidget(self.tray_empty)
        tray_row.addStretch(1)
        tray_row.addWidget(self.tray_peek)
        root.addLayout(tray_row)

        # Rodapé de links
        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.audio_link = _link_btn("mais em Áudio ›")
        self.audio_link.clicked.connect(self.open_audio.emit)
        self.notif_link = _link_btn("notificações ›")
        self.notif_link.clicked.connect(self._open_notifications)
        self.win_link = _link_btn("Configurações do Windows ›")
        self.win_link.clicked.connect(lambda: self._open("ms-settings:"))
        foot.addWidget(self.audio_link)
        foot.addStretch(1)
        foot.addWidget(self.notif_link)
        foot.addWidget(self.win_link)
        root.addLayout(foot)

        ctx.audio.changed.connect(self._audio)  # type: ignore[attr-defined]
        ctx.caffeine.changed.connect(lambda _on: self.refresh())  # type: ignore[attr-defined]
        ctx.timer.changed.connect(lambda _s: self.refresh())  # type: ignore[attr-defined]
        ctx.settings_changed.connect(lambda _s: self.refresh())  # type: ignore[attr-defined]
        self.refresh_clock()
        self.refresh()

    def preferred_height(self) -> int:
        extra = 0
        if not self.devices_box.isHidden():
            extra = min(160, max(44, self.devices_box.sizeHint().height() + 8))
        return CONTROLS_BASE_HEIGHT + extra

    def refresh_clock(self) -> None:
        self.clock.setText(clock(datetime.now(), self.ctx.settings.clock_24h))  # type: ignore[attr-defined]

    def set_network(self, kind: str, wifi: str = "") -> None:
        self.network_kind, self.wifi = kind, wifi
        self.refresh()

    def refresh(self) -> None:
        ctx = self.ctx
        kind = self.network_kind
        if kind == "wifi":
            self.network.set_state(True, self.wifi or "Conectado", "wifi", "wi-fi")
        elif kind == "ethernet":
            self.network.set_state(True, "Conectado", "ethernet", "rede")
        else:
            self.network.set_state(False, "Sem conexão", "wifi-off", "rede")
        caffeine = ctx.caffeine  # type: ignore[attr-defined]
        self.caffeine.set_state(caffeine.active, "Tela acesa" if caffeine.active else "Desligada")
        timer = ctx.timer.state  # type: ignore[attr-defined]
        self.focus.set_state(timer is not None, "Em andamento" if timer is not None else "25 min")
        quiet = bool(getattr(ctx.settings, "quiet_mode", False))  # type: ignore[attr-defined]
        self.quiet.set_state(
            quiet,
            "Só timer, agenda e limite ≥ 95%" if quiet else "Todos os avisos ligados",
            "bell-off" if quiet else "bell",
        )
        self._audio(ctx.audio.state)  # type: ignore[attr-defined]

    def set_tray_apps(self, tray: list[apps.TrayApp]) -> None:
        self._tray_apps = list(tray)
        for widget in self._tray_widgets:
            self.tray_icons.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self._tray_widgets = []
        self.tray_empty.setVisible(not tray)
        for app in tray[:5]:
            icon = TrayIcon(app, self.ctx, self._launch_tray_app)
            self.tray_icons.addWidget(icon)
            self._tray_widgets.append(icon)
        extra = len(tray) - 5
        if extra > 0:
            more = QPushButton(f"+{extra}")
            more.setFixedSize(32, 26)
            more.setCursor(Qt.CursorShape.PointingHandCursor)
            more.setAccessibleName(f"Mais {extra} apps em segundo plano")
            more.setStyleSheet(
                f"QPushButton {{ background: {theme.INK_KEY}; border: none; border-radius: 13px; "
                f"padding: 0px; color: {theme.TEXT_2}; font-size: 11px; }}"
                "QPushButton:hover { background: rgba(255,255,255,0.10); color: #ECEEF2; }"
            )
            more.clicked.connect(lambda: self._show_more_tray(more))
            self.tray_icons.addWidget(more)
            self._tray_widgets.append(more)

    def _show_more_tray(self, anchor: QWidget) -> None:
        menu = brand.ink_menu(self)
        for app in self._tray_apps[5:24]:
            pm = QPixmap()
            if app.icon:
                pm.loadFromData(app.icon)
            ico = QIcon(pm) if not pm.isNull() else icons.icon("apps", theme.TEXT_2, 14)
            action = menu.addAction(ico, app.name)
            action.triggered.connect(lambda _c=False, a=app: self._launch_tray_app(a))
        menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height() + 4)))

    def _launch_tray_app(self, app: apps.TrayApp) -> None:
        self.close_requested.emit()
        shell = getattr(self.ctx, "shell", None)
        if shell is not None and hasattr(shell, "launch"):
            shell.launch("app", app.exe, app.name, record=False)  # type: ignore[attr-defined]
        else:
            from ventude.services import launcher

            launcher.open_target("app", app.exe)

    def _audio(self, state: object) -> None:
        master = getattr(state, "master", None)
        muted = bool(getattr(state, "master_muted", False))
        self._updating = True
        self.slider.setEnabled(master is not None)
        self.slider.setValue(round((master or 0) * 100))
        self.level.setText(f"{round((master or 0) * 100)}%" if master is not None else "—")
        self.mute.set_icon(volume_icon(master, muted), theme.DANGER if muted else theme.TEXT)

        mic = getattr(state, "mic", None)
        mic_muted = bool(getattr(state, "mic_muted", False))
        has_mic = mic is not None
        self.mic_slider.setEnabled(has_mic)
        self.mic_slider.setValue(round((mic or 0) * 100))
        self._updating = False
        mic_text = (
            "mudo"
            if (has_mic and mic_muted)
            else (f"{round((mic or 0) * 100)}%" if has_mic else "—")
        )
        self.mic_level.setText(mic_text)
        self.mic_mute.set_icon("mic-off" if mic_muted else "mic", theme.DANGER if mic_muted else theme.TEXT)
        self.mic.set_state(
            has_mic and not mic_muted,
            "Mudo" if mic_muted else ("Ligado" if has_mic else "Nenhum"),
            "mic-off" if mic_muted else "mic",
        )
        default_out = getattr(state, "default_output", None)
        out_name = getattr(default_out, "name", "") if default_out else ""
        short = out_name.split(" (")[0].strip() if out_name else "Saída"
        if len(short) > 14:
            short = short[:13] + "…"
        self.output_chip.setText(f"{short} {'▴' if not self.devices_box.isHidden() else '▾'}")
        self._fill_devices(getattr(state, "outputs", ()))

    def _fill_devices(self, outputs: tuple) -> None:
        while self.devices.count():
            item = self.devices.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        for device in outputs:
            button = HoverButton(
                "check" if device.default else "speaker", device.name, "", QSize(10, 32), 14, 10
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

    def _mic_moved(self, value: int) -> None:
        if self._updating:
            return
        self.ctx.audio.set_mic(value / 100)  # type: ignore[attr-defined]
        self.mic_level.setText(f"{value}%")

    def _toggle_mute(self) -> None:
        state = self.ctx.audio.state  # type: ignore[attr-defined]
        self.ctx.audio.set_master_mute(not state.master_muted)  # type: ignore[attr-defined]

    def _toggle_mic(self) -> None:
        self.mic_clicked.emit()
        self.ctx.audio.toggle_mic_mute()  # type: ignore[attr-defined]

    def _toggle_caffeine(self) -> None:
        self.caffeine_clicked.emit()
        self.ctx.caffeine.toggle()  # type: ignore[attr-defined]

    def _toggle_quiet(self) -> None:
        cur = bool(getattr(self.ctx.settings, "quiet_mode", False))  # type: ignore[attr-defined]
        self.ctx.update_settings(quiet_mode=not cur)  # type: ignore[attr-defined]

    def _toggle_devices(self) -> None:
        show = self.devices_box.isHidden()
        self.devices_box.setVisible(show)
        self._audio(self.ctx.audio.state)  # type: ignore[attr-defined]
        self.size_changed.emit()

    def _toggle_focus(self) -> None:
        timer = self.ctx.timer  # type: ignore[attr-defined]
        if timer.state is not None:
            timer.stop()
        else:
            timer.start_pomodoro()
        self.refresh()

    def _tool(self, name: str) -> None:
        self.close_requested.emit()
        QTimer.singleShot(200, lambda: self.ctx.tool_requested.emit(name))  # type: ignore[attr-defined]

    def _open(self, target: str) -> None:
        self.close_requested.emit()
        apps.open_system(target)

    def _open_notifications(self) -> None:
        self.close_requested.emit()
        ok, _err = apps.open_system("ms-actioncenter:")
        if not ok:
            apps.send_keys(0x5B, 0x4E)  # Win + N

    def _project(self) -> None:
        self.close_requested.emit()
        apps.send_keys(0x5B, 0x50)  # Win + P

    def _peek_windows_tray(self) -> None:
        self.close_requested.emit()
        shell = getattr(self.ctx, "shell", None)
        if shell is not None and hasattr(shell, "peek_windows_taskbar"):
            shell.peek_windows_taskbar()  # type: ignore[attr-defined]
