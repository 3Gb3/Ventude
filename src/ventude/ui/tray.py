from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.island import Island


def app_icon() -> QIcon:
    result = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = size / 64
        p.setPen(QPen(QColor("#4A505B"), max(1.0, 2.5 * s)))
        p.setBrush(QColor(theme.BG))
        p.drawRoundedRect(QRectF(3 * s, 16 * s, 58 * s, 32 * s), 16 * s, 16 * s)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        p.drawEllipse(QRectF(39 * s, 25 * s, 14 * s, 14 * s))
        p.setBrush(QColor(theme.TEXT_2))
        p.drawRoundedRect(QRectF(12 * s, 29 * s, 20 * s, 6 * s), 3 * s, 3 * s)
        p.end()
        result.addPixmap(pm)
    return result


class Tray(QSystemTrayIcon):
    def __init__(self, ctx: AppContext, island: Island) -> None:
        super().__init__(app_icon())
        self.ctx, self.island = ctx, island
        menu = QMenu()
        menu.addAction(icons.icon("home", theme.TEXT_2, 15), "Abrir painel", self._open)
        self.visible_action = menu.addAction("Ocultar a ilha", self._toggle_visible)
        menu.addAction(
            icons.icon("refresh", theme.TEXT_2, 15), "Atualizar limites das IAs", ctx.limits.refresh_all
        )
        menu.addSeparator()
        self.monitors = menu.addMenu(icons.icon("monitor", theme.TEXT_2, 15), "Monitor")
        self.monitors.aboutToShow.connect(self._fill_monitors)
        menu.addAction(icons.icon("settings", theme.TEXT_2, 15), "Ajustes", lambda: self._open("settings"))
        menu.addSeparator()
        menu.addAction(icons.icon("power", theme.DANGER, 15), "Sair do Ventude", ctx.quit_requested.emit)
        self._menu = menu
        self.setContextMenu(menu)
        self.activated.connect(self._activated)
        ctx.tray_message.connect(self._alert)
        ctx.limits.changed.connect(lambda _p: self._tooltip())
        self._tooltip()

    def _tooltip(self) -> None:
        self.setToolTip(f"Ventude\n{self.ctx.limits.summary()}")

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if not self.island.isVisible():
                self.island.user_hidden(False)
            self.island.toggle()

    def _open(self, page: str | None = None) -> None:
        self.island.user_hidden(False)
        self.island.expand(page)
        self.island.raise_()
        self.island.activateWindow()
        self._sync_label()

    def _toggle_visible(self) -> None:
        self.island.user_hidden(self.island.isVisible())
        self._sync_label()

    def _sync_label(self) -> None:
        self.visible_action.setText("Ocultar a ilha" if self.island.isVisible() else "Mostrar a ilha")

    def _fill_monitors(self) -> None:
        self.monitors.clear()
        group = QActionGroup(self.monitors)
        for index, screen in enumerate(QGuiApplication.screens()):
            size = screen.geometry()
            action = QAction(f"{index + 1} · {size.width()}×{size.height()}", self.monitors, checkable=True)
            action.setChecked(index == self.ctx.settings.monitor_index)
            action.triggered.connect(
                lambda _c=False, i=index: self.ctx.update_settings(monitor_index=i, island_position_json="")
            )
            group.addAction(action)
            self.monitors.addAction(action)
        self._sync_label()

    def _alert(self, title: str, message: str) -> None:
        if self.supportsMessages():
            self.showMessage(title, message, app_icon(), 7000)
