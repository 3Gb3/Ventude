"""Corpo do modo Controles da ilha: Som (saída, volume, microfone), teclas rápidas, ferramentas e apps
em segundo plano."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QFont, QIcon, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ventude.core.timefmt import clock
from ventude.services import apps
from ventude.ui import brand, icons, theme
from ventude.ui.shell.common import HoverButton, app_pixmap, draw_pixmap

CONTROLS_WIDTH = 480
CONTROLS_MIN_HEIGHT = 300
TRAY_SHOWN = 8  # ícones de apps em segundo plano antes do "+N"
CARD_QSS = (
    "QFrame#CtlCard { background: rgba(255,255,255,0.035); border: 1px solid rgba(255,255,255,0.07);"
    " border-radius: 16px; }"
)


def volume_icon(level: float | None, muted: bool) -> str:
    if muted or level is None or level <= 0.001:
        return "volume-x"
    return "volume-1" if level < 0.5 else "volume"


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px; font-weight: 600; letter-spacing: 0.3px;")
    return lbl


def _card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("CtlCard")
    frame.setStyleSheet(CARD_QSS)
    box = QVBoxLayout(frame)
    box.setContentsMargins(12, 10, 12, 10)
    box.setSpacing(6)
    return frame, box


class QuickTile(QWidget):
    """Tecla de Controles (104×54): ícone, nome e o estado embaixo; ligada fica com fundo menta."""

    clicked = Signal()

    def __init__(self, icon: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon_name = icon
        self.title = title
        self.detail = ""
        self.on = False
        self._pressed = False
        self.setFixedSize(104, 54)
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
            p.scale(0.96, 0.96)
            p.translate(-cx, -cy)
        radius = 14.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color(theme.TOGGLE_ON_BG if self.on else theme.INK_KEY))
        p.drawRoundedRect(rect, radius, radius)
        if self._pressed:
            p.setBrush(theme.HALO_PRESS)
            p.drawRoundedRect(rect, radius, radius)
        elif self.underMouse():
            p.setBrush(theme.HALO)
            p.drawRoundedRect(rect, radius, radius)
        fg = theme.TOGGLE_ON_FG if self.on else theme.TEXT_2
        draw_pixmap(p, icons.pixmap(self.icon_name, fg, 16), QRectF(11, 9, 16, 16))
        width = int(rect.width() - 20)
        p.setPen(QColor(fg if self.on else theme.TEXT))
        p.setFont(theme.font(12, theme.SEMI if self.on else QFont.Weight.Medium))
        title = p.fontMetrics().elidedText(self.title, Qt.TextElideMode.ElideRight, width)
        p.drawText(QRectF(11, 27, width, 14), Qt.AlignmentFlag.AlignVCenter, title)
        if self.detail:
            p.setPen(QColor(fg if self.on else theme.MUTED))
            p.setFont(theme.font(10))
            detail = p.fontMetrics().elidedText(self.detail, Qt.TextElideMode.ElideRight, width)
            p.drawText(QRectF(11, 40, width, 12), Qt.AlignmentFlag.AlignVCenter, detail)

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
    """Ícone de app rodando em segundo plano (seção 'Em segundo plano' de Controles)."""

    def __init__(
        self,
        app: apps.TrayApp,
        ctx: object,
        open_: Callable[[apps.TrayApp], None],
        menu_: Callable[[apps.TrayApp, QPoint], None] | None = None,
    ) -> None:
        super().__init__()
        self.app, self.ctx, self._open, self._menu = app, ctx, open_, menu_
        self.setFixedSize(40, 40)
        self.setToolTip(f"{app.name}\nClique para abrir · botão direito para opções")
        self.setAccessibleName(app.name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.pixmap = QPixmap()
        if app.icon:
            self.pixmap.loadFromData(app.icon)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.HALO if self.underMouse() else QColor(255, 255, 255, 6))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)
        pixmap = self.pixmap if not self.pixmap.isNull() else app_pixmap(self.ctx, "app", self.app.exe, 22)
        draw_pixmap(p, pixmap, QRectF(9, 9, 22, 22))

    def enterEvent(self, _event: object) -> None:
        self.update()

    def leaveEvent(self, _event: object) -> None:
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton and self._menu is not None:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if not self.rect().contains(event.position().toPoint()):
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._open(self.app)
        elif event.button() == Qt.MouseButton.RightButton and self._menu is not None:
            self._menu(self.app, event.globalPosition().toPoint())
            event.accept()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if self._menu is not None and event.reason() != QContextMenuEvent.Reason.Mouse:
            self._menu(self.app, event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)


class DeviceRow(QWidget):
    """Linha clicável com o nome inteiro do dispositivo de saída e a seta de abrir a lista."""

    clicked = Signal()

    def __init__(self, icon: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon_name = icon
        self.text = ""
        self.expanded = False
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setAccessibleName("Escolher saída de áudio")
        self.setToolTip("Escolher saída de áudio")

    def set_text(self, text: str, expanded: bool = False) -> None:
        self.text, self.expanded = text, expanded
        self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.HALO)
            p.drawRoundedRect(rect, 10, 10)
        draw_pixmap(p, icons.pixmap(self.icon_name, theme.TEXT_2, 15), QRectF(8, 7.5, 15, 15))
        chevron = "chevron-up" if self.expanded else "chevron-down"
        draw_pixmap(p, icons.pixmap(chevron, theme.TEXT_2, 14), QRectF(rect.right() - 22, 8, 14, 14))
        p.setPen(QColor(theme.TEXT))
        p.setFont(theme.font(12, QFont.Weight.Medium))
        width = int(rect.width() - 62)
        text = p.fontMetrics().elidedText(self.text, Qt.TextElideMode.ElideRight, width)
        p.drawText(QRectF(32, 0, width, rect.height()), Qt.AlignmentFlag.AlignVCenter, text)

    def enterEvent(self, _event: object) -> None:
        self.update()

    def leaveEvent(self, _event: object) -> None:
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()


def _capsule_btn(icon_name: str, text: str, tip: str = "") -> QPushButton:
    btn = QPushButton(f"  {text}")
    btn.setIcon(icons.icon(icon_name, theme.TEXT_2, 15))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFixedHeight(34)
    btn.setToolTip(tip or text)
    btn.setAccessibleName(text)
    btn.setStyleSheet(
        f"QPushButton {{ background: {theme.INK_KEY}; border: none; border-radius: 12px; "
        f"padding: 0 14px; color: {theme.TEXT}; font-size: 12px; text-align: left; }}"
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
        f"QPushButton {{ background: transparent; border: none; padding: 4px 6px; "
        f"color: {theme.TEXT_2}; font-size: 11px; border-radius: 8px; }}"
        f"QPushButton:hover {{ color: {theme.TEXT}; background: rgba(255,255,255,0.06); }}"
    )
    return btn


class ControlsBody(QWidget):
    """Estado Controles da ilha (480 px), em seções: Som, atalhos, Ferramentas e Em segundo plano.

    A altura vem do conteúdo (`preferred_height`), então nada fica espremido nem por cima de outra coisa.
    """

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
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)

        # Cabeçalho: 'Controles' + hora + fechar
        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Controles")
        title.setFont(theme.font(14, theme.SEMI, display=True))
        self.clock = QLabel("")
        self.clock.setFont(theme.font(13, theme.SEMI, display=True, tabular=True))
        self.clock.setStyleSheet(f"color: {theme.TEXT_2};")
        self.close_btn = HoverButton("chevron-up", "", "Fechar (Esc)", QSize(28, 28), 15, 14)
        self.close_btn.clicked.connect(self.close_requested.emit)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.clock)
        header.addWidget(self.close_btn)
        root.addLayout(header)

        slider_qss = (
            "QSlider { min-height: 22px; }"
            "QSlider::groove:horizontal { height: 6px; background: rgba(255,255,255,0.12);"
            " border-radius: 3px; }"
            f"QSlider::sub-page:horizontal {{ background: {theme.ACCENT}; border-radius: 3px; }}"
            "QSlider::handle:horizontal { width: 16px; height: 16px; margin: -6px 0;"
            f" background: {theme.TEXT}; border: 3px solid {theme.ACCENT}; border-radius: 8px; }}"
            "QSlider::sub-page:horizontal:disabled { background: rgba(255,255,255,0.18); }"
        )

        # ---------- Som ----------
        sound, sound_box = _card()
        sound_box.addWidget(_section_label("Som"))
        self.output_row = DeviceRow("speaker")
        self.output_row.clicked.connect(self._toggle_devices)
        sound_box.addWidget(self.output_row)

        self.devices = QVBoxLayout()
        self.devices.setContentsMargins(22, 0, 0, 2)
        self.devices.setSpacing(2)
        self.devices_box = QWidget()
        self.devices_box.setLayout(self.devices)
        self.devices_box.hide()
        sound_box.addWidget(self.devices_box)

        self.mute = HoverButton("volume", "", "Mudo", QSize(32, 32), 17, 16)
        self.mute.clicked.connect(self._toggle_mute)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setAccessibleName("Volume")
        self.slider.setStyleSheet(slider_qss)
        self.slider.valueChanged.connect(self._volume_moved)
        self.level = QLabel("—")
        self.level.setFixedWidth(44)
        self.level.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.level.setFont(theme.font(12, theme.SEMI, tabular=True))
        vol_row = QHBoxLayout()
        vol_row.setSpacing(8)
        vol_row.addWidget(self.mute)
        vol_row.addWidget(self.slider, 1)
        vol_row.addWidget(self.level)
        sound_box.addLayout(vol_row)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background: rgba(255,255,255,0.06); border: none;")
        sound_box.addWidget(divider)

        mic_head = QHBoxLayout()
        mic_head.setContentsMargins(8, 2, 0, 0)
        mic_head.setSpacing(9)
        mic_icon = QLabel()
        mic_icon.setPixmap(icons.pixmap("mic", theme.TEXT_2, 15))
        self.mic_name = QLabel("Microfone")
        self.mic_name.setStyleSheet(f"color: {theme.TEXT}; font-size: 12px; font-weight: 500;")
        self.mic_name.setMinimumWidth(10)
        mic_head.addWidget(mic_icon)
        mic_head.addWidget(self.mic_name, 1)
        sound_box.addLayout(mic_head)

        self.mic_mute = HoverButton("mic", "", "Mutar microfone", QSize(32, 32), 17, 16)
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
        mic_row = QHBoxLayout()
        mic_row.setSpacing(8)
        mic_row.addWidget(self.mic_mute)
        mic_row.addWidget(self.mic_slider, 1)
        mic_row.addWidget(self.mic_level)
        sound_box.addLayout(mic_row)
        root.addWidget(sound)

        # ---------- teclas rápidas (4 × 2) ----------
        self.network = QuickTile("ethernet", "Rede")
        self.network.clicked.connect(lambda: self._open("ms-settings:network-status"))
        self.bluetooth = QuickTile("bluetooth", "Bluetooth")
        self.bluetooth.set_state(False, "Configurar")
        self.bluetooth.clicked.connect(lambda: self._open("ms-settings:bluetooth"))
        self.caffeine = QuickTile("coffee", "Cafeína")
        self.caffeine.clicked.connect(self._toggle_caffeine)
        self.mic = QuickTile("mic", "Microfone")
        self.mic.clicked.connect(self._toggle_mic)
        self.focus = QuickTile("focus", "Foco 25")
        self.focus.clicked.connect(self._toggle_focus)
        self.night = QuickTile("night", "Luz noturna")
        self.night.set_state(False, "Configurar")
        self.night.clicked.connect(lambda: self._open("ms-settings:nightlight"))
        self.project = QuickTile("monitor", "Projetar")
        self.project.set_state(False, "Win + P")
        self.project.clicked.connect(self._project)
        self.quiet = QuickTile("bell-off", "Silêncio")
        self.quiet.clicked.connect(self._toggle_quiet)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
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

        # ---------- Ferramentas ----------
        tools_row = QHBoxLayout()
        tools_row.setSpacing(8)
        self.color_btn = _capsule_btn("pipette", "Conta-gotas", "Copiar a cor de qualquer ponto da tela")
        self.color_btn.clicked.connect(lambda: self._tool("color"))
        self.ocr_btn = _capsule_btn("scan", "Texto da tela", "Capturar texto da tela (OCR)")
        self.ocr_btn.clicked.connect(lambda: self._tool("ocr"))
        tools_row.addWidget(self.color_btn, 1)
        tools_row.addWidget(self.ocr_btn, 1)
        root.addLayout(tools_row)

        # ---------- Em segundo plano (apps da bandeja) ----------
        tray, tray_box = _card()
        tray_head = QHBoxLayout()
        tray_head.setSpacing(6)
        self.tray_title = _section_label("Em segundo plano")
        self.tray_peek = _link_btn("bandeja do Windows ›", "Mostrar a barra original por 12 s")
        self.tray_peek.clicked.connect(self._peek_windows_tray)
        tray_head.addWidget(self.tray_title)
        tray_head.addStretch(1)
        tray_head.addWidget(self.tray_peek)
        tray_box.addLayout(tray_head)
        tray_row = QHBoxLayout()
        tray_row.setSpacing(6)
        self.tray_icons = QHBoxLayout()
        self.tray_icons.setSpacing(6)
        self.tray_empty = QLabel("Nenhum app em segundo plano")
        self.tray_empty.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        tray_row.addLayout(self.tray_icons)
        tray_row.addWidget(self.tray_empty)
        tray_row.addStretch(1)
        tray_box.addLayout(tray_row)
        root.addWidget(tray)

        # ---------- rodapé ----------
        foot = QHBoxLayout()
        foot.setSpacing(4)
        self.audio_link = _link_btn("Mixer e dispositivos ›", "Abrir a aba Áudio da ilha")
        self.audio_link.clicked.connect(self.open_audio.emit)
        self.notif_link = _link_btn("Notificações ›")
        self.notif_link.clicked.connect(self._open_notifications)
        self.win_link = _link_btn("Configurações ›", "Configurações do Windows")
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
        """Altura que o conteúdo pede na largura da ilha (lista de saídas aberta ou não)."""
        layout = self.layout()
        layout.activate()
        height = layout.totalHeightForWidth(CONTROLS_WIDTH) if layout.hasHeightForWidth() else -1
        if height <= 0:
            height = layout.totalSizeHint().height()
        return max(CONTROLS_MIN_HEIGHT, height)

    def refresh_clock(self) -> None:
        self.clock.setText(clock(datetime.now(), self.ctx.settings.clock_24h))  # type: ignore[attr-defined]

    def set_network(self, kind: str, wifi: str = "") -> None:
        self.network_kind, self.wifi = kind, wifi
        self.refresh()

    def refresh(self) -> None:
        ctx = self.ctx
        kind = self.network_kind
        if kind == "wifi":
            self.network.set_state(True, self.wifi or "Conectado", "wifi", "Wi-Fi")
        elif kind == "ethernet":
            self.network.set_state(True, "Conectado", "ethernet", "Rede")
        else:
            self.network.set_state(False, "Sem conexão", "wifi-off", "Rede")
        caffeine = ctx.caffeine  # type: ignore[attr-defined]
        self.caffeine.set_state(caffeine.active, "Tela acesa" if caffeine.active else "Desligada")
        timer = ctx.timer.state  # type: ignore[attr-defined]
        self.focus.set_state(timer is not None, "Em andamento" if timer is not None else "25 min")
        quiet = bool(getattr(ctx.settings, "quiet_mode", False))  # type: ignore[attr-defined]
        self.quiet.set_state(
            quiet,
            "Só o essencial" if quiet else "Avisos ligados",
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
        self.tray_title.setText(f"Em segundo plano · {len(tray)}" if tray else "Em segundo plano")
        for app in tray[:TRAY_SHOWN]:
            icon = TrayIcon(app, self.ctx, self._launch_tray_app, self._tray_menu)
            self.tray_icons.addWidget(icon)
            self._tray_widgets.append(icon)
        extra = len(tray) - TRAY_SHOWN
        if extra > 0:
            more = QPushButton(f"+{extra}")
            more.setFixedSize(40, 32)
            more.setCursor(Qt.CursorShape.PointingHandCursor)
            more.setAccessibleName(f"Mais {extra} apps em segundo plano")
            more.setStyleSheet(
                f"QPushButton {{ background: {theme.INK_KEY}; border: none; border-radius: 16px; "
                f"padding: 0px; color: {theme.TEXT_2}; font-size: 11px; }}"
                "QPushButton:hover { background: rgba(255,255,255,0.10); color: #ECEEF2; }"
            )
            more.clicked.connect(lambda: self._show_more_tray(more))
            self.tray_icons.addWidget(more)
            self._tray_widgets.append(more)
        self.size_changed.emit()  # com ícones a seção fica mais alta que o "Nenhum app"

    def _populate_tray_actions(
        self,
        menu: QMenu,
        app: apps.TrayApp,
        *,
        include_header: bool = True,
    ) -> None:
        if include_header:
            header = menu.addAction(app.name)
            header.setEnabled(False)
            menu.addSeparator()
        menu.addAction(
            icons.icon("external", theme.TEXT_2, 15),
            "Abrir",
            lambda _c=False, a=app: self._launch_tray_app(a),
        )
        if app.exe:
            menu.addAction(
                icons.icon("folder-open", theme.TEXT_2, 15),
                "Abrir local do arquivo",
                lambda _c=False, p=app.exe: apps.open_location(p),
            )
        menu.addSeparator()
        menu.addAction(
            icons.icon("close", theme.TEXT, 15),
            "Fechar aplicativo",
            lambda _c=False, a=app: self._close_tray_app(a, force=False),
        )
        menu.addAction(
            icons.icon("power", theme.DANGER, 15),
            "Encerrar processo",
            lambda _c=False, a=app: self._close_tray_app(a, force=True),
        )

    def _exec_menu(self, menu: QMenu, pos: QPoint) -> None:
        if hasattr(self.ctx, "keep_open"):
            with self.ctx.keep_open():  # type: ignore[attr-defined]
                menu.exec(pos)
        else:
            menu.exec(pos)

    def _tray_menu(self, app: apps.TrayApp, pos: QPoint) -> None:
        menu = brand.ink_menu(self)
        self._populate_tray_actions(menu, app, include_header=True)
        self._exec_menu(menu, pos)

    def _show_more_tray(self, anchor: QWidget) -> None:
        menu = brand.ink_menu(self)
        for app in self._tray_apps[TRAY_SHOWN:TRAY_SHOWN + 20]:
            pm = QPixmap()
            if app.icon:
                pm.loadFromData(app.icon)
            ico = QIcon(pm) if not pm.isNull() else icons.icon("apps", theme.TEXT_2, 14)
            sub = menu.addMenu(ico, app.name)
            self._populate_tray_actions(sub, app, include_header=False)
        self._exec_menu(menu, anchor.mapToGlobal(QPoint(0, anchor.height() + 4)))

    def _close_tray_app(self, app: apps.TrayApp, *, force: bool = False) -> None:
        def done(result: tuple[bool, str]) -> None:
            ok, err = result
            if not ok and err and hasattr(self.ctx, "tray_message"):
                self.ctx.tray_message.emit("Não deu para fechar", f"{app.name}: {err}")  # type: ignore[attr-defined]
            QTimer.singleShot(450, self._refresh_tray_apps)
            if not force:
                QTimer.singleShot(1200, self._refresh_tray_apps)

        if hasattr(self.ctx, "run"):
            self.ctx.run(  # type: ignore[attr-defined]
                lambda e=app.exe, f=force: apps.close_background_app(e, force=f),
                done=done,
                owner=self,
            )
        else:
            done(apps.close_background_app(app.exe, force=force))

    def _refresh_tray_apps(self) -> None:
        if self.isVisible() and hasattr(self.ctx, "run"):
            self.ctx.run(apps.tray_apps, done=self.set_tray_apps, owner=self)  # type: ignore[attr-defined]

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
        self.output_row.set_text(out_name or "Saída de áudio", expanded=not self.devices_box.isHidden())
        default_in = getattr(state, "default_input", None)
        self.mic_name.setText(getattr(default_in, "name", "") or "Microfone")
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
                "check" if device.default else "speaker", device.name, "", QSize(10, 34), 15, 11
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
