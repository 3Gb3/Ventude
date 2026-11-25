"""Áudio: saída e entrada com 1 clique, volume master e do microfone e mixer por aplicativo."""

from __future__ import annotations

from PySide6.QtCore import QFileInfo, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFileIconProvider,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ventude.services.audio import KIND_ICONS, AppSession, AudioState, Device, short_device_name
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.pages.base import Page, scroller
from ventude.ui.widgets import Card, ElidedLabel, IconButton, hbox, label, vbox

REFRESH_MS = 2000
DEVICE_STYLE = (
    "QPushButton#Device { text-align: left; padding: 7px 10px; background: transparent;"
    f" border: 1px solid transparent; border-radius: 10px; color: {theme.TEXT_2}; }}"
    f"QPushButton#Device:hover {{ background: {theme.SURFACE_2}; }}"
    f"QPushButton#Device:checked {{ background: {theme.ACCENT_SOFT}; border-color: {theme.ACCENT_LINE};"
    f" color: {theme.ACCENT}; }}"
)


def percent(level: float | None) -> str:
    return "—" if level is None else f"{round(level * 100)}%"


class VolumeRow(QWidget):
    """Botão de mudo + slider 0–100 + porcentagem. Não briga com o arraste do usuário."""

    level_changed = Signal(float)
    mute_toggled = Signal(bool)

    def __init__(self, icon_on: str, icon_off: str, tooltip: str) -> None:
        super().__init__()
        self._icons = (icon_on, icon_off)
        self.muted = False
        self.mute = IconButton(icon_on, tooltip, 30, 16)
        self.mute.clicked.connect(lambda: self.mute_toggled.emit(not self.muted))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.valueChanged.connect(self._moved)
        self.value = label("—", "Muted")
        self.value.setFixedWidth(40)
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setLayout(hbox(self.mute, self.slider, self.value, spacing=8))

    def _moved(self, value: int) -> None:
        self.value.setText(f"{value}%")
        self.level_changed.emit(value / 100)

    def set_state(self, level: float | None, muted: bool) -> None:
        self.setEnabled(level is not None)
        self.muted = muted
        icon = self._icons[1] if muted or not level else self._icons[0]
        self.mute.set_icon(icon, theme.DANGER if muted else theme.TEXT_2)
        if level is None or self.slider.isSliderDown():
            self.value.setText(percent(level))
            return
        self.slider.blockSignals(True)
        self.slider.setValue(round(level * 100))
        self.slider.blockSignals(False)
        self.value.setText("Mudo" if muted else percent(level))


class DeviceList(QWidget):
    """Um botão por dispositivo; o padrão fica marcado. Clique troca o padrão do Windows."""

    chosen = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(DEVICE_STYLE)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(2)
        self.buttons: dict[str, QPushButton] = {}
        self._layout_key: tuple = ()

    def set_devices(self, devices: tuple[Device, ...]) -> None:
        key = tuple((d.id, d.name) for d in devices)
        if key != self._layout_key:
            self._layout_key = key
            while self.box.count():
                item = self.box.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.buttons.clear()
            for device in devices:
                name = short_device_name(device.name)
                button = QPushButton(f"  {name[:34] + '…' if len(name) > 35 else name}")
                button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                button.setObjectName("Device")
                button.setCheckable(True)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setToolTip(device.name)
                button.clicked.connect(lambda _c=False, d=device.id: self.chosen.emit(d))
                self.box.addWidget(button)
                self.buttons[device.id] = button
            if not devices:
                self.box.addWidget(label("Nenhum dispositivo ativo", "Faint"))
        for device in devices:
            button = self.buttons[device.id]
            button.setChecked(device.default)
            color = theme.ACCENT if device.default else theme.TEXT_2
            button.setIcon(icons.icon(KIND_ICONS.get(device.kind, "volume"), color, 16))
            button.setIconSize(QSize(16, 16))


class SessionRow(QWidget):
    """Um app no mixer: ícone real do .exe, nome, volume e mudo."""

    def __init__(self, session: AppSession, icon: QIcon) -> None:
        super().__init__()
        self.key = session.key
        glyph = QLabel()
        glyph.setFixedSize(24, 24)
        glyph.setPixmap(icon.pixmap(QSize(22, 22)))
        self.name = ElidedLabel(session.name, "")
        self.name.setStyleSheet("font-size: 12px; font-weight: 600;")
        self.name.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.name.setFixedWidth(120)
        self.volume = VolumeRow("volume", "volume-x", f"Mudo: {session.name}")
        self.setLayout(hbox(glyph, self.name, self.volume, spacing=8))
        self.update_session(session)

    def update_session(self, session: AppSession) -> None:
        self.name.setText(session.name)
        self.volume.set_state(session.level, session.muted)
        self.setToolTip(session.path or session.name)


class AudioPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        self.audio = ctx.audio
        row, self.subtitle = self.header("Áudio")
        refresh = IconButton("refresh", "Atualizar agora", 30, 15)
        refresh.clicked.connect(self.audio.refresh)
        row.addWidget(refresh)
        self._file_icons = QFileIconProvider()
        self._icon_cache: dict[str, QIcon] = {}

        out_card = Card(margins=(14, 12, 14, 12), spacing=8)
        out_card.body.addWidget(label("SAÍDA", "Caption"))
        self.master = VolumeRow("volume", "volume-x", "Mudo (todo o som)")
        self.master.level_changed.connect(self.audio.set_master)
        self.master.mute_toggled.connect(self.audio.set_master_mute)
        out_card.body.addWidget(self.master)
        self.outputs = DeviceList()
        self.outputs.chosen.connect(self.audio.set_default)
        out_card.body.addWidget(scroller(self.outputs), 1)

        in_card = Card(margins=(14, 12, 14, 12), spacing=8)
        in_card.body.addWidget(label("ENTRADA", "Caption"))
        self.mic = VolumeRow("mic", "mic-off", "Mutar o microfone")
        self.mic.level_changed.connect(self.audio.set_mic)
        self.mic.mute_toggled.connect(self.audio.set_mic_mute)
        in_card.body.addWidget(self.mic)
        self.inputs = DeviceList()
        self.inputs.chosen.connect(self.audio.set_default)
        in_card.body.addWidget(scroller(self.inputs), 1)

        mixer = Card(margins=(14, 12, 10, 12), spacing=8)
        mixer.body.addWidget(label("MIXER POR APLICATIVO", "Caption"))
        self.sessions_box = QWidget()
        self.sessions = QVBoxLayout(self.sessions_box)
        self.sessions.setContentsMargins(0, 0, 6, 0)
        self.sessions.setSpacing(6)
        self.sessions.addStretch(1)
        mixer.body.addWidget(scroller(self.sessions_box), 1)
        self.empty = label("Nenhum app tocando som agora.", "Faint")
        mixer.body.addWidget(self.empty)
        self.rows: dict[str, SessionRow] = {}

        left = vbox(out_card, in_card, spacing=12)
        body = hbox(spacing=12)
        body.addLayout(left, 5)
        body.addWidget(mixer, 6)
        self.root.addLayout(body, 1)
        self.unavailable = label(
            "O controle de áudio usa o Core Audio do Windows e não está disponível agora.", "Muted", wrap=True
        )
        self.root.addWidget(self.unavailable)
        self.timer = QTimer(self, interval=REFRESH_MS)
        self.timer.timeout.connect(self.audio.refresh)
        self.audio.changed.connect(self.set_state)
        self.set_state(self.audio.state)

    def _icon(self, session: AppSession) -> QIcon:
        if session.system or not session.path:
            return icons.icon("volume", theme.TEXT_2, 22)
        if session.path not in self._icon_cache:
            icon = self._file_icons.icon(QFileInfo(session.path))
            self._icon_cache[session.path] = (
                icon if not icon.isNull() else icons.icon("window", theme.TEXT_2, 22)
            )
        return self._icon_cache[session.path]

    def set_state(self, state: AudioState) -> None:
        running = self.audio.running
        self.unavailable.setVisible(not running and not state.outputs)
        out, mic = state.default_output, state.default_input
        self.subtitle.setText(
            f"Saída: {short_device_name(out.name)}" if out else "Clique num dispositivo para virar o padrão"
        )
        self.master.set_state(state.master, state.master_muted)
        self.mic.set_state(state.mic, state.mic_muted)
        self.mic.mute.setToolTip("Ativar o microfone" if state.mic_muted else "Mutar o microfone")
        self.outputs.set_devices(state.outputs)
        self.inputs.set_devices(state.inputs)
        self._fill_sessions(state.sessions)
        if mic is None:
            self.mic.setToolTip("Nenhum microfone ativo")

    def _fill_sessions(self, sessions: tuple[AppSession, ...]) -> None:
        keys = [s.key for s in sessions]
        if keys != list(self.rows):
            for row in self.rows.values():
                self.sessions.removeWidget(row)
                row.deleteLater()
            self.rows.clear()
            for session in sessions:
                row = SessionRow(session, self._icon(session))
                row.volume.level_changed.connect(lambda v, k=session.key: self.audio.set_session(k, v))
                row.volume.mute_toggled.connect(lambda m, k=session.key: self.audio.set_session_mute(k, m))
                self.sessions.insertWidget(self.sessions.count() - 1, row)
                self.rows[session.key] = row
        else:
            for session in sessions:
                self.rows[session.key].update_session(session)
        self.empty.setVisible(not sessions)

    def showEvent(self, event: object) -> None:
        super().showEvent(event)  # type: ignore[arg-type]
        if self.audio.running:
            self.audio.refresh()
            self.timer.start()

    def hideEvent(self, event: object) -> None:
        super().hideEvent(event)  # type: ignore[arg-type]
        self.timer.stop()

    def on_show(self) -> None:
        self.set_state(self.audio.state)
