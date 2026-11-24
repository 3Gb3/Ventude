"""Corpo do modo Controles da ilha: teclas rápidas (rede, Cafeína, microfone, Foco, ferramentas...)."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ventude.core.timefmt import clock
from ventude.services import apps, nightlight
from ventude.ui import icons, theme
from ventude.ui.shell.common import HoverButton, draw_pixmap

CONTROLS_WIDTH = 480
CONTROLS_MIN_HEIGHT = 200

class QuickTile(QWidget):
    """Tecla de Controles (104×66): ícone numa bolinha, nome e estado; ligada ganha o gradiente da marca."""

    clicked = Signal()

    def __init__(self, icon: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon_name = icon
        self.title = title
        self.detail = ""
        self.on = False
        self._pressed = False
        self.setFixedSize(104, 66)
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
        radius = 15.0
        hover = self.underMouse()
        p.setPen(QPen(theme.color("#FFFFFF", 0.10 if hover else 0.06), 1.0))
        p.setBrush(theme.color(theme.INK_KEY))
        p.drawRoundedRect(rect, radius, radius)
        if self.on:  # ligada: um véu do gradiente da marca
            p.setPen(QPen(theme.color(theme.ACCENT, 0.35), 1.0))
            p.setBrush(theme.accent_gradient(rect.left(), rect.top(), rect.right(), rect.bottom(), 0.16))
            p.drawRoundedRect(rect, radius, radius)
        if self._pressed:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.HALO_PRESS)
            p.drawRoundedRect(rect, radius, radius)
        elif hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.HALO)
            p.drawRoundedRect(rect, radius, radius)
        # ícone numa bolinha: cheia com o gradiente quando ligada
        badge = QRectF(10, 9, 24, 24)
        p.setPen(Qt.PenStyle.NoPen)
        if self.on:
            p.setBrush(theme.accent_gradient(badge.left(), badge.top(), badge.right(), badge.bottom()))
        else:
            p.setBrush(theme.color(theme.SURFACE_3))
        p.drawEllipse(badge)
        icon_fg = theme.ACCENT_TEXT if self.on else theme.TEXT_2
        draw_pixmap(p, icons.pixmap(self.icon_name, icon_fg, 14), QRectF(15, 14, 14, 14))
        width = int(rect.width() - 20)
        p.setPen(QColor(theme.TEXT))
        p.setFont(theme.font(12, theme.SEMI))
        title = p.fontMetrics().elidedText(self.title, Qt.TextElideMode.ElideRight, width)
        p.drawText(QRectF(11, 36, width, 14), Qt.AlignmentFlag.AlignVCenter, title)
        if self.detail:
            p.setPen(QColor(theme.ACCENT if self.on else theme.MUTED))
            p.setFont(theme.font(10, QFont.Weight.Medium))
            detail = p.fontMetrics().elidedText(self.detail, Qt.TextElideMode.ElideRight, width)
            p.drawText(QRectF(11, 50, width, 12), Qt.AlignmentFlag.AlignVCenter, detail)

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
    """Estado Controles da ilha (480 px): teclas rápidas e as ferramentas de tela.

    Som (volume, saída e microfone) mora na aba Áudio; apps em segundo plano e notificações ficam no
    Lançador e na ponta direita da Doca. As ferramentas que agem sobre a tela (conta-gotas e texto da
    tela) vieram do Lançador para cá, junto das outras teclas. A altura vem do conteúdo
    (`preferred_height`).
    """

    close_requested = Signal()
    size_changed = Signal()
    open_audio = Signal()
    caffeine_clicked = Signal()
    mic_clicked = Signal()
    tool_requested = Signal(str)  # "color" | "ocr"

    def __init__(self, ctx: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.network_kind = ""
        self.wifi = ""

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

        # ---------- teclas rápidas (4 × 2) ----------
        self.network = QuickTile("ethernet", "Rede")
        self.network.clicked.connect(lambda: self._open("ms-settings:network-status"))
        self.caffeine = QuickTile("coffee", "Cafeína")
        self.caffeine.clicked.connect(self._toggle_caffeine)
        self.mic = QuickTile("mic", "Microfone")
        self.mic.clicked.connect(self._toggle_mic)
        self.focus = QuickTile("focus", "Foco 25")
        self.focus.clicked.connect(self._toggle_focus)
        self.night = QuickTile("night", "Luz noturna")
        self.night.set_state(False, "Configurar")
        self.night.clicked.connect(self._toggle_night)
        self.quiet = QuickTile("bell-off", "Silêncio")
        self.quiet.clicked.connect(self._toggle_quiet)
        self.color = QuickTile("pipette", "Conta-gotas")
        self.color.set_state(False, "Copia uma cor")
        self.color.clicked.connect(lambda: self._tool("color"))
        self.ocr = QuickTile("scan", "Texto da tela")
        self.ocr.set_state(False, "Copia o texto")
        self.ocr.clicked.connect(lambda: self._tool("ocr"))

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for index, tile in enumerate(
            (
                self.network,
                self.caffeine,
                self.mic,
                self.focus,
                self.night,
                self.quiet,
                self.color,
                self.ocr,
            )
        ):
            grid.addWidget(tile, index // 4, index % 4)
        root.addLayout(grid)

        # ---------- rodapé ----------
        foot = QHBoxLayout()
        foot.setSpacing(2)
        self.audio_link = _link_btn("Som ›", "Volume, saídas, microfone e mixer (aba Áudio)")
        self.audio_link.clicked.connect(self.open_audio.emit)
        self.background_link = _link_btn("Em segundo plano ›", "Apps rodando em segundo plano (no Iniciar)")
        self.background_link.clicked.connect(lambda: self._launcher_page("background"))
        self.notif_link = _link_btn("Notificações ›", "Avisos do Ventude (no Iniciar)")
        self.notif_link.clicked.connect(self._open_notifications)
        self.win_link = _link_btn("Configurações ›", "Configurações do Windows")
        self.win_link.clicked.connect(lambda: self._open("ms-settings:"))
        foot.addWidget(self.audio_link)
        foot.addWidget(self.background_link)
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
        """Altura que o conteúdo pede na largura da ilha."""
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
        self._sync_night()

    def _sync_night(self, on: bool | None = None) -> None:
        on = nightlight.state() if on is None else on
        if on is None:
            self.night.set_state(False, "Configurar")
        else:
            self.night.set_state(on, "Ligada" if on else "Desligada")

    def _toggle_night(self) -> None:
        """Liga/desliga direto; se o Windows guardar o estado num formato novo, abre as Configurações."""
        current = nightlight.state()
        if current is None or not nightlight.set_on(not current):
            self._open("ms-settings:nightlight")
            return
        self._sync_night(not current)

    def _audio(self, state: object) -> None:
        mic = getattr(state, "mic", None)
        mic_muted = bool(getattr(state, "mic_muted", False))
        has_mic = mic is not None
        self.mic.set_state(
            has_mic and not mic_muted,
            "Mudo" if mic_muted else ("Ligado" if has_mic else "Nenhum"),
            "mic-off" if mic_muted else "mic",
        )

    def _toggle_mic(self) -> None:
        self.mic_clicked.emit()
        self.ctx.audio.toggle_mic_mute()  # type: ignore[attr-defined]

    def _toggle_caffeine(self) -> None:
        self.caffeine_clicked.emit()
        self.ctx.caffeine.toggle()  # type: ignore[attr-defined]

    def _toggle_quiet(self) -> None:
        cur = bool(getattr(self.ctx.settings, "quiet_mode", False))  # type: ignore[attr-defined]
        self.ctx.update_settings(quiet_mode=not cur)  # type: ignore[attr-defined]

    def _toggle_focus(self) -> None:
        timer = self.ctx.timer  # type: ignore[attr-defined]
        if timer.state is not None:
            timer.stop()
        else:
            timer.start_pomodoro()
        self.refresh()

    def _open(self, target: str) -> None:
        self.close_requested.emit()
        apps.open_system(target)

    def _launcher_page(self, page: str) -> bool:
        """Abre o Lançador do Shell numa página; sem o Shell, devolve False."""
        shell = getattr(self.ctx, "shell", None)
        if shell is None or not hasattr(shell, "open_page"):
            return False
        self.close_requested.emit()
        QTimer.singleShot(160, lambda: shell.open_page(page))  # type: ignore[attr-defined]
        return True

    def _open_notifications(self) -> None:
        if self._launcher_page("notifications"):
            return
        self.close_requested.emit()
        ok, _err = apps.open_system("ms-actioncenter:")
        if not ok:
            apps.send_keys(0x5B, 0x4E)  # Win + N

    def _tool(self, mode: str) -> None:
        """Conta-gotas e texto da tela: a ilha recolhe antes de congelar a tela."""
        self.tool_requested.emit(mode)
