"""Apps em segundo plano (ícones da bandeja que estão abertos), agora dentro do Lançador.

Antes moravam na seção "Em segundo plano" dos Controles da ilha. Aqui ganham espaço: grade com nome,
clique abre, botão direito traz abrir local, fechar e encerrar. A Doca tem um botão que abre esta página.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QMenu, QScrollArea, QVBoxLayout, QWidget

from ventude.services import apps
from ventude.ui import brand, icons, theme
from ventude.ui.shell.common import HoverButton, app_pixmap, draw_pixmap, shell_font

COLUMNS = 7
TILE = QSize(84, 82)


class BackgroundTile(QWidget):
    """Um app em segundo plano: ícone real e nome embaixo."""

    def __init__(
        self,
        app: apps.TrayApp,
        ctx: object,
        open_: Callable[[apps.TrayApp], None],
        menu_: Callable[[apps.TrayApp, QPoint], None],
    ) -> None:
        super().__init__()
        self.app, self.ctx, self._open, self._menu = app, ctx, open_, menu_
        self.setFixedSize(TILE)
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
        if self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.HALO)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 14, 14)
        pixmap = self.pixmap if not self.pixmap.isNull() else app_pixmap(self.ctx, "app", self.app.exe, 30)
        draw_pixmap(p, pixmap, QRectF((self.width() - 30) / 2, 12, 30, 30))
        p.setPen(QColor(theme.TEXT))
        p.setFont(shell_font(11))
        rect = QRectF(4, 48, self.width() - 8, 28)
        name = p.fontMetrics().elidedText(self.app.name, Qt.TextElideMode.ElideRight, int(rect.width()))
        p.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, name)

    def enterEvent(self, _event: object) -> None:
        self.update()

    def leaveEvent(self, _event: object) -> None:
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if not self.rect().contains(event.position().toPoint()):
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._open(self.app)
        elif event.button() == Qt.MouseButton.RightButton:
            self._menu(self.app, event.globalPosition().toPoint())
        event.accept()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if event.reason() != QContextMenuEvent.Reason.Mouse:
            self._menu(self.app, event.globalPos())
        event.accept()


class BackgroundView(QWidget):
    """Página "Em segundo plano" do Lançador."""

    back_requested = Signal()
    finished = Signal()  # abriu um app: o Lançador fecha
    count_changed = Signal(int)

    def __init__(self, ctx: object, shell: object | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx, self.shell = ctx, shell
        self.apps: list[apps.TrayApp] = []
        self.tiles: list[BackgroundTile] = []
        self.loading = False

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        back = HoverButton("back", "", "Voltar ao Início", QSize(28, 28), 14, 10)
        back.clicked.connect(self.back_requested)
        self.title = QLabel("Em segundo plano")
        self.title.setStyleSheet("font-size: 14px; font-weight: 600;")
        refresh = HoverButton("refresh", "", "Atualizar", QSize(28, 28), 14, 10)
        refresh.clicked.connect(self.refresh)
        self.peek_btn = HoverButton(
            "dock", "", "Mostrar a bandeja do Windows por 12 s", QSize(28, 28), 14, 10
        )
        self.peek_btn.clicked.connect(self._peek_windows_tray)
        top.addWidget(back)
        top.addSpacing(4)
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(refresh)
        top.addWidget(self.peek_btn)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(4)
        self.grid.setVerticalSpacing(4)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; }"
            " QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        scroll.setWidget(self.grid_host)

        self.empty = QLabel("Nenhum app em segundo plano agora")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;")
        self.empty.hide()

        self.hint = QLabel("Clique abre · botão direito: abrir local, fechar ou encerrar · Esc volta")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        root.addLayout(top)
        root.addWidget(scroll, 1)
        root.addWidget(self.empty, 1)
        root.addWidget(self.hint)
        self._scroll = scroll

    # ---------- dados ----------
    def refresh(self) -> None:
        run = getattr(self.ctx, "run", None)
        if run is None or self.loading:
            return
        self.loading = True

        def done(found: list[apps.TrayApp]) -> None:
            self.loading = False
            self.set_apps(found)

        def failed(_error: BaseException) -> None:
            self.loading = False

        run(apps.tray_apps, done=done, fail=failed, owner=self)

    def set_apps(self, found: list[apps.TrayApp]) -> None:
        self.apps = list(found)
        for tile in self.tiles:
            self.grid.removeWidget(tile)
            tile.hide()
            tile.deleteLater()
        self.tiles = []
        for index, app in enumerate(self.apps):
            tile = BackgroundTile(app, self.ctx, self.launch, self.show_menu)
            self.grid.addWidget(tile, index // COLUMNS, index % COLUMNS)
            self.tiles.append(tile)
        has = bool(self.apps)
        self._scroll.setVisible(has)
        self.empty.setVisible(not has)
        self.title.setText(f"Em segundo plano · {len(self.apps)}" if has else "Em segundo plano")
        self.count_changed.emit(len(self.apps))

    # ---------- ações ----------
    def launch(self, app: apps.TrayApp) -> None:
        self.finished.emit()
        if self.shell is not None and hasattr(self.shell, "launch"):
            self.shell.launch("app", app.exe, app.name, record=False)  # type: ignore[attr-defined]
        else:
            from ventude.services import launcher

            launcher.open_target("app", app.exe)

    def populate_menu(self, menu: QMenu, app: apps.TrayApp, *, header: bool = True) -> None:
        if header:
            title = menu.addAction(app.name)
            title.setEnabled(False)
            menu.addSeparator()
        menu.addAction(
            icons.icon("external", theme.TEXT_2, 15), "Abrir", lambda _c=False, a=app: self.launch(a)
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
            lambda _c=False, a=app: self.close_app(a, force=False),
        )
        menu.addAction(
            icons.icon("power", theme.DANGER, 15),
            "Encerrar processo",
            lambda _c=False, a=app: self.close_app(a, force=True),
        )

    def show_menu(self, app: apps.TrayApp, pos: QPoint) -> None:
        menu = brand.ink_menu(self)
        self.populate_menu(menu, app)
        self._exec_menu(menu, pos)

    def _exec_menu(self, menu: QMenu, pos: QPoint) -> None:
        menu.exec(pos)

    def close_app(self, app: apps.TrayApp, *, force: bool = False) -> None:
        def done(result: tuple[bool, str]) -> None:
            ok, err = result
            if not ok and err and hasattr(self.ctx, "tray_message"):
                self.ctx.tray_message.emit("Não deu para fechar", f"{app.name}: {err}")  # type: ignore[attr-defined]
            QTimer.singleShot(450, self.refresh)
            if not force:
                QTimer.singleShot(1200, self.refresh)

        run = getattr(self.ctx, "run", None)
        if run is not None:
            run(lambda e=app.exe, f=force: apps.close_background_app(e, force=f), done=done, owner=self)
        else:
            done(apps.close_background_app(app.exe, force=force))

    def _peek_windows_tray(self) -> None:
        self.finished.emit()
        if self.shell is not None and hasattr(self.shell, "peek_windows_taskbar"):
            self.shell.peek_windows_taskbar()  # type: ignore[attr-defined]


__all__ = ["BackgroundTile", "BackgroundView"]
