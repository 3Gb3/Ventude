"""Dock do Ventude: barra flutuante em forma de pílula no lugar da barra de tarefas do Windows.

- Botão do Ventude (abre o Menu Iniciar / Busca Universal na ilha).
- Atalhos fixados (os mesmos da aba Atalhos) + janelas abertas agora, agrupadas por app, com o ícone
  real do .exe. Ponto embaixo: app aberto; traço na cor de destaque: janela em primeiro plano.
- Clique foca (ou minimiza se já está em foco); clique do meio fecha; botão direito abre o menu.
- Some deslizando quando uma janela maximizada/tela cheia ocupa o monitor e volta ao encostar o
  mouse nos 4 px de baixo.

O DockController liga e desliga tudo pelo ajuste "Substituir barra de tarefas pelo Dock do Ventude"
e garante que a barra do Windows volte ao desligar, ao sair ou em caso de erro.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QFileInfo,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QCursor, QIcon, QImage, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFileIconProvider, QHBoxLayout, QMenu, QWidget

from ventude.core.timefmt import clock
from ventude.services import launcher, taskbar
from ventude.services.taskbar import TaskbarHider, WindowInfo
from ventude.ui import icons, theme
from ventude.ui.widgets import label, vbox

log = logging.getLogger(__name__)

BUTTON = 46
ICON = 28
HEIGHT = 62
MARGIN = 8
WINDOWS_MS = 1000
COVER_MS = 700
EDGE_MS = 120
EDGE_PX = 4


@dataclass
class DockItem:
    key: str  # caminho do .exe em minúsculas, ou o alvo do atalho
    label: str
    path: str  # .exe (para o ícone) ou alvo do atalho
    kind: str = "app"  # app | folder | file | url | window
    target: str = ""  # o que abrir quando não há janela
    shortcut_id: int = 0  # 0: não fixado
    windows: list[WindowInfo] = field(default_factory=list)

    @property
    def pinned(self) -> bool:
        return self.shortcut_id > 0

    @property
    def active(self) -> bool:
        return any(w.foreground for w in self.windows)


def norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)) if path else ""


def shortcut_exe(target: str) -> str:
    """Exe por trás de um atalho fixado (.lnk resolvido; .exe direto)."""
    if target.lower().endswith(".lnk"):
        from ventude.services.winutil import lnk_target

        return lnk_target(target) or target
    return target


def group_windows(shortcuts: list[object], windows: list[WindowInfo]) -> list[DockItem]:
    """Fixados primeiro (na ordem da aba Atalhos), depois os apps abertos, juntando janelas do mesmo .exe."""
    items: list[DockItem] = []
    by_exe: dict[str, DockItem] = {}
    for shortcut in shortcuts:
        kind, target = shortcut.kind, shortcut.target  # type: ignore[attr-defined]
        exe = shortcut_exe(target) if kind == "app" else ""
        item = DockItem(
            key=norm(exe or target),
            label=shortcut.label,  # type: ignore[attr-defined]
            path=exe or target,
            kind=kind,
            target=target,
            shortcut_id=int(shortcut.id),  # type: ignore[attr-defined]
        )
        items.append(item)
        if exe:
            by_exe.setdefault(norm(exe), item)
    for window in windows:
        key = norm(window.exe) or f"hwnd:{window.hwnd}"
        item = by_exe.get(key)
        if item is None:
            from ventude.services.winutil import app_name

            item = DockItem(
                key=key, label=app_name(window.exe) or window.title, path=window.exe, kind="window"
            )
            by_exe[key] = item
            items.append(item)
        item.windows.append(window)
    return items


class DockButton(QWidget):
    """Um app no Dock: ícone, ponto de "aberto" e traço de "em foco"."""

    def __init__(self, dock: Dock, item: DockItem, icon: QIcon) -> None:
        super().__init__(dock)
        self.dock, self.item, self.icon = dock, item, icon
        self.setFixedSize(BUTTON, BUTTON + 8)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._cycle = 0
        self.update_item(item)

    def update_item(self, item: DockItem) -> None:
        self.item = item
        titles = [w.title for w in item.windows]
        tip = item.label if not titles else "\n".join(titles[:6])
        self.setToolTip(tip + ("\n(fixado)" if item.pinned else ""))
        self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tile = QRectF(3, 3, BUTTON - 6, BUTTON - 6)
        if self.underMouse() or self.item.active:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color(theme.SURFACE_3 if self.underMouse() else theme.SURFACE_2, 0.95))
            p.drawRoundedRect(tile, 12, 12)
        pixmap = self.icon.pixmap(QSize(ICON, ICON))
        offset = (BUTTON - ICON) / 2
        p.drawPixmap(int(offset), int(offset), pixmap)
        count = len(self.item.windows)
        y = BUTTON + 2
        p.setPen(Qt.PenStyle.NoPen)
        if self.item.active:
            p.setBrush(theme.color(theme.ACCENT))
            p.drawRoundedRect(QRectF(BUTTON / 2 - 8, y, 16, 3), 1.5, 1.5)
        elif count:
            p.setBrush(theme.color(theme.TEXT_2))
            dots = min(count, 3)
            start = BUTTON / 2 - (dots - 1) * 3.5
            for index in range(dots):
                p.drawEllipse(QRectF(start + index * 7 - 1.75, y, 3.5, 3.5))

    def enterEvent(self, _event: object) -> None:
        self.update()

    def leaveEvent(self, _event: object) -> None:
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if not self.rect().contains(event.position().toPoint()):
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.dock.click(self)
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.dock.close_window(self.item)
        elif event.button() == Qt.MouseButton.RightButton:
            self.dock.menu(self, event.globalPosition().toPoint())


class Dock(QWidget):
    def __init__(self, ctx: object, open_start: object) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.ctx = ctx
        self._open_start = open_start
        self.setWindowTitle("Ventude Dock")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.items: list[DockItem] = []
        self.buttons: dict[str, DockButton] = {}
        self._icons: dict[str, QIcon] = {}
        self._file_icons = QFileIconProvider()
        self._busy = False
        self.slid = False  # escondido pela janela maximizada
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(10, 4, 14, 0)
        self.row.setSpacing(2)
        self.start = DockButton(
            self, DockItem("ventude", "Ventude: Menu Iniciar e busca", ""), self._start_icon()
        )
        self.row.addWidget(self.start)
        self.row.addSpacing(6)
        self.apps = QHBoxLayout()
        self.apps.setSpacing(2)
        self.row.addLayout(self.apps)
        self.row.addSpacing(10)
        self.clock = label("", "")
        self.clock.setStyleSheet("font-size: 12px; font-weight: 600;")
        self.date = label("", "Faint")
        column = QWidget()
        column.setLayout(vbox(None, self.clock, self.date, None, spacing=0))
        self.row.addWidget(column)
        self.anim = QPropertyAnimation(self, b"pos", self)
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(self._slide_done)
        self.windows_timer = QTimer(self, interval=WINDOWS_MS)
        self.windows_timer.timeout.connect(self.refresh)
        self.cover_timer = QTimer(self, interval=COVER_MS)
        self.cover_timer.timeout.connect(self._check_cover)
        self.edge_timer = QTimer(self, interval=EDGE_MS)
        self.edge_timer.timeout.connect(self._check_edge)
        self.clock_timer = QTimer(self, interval=1000)
        self.clock_timer.timeout.connect(self._tick)
        self.ctx.data_changed.connect(lambda kind: kind == "shortcuts" and self.refresh())  # type: ignore[attr-defined]
        self._tick()

    @staticmethod
    def _start_icon() -> QIcon:
        return icons.icon("ventude", theme.ACCENT, ICON)

    # ---------- ciclo de vida ----------
    def start_dock(self) -> None:
        self.slid = False
        self.refresh()
        self.place()
        self.show()
        for timer in (self.windows_timer, self.cover_timer, self.clock_timer):
            timer.start()

    def stop_dock(self) -> None:
        for timer in (self.windows_timer, self.cover_timer, self.clock_timer, self.edge_timer):
            timer.stop()
        self.anim.stop()
        self.hide()

    def screen_rect(self):  # noqa: ANN201
        from ventude.ui.island import screen_for

        return screen_for(self.ctx.settings.monitor_index).geometry()  # type: ignore[attr-defined]

    def shown_pos(self) -> QPoint:
        area = self.screen_rect()
        return QPoint(area.center().x() - self.width() // 2, area.bottom() - self.height() - MARGIN + 1)

    def hidden_pos(self) -> QPoint:
        area = self.screen_rect()
        return QPoint(area.center().x() - self.width() // 2, area.bottom() - EDGE_PX)

    def place(self) -> None:
        self.adjustSize()
        self.resize(self.sizeHint().width(), HEIGHT)
        if self.anim.state() != QPropertyAnimation.State.Running:
            self.move(self.hidden_pos() if self.slid else self.shown_pos())

    # ---------- janelas ----------
    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.ctx.run(taskbar.list_windows, done=self._apply, fail=self._failed, owner=self)  # type: ignore[attr-defined]

    def _failed(self, error: BaseException) -> None:
        self._busy = False
        log.debug("Lista de janelas falhou: %r", error)

    def _apply(self, windows: list[WindowInfo]) -> None:
        self._busy = False
        self.set_items(group_windows(self.ctx.store.shortcuts(), windows))  # type: ignore[attr-defined]

    def set_items(self, items: list[DockItem]) -> None:
        keys = [i.key for i in items]
        if keys != [i.key for i in self.items]:
            for button in self.buttons.values():
                self.apps.removeWidget(button)
                button.deleteLater()
            self.buttons.clear()
            for item in items:
                button = DockButton(self, item, self._icon(item))
                self.apps.addWidget(button)
                self.buttons[item.key] = button
            self.items = items
            self.place()
        else:
            self.items = items
            for item in items:
                self.buttons[item.key].update_item(item)

    def _icon(self, item: DockItem) -> QIcon:
        if item.key in self._icons:
            return self._icons[item.key]
        icon = QIcon()
        if item.kind == "url":
            icon = icons.icon("globe", theme.ACCENT, ICON)
        elif item.path and Path(item.path).exists():
            icon = self._file_icons.icon(QFileInfo(item.path))
        if icon.isNull() and item.windows:
            handle = taskbar.window_icon(item.windows[0].hwnd)
            if handle and hasattr(QImage, "fromHICON"):
                image = QImage.fromHICON(handle)
                if not image.isNull():
                    icon = QIcon(QPixmap.fromImage(image))
        if icon.isNull():
            icon = icons.icon("window", theme.TEXT_2, ICON)
        self._icons[item.key] = icon
        return icon

    # ---------- ações ----------
    def click(self, button: DockButton) -> None:
        item = button.item
        if button is self.start:
            self._open_start()  # type: ignore[operator]
            return
        windows = item.windows
        if not windows:
            launcher.open_target(item.kind if item.kind != "window" else "app", item.target or item.path)
            return
        if len(windows) == 1:
            window = windows[0]
            (taskbar.minimize if window.foreground and not window.minimized else taskbar.activate)(
                window.hwnd
            )
        else:
            current = next((n for n, w in enumerate(windows) if w.foreground), -1)
            taskbar.activate(windows[(current + 1) % len(windows)].hwnd)  # vários: alterna entre eles
        QTimer.singleShot(150, self.refresh)

    def close_window(self, item: DockItem) -> None:
        if item.windows:
            target = next((w for w in item.windows if w.foreground), item.windows[0])
            taskbar.close(target.hwnd)
            QTimer.singleShot(400, self.refresh)

    def menu(self, button: DockButton, pos: QPoint) -> None:
        item = button.item
        if button is self.start:
            return
        menu = QMenu(self)
        for window in item.windows[:8]:
            target = (
                menu.addMenu(icons.icon("window", theme.TEXT_2, 15), window.title[:60])
                if len(item.windows) > 1
                else menu
            )
            target.addAction(
                icons.icon("external", theme.TEXT_2, 15), "Focar", lambda h=window.hwnd: taskbar.activate(h)
            )
            target.addAction(
                icons.icon("minus", theme.TEXT_2, 15), "Minimizar", lambda h=window.hwnd: taskbar.minimize(h)
            )
            target.addAction(
                icons.icon("restore" if window.maximized else "maximize", theme.TEXT_2, 15),
                "Restaurar" if window.maximized else "Maximizar",
                lambda h=window.hwnd: taskbar.toggle_maximize(h),
            )
            target.addAction(
                icons.icon("close", theme.DANGER, 15), "Fechar janela", lambda h=window.hwnd: taskbar.close(h)
            )
        if not item.windows:
            menu.addAction(icons.icon("external", theme.TEXT_2, 15), "Abrir", lambda: self.click(button))
        menu.addSeparator()
        if item.pinned:
            menu.addAction(
                icons.icon("pin", theme.ACCENT, 15), "Tirar dos Atalhos", lambda: self._unpin(item)
            )
        elif item.path:
            menu.addAction(icons.icon("pin", theme.TEXT_2, 15), "Fixar nos Atalhos", lambda: self._pin(item))
        menu.exec(pos)
        QTimer.singleShot(200, self.refresh)

    def _pin(self, item: DockItem) -> None:
        self.ctx.store.add_shortcut("app", item.label, item.path)  # type: ignore[attr-defined]
        self.ctx.data_changed.emit("shortcuts")  # type: ignore[attr-defined]

    def _unpin(self, item: DockItem) -> None:
        self.ctx.store.remove_shortcut(item.shortcut_id)  # type: ignore[attr-defined]
        self.ctx.data_changed.emit("shortcuts")  # type: ignore[attr-defined]

    # ---------- some e volta ----------
    def _check_cover(self) -> None:
        area = self.screen_rect()
        rect = (area.left(), area.top(), area.right() + 1, area.bottom() + 1)
        covered = taskbar.covering_window(rect)
        hovering = self.isVisible() and self.geometry().contains(QCursor.pos())
        if covered and not self.slid and not hovering:
            self.slide(True)
        elif not covered and self.slid:
            self.slide(False)

    def _check_edge(self) -> None:
        pos = QCursor.pos()
        area = self.screen_rect()
        if area.contains(pos) and pos.y() >= area.bottom() - EDGE_PX:
            self.slide(False, peek=True)

    def slide(self, away: bool, peek: bool = False) -> None:
        self.slid = away
        self.anim.stop()
        if away:
            self.anim.setStartValue(self.pos())
            self.anim.setEndValue(self.hidden_pos())
            self.edge_timer.start()
        else:
            self.edge_timer.stop()
            if not self.isVisible():
                self.move(self.hidden_pos())
                self.show()
            self.anim.setStartValue(self.pos())
            self.anim.setEndValue(self.shown_pos())
            if peek:  # apareceu pelo mouse: some de novo quando ele sair (se ainda estiver coberto)
                self.cover_timer.start()
        self.anim.start()

    def _slide_done(self) -> None:
        if self.slid:
            self.hide()

    def leaveEvent(self, _event: object) -> None:
        QTimer.singleShot(500, self._check_cover)

    def _tick(self) -> None:
        now = datetime.now()
        self.clock.setText(clock(now, self.ctx.settings.clock_24h))  # type: ignore[attr-defined]
        self.date.setText(f"{now:%d/%m}")

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(theme.ISLAND_RADIUS, rect.height() / 2)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.fillPath(path, QColor(theme.BG))
        p.setPen(QPen(QColor(255, 255, 255, 20), 1))
        p.drawPath(path)


class DockController(QObject):
    """Liga/desliga o Dock e a barra do Windows pelo ajuste, com restauração garantida."""

    def __init__(self, ctx: object, open_start: object, hider: TaskbarHider | None = None) -> None:
        super().__init__()
        self.ctx = ctx
        self.hider = hider or TaskbarHider()
        self.dock: Dock | None = None
        self._open_start = open_start
        self.failed = False  # um erro desligou a troca até a próxima abertura (ou mexer no ajuste)
        self.timer = QTimer(self, interval=2000)
        self.timer.timeout.connect(self.hider.reassert)
        ctx.settings_changed.connect(lambda _s: self.sync())  # type: ignore[attr-defined]
        self._wanted = False

    def start(self) -> None:
        if not self.ctx.settings.replace_taskbar:  # type: ignore[attr-defined]
            self.hider.recover()  # o Ventude anterior fechou com a barra escondida
        self.sync()

    @property
    def wanted(self) -> bool:
        return bool(self.ctx.settings.replace_taskbar) and sys.platform == "win32"  # type: ignore[attr-defined]

    def sync(self) -> None:
        wanted = self.wanted
        if wanted != self._wanted:
            self.failed = False  # mexeu no ajuste: tenta de novo
            self._wanted = wanted
        if wanted and not self.failed:
            if self.dock is None:
                self.dock = Dock(self.ctx, self._open_start)
            if not self.hider.hidden:
                if not self.hider.hide():
                    log.warning("Barra do Windows não encontrada: o Dock aparece mesmo assim")
                self.dock.start_dock()
                self.timer.start()
        else:
            self.restore()

    def restore(self) -> None:
        """Sempre seguro: mostra a barra do Windows de volta e esconde o Dock."""
        self.timer.stop()
        if self.hider.hidden or self.hider.marker.exists():
            self.hider.restore()
        if self.dock is not None:
            self.dock.stop_dock()

    def emergency(self) -> None:
        """Erro inesperado: a barra do Windows volta na hora e fica até mexer no ajuste."""
        self.failed = True
        try:
            self.restore()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao restaurar a barra de tarefas")
