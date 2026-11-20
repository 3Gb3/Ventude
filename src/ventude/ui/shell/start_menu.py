"""Menu Iniciar do Ventude Shell (botão do logo na barra).

- Campo de pesquisa no topo: ao digitar, vira a mesma busca da tela de Pesquisa.
- Fixado: apps, pastas, arquivos e páginas de Configurações (botão direito: desafixar, mover para o
  início, fixar na barra, abrir local, executar como administrador).
- Recente: o que você abriu pelo Ventude e os arquivos recentes do Windows ("Mostrar tudo" expande).
- Todos: todos os apps instalados por Categoria (clique abre a categoria), em Grade ou em Lista.
- Rodapé: sua conta (bloquear, sair, configurações da conta), Ajustes do Ventude, Explorador e Energia.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ventude.services import apps
from ventude.ui import icons, theme
from ventude.ui.shell.common import Avatar, HoverButton, ShellWindow, shell_font
from ventude.ui.shell.search import SearchResults, search_field
from ventude.ui.shell.tiles import AppList, Tile, TileGrid

START_SIZE = QSize(680, 740)
PINNED_ROWS = 3
RECENT_ROWS = 3
VIEWS = (("category", "Categoria"), ("grid", "Grade"), ("list", "Lista"))


class SectionHeader(QWidget):
    """Título da seção à esquerda e um link/controle à direita."""

    def __init__(self, title: str, side: QWidget | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 0, 0, 0)
        label = QLabel(title)
        label.setStyleSheet("font-size: 14px; font-weight: 600;")
        row.addWidget(label)
        row.addStretch(1)
        if side is not None:
            row.addWidget(side)


class LinkButton(HoverButton):
    """'Mostrar tudo ›' no canto das seções."""

    def __init__(self, text: str) -> None:
        super().__init__("chevron-right", "", "", QSize(10, 30), 12, 6)
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self.text = text
        self.setFixedSize(QFontMetrics(shell_font(12)).horizontalAdvance(text) + 44, 30)
        self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = self.background()
        if bg is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)
        p.setPen(QColor(theme.TEXT))
        p.setFont(shell_font(12))
        p.drawText(
            self.rect().adjusted(10, 0, -26, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            self.text,
        )
        p.drawPixmap(
            self.width() - 22, (self.height() - 12) // 2, icons.pixmap(self.icon_name, theme.TEXT_2, 12)
        )


class PersonButton(HoverButton):
    """Foto e nome da conta no rodapé do Iniciar."""

    def __init__(self) -> None:
        super().__init__("", "", "Sua conta", QSize(240, 44), 16, 6)
        self.text = "Você"
        self.avatar = Avatar("Você", "", 32, self)
        self.avatar.move(8, 6)
        self.avatar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_person(self, name: str, picture: str) -> None:
        self.text = name
        self.avatar.set_person(name, picture)
        self.setToolTip(name)
        self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = self.background()
        if bg is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)
        p.setPen(QColor(theme.TEXT))
        p.setFont(shell_font(13))
        p.drawText(self.rect().adjusted(52, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter, self.text)


class Footer(QWidget):
    """Rodapé: conta à esquerda; Ajustes, Explorador e Energia à direita."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(64)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 40))
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.drawLine(0, 0, self.width(), 0)


class StartMenu(ShellWindow):
    def __init__(self, ctx: object, shell: object) -> None:
        super().__init__(ctx)
        self.shell = shell
        self.setWindowTitle("Ventude: Iniciar")
        self.opened_at = 0.0
        self.expanded_pins = False
        self.expanded_recent = False
        # ---------- topo ----------
        self.input = search_field("Pesquisar aplicativos, configurações e documentos")
        self.input.textChanged.connect(self._typed)
        self.input.installEventFilter(self)
        # ---------- página inicial ----------
        self.pinned = TileGrid(ctx, "tile", 6, QSize(98, 92), 32)
        self.pinned.empty_text = "Clique com o botão direito num app para fixar aqui"
        self.pinned.activated.connect(self._activate)
        self.pinned.context.connect(self._tile_menu)
        self.pins_more = LinkButton("Mostrar mais")
        self.pins_more.clicked.connect(self._toggle_pins)
        self.recent = TileGrid(ctx, "row", 2, QSize(300, 56), 32)
        self.recent.empty_text = "Os arquivos e apps que você abrir aparecem aqui"
        self.recent.activated.connect(self._activate)
        self.recent.context.connect(self._tile_menu)
        self.recent_more = LinkButton("Mostrar tudo")
        self.recent_more.clicked.connect(self._toggle_recent)
        self.view = QComboBox()
        for key, text in VIEWS:
            self.view.addItem(f"Exibição: {text}", key)
        self.view.setFixedHeight(30)
        self.view.setStyleSheet(
            "QComboBox { background: transparent; border: none; padding: 4px 8px; font-size: 12px; }"
            "QComboBox:hover { background: rgba(255,255,255,0.06); border-radius: 6px; }"
        )
        self.view.currentIndexChanged.connect(self._view_changed)
        self.categories = TileGrid(ctx, "category", 4, QSize(150, 150), 30)
        self.categories.activated.connect(self._open_category)
        self.grid = TileGrid(ctx, "tile", 6, QSize(98, 92), 32)
        self.grid.activated.connect(self._activate)
        self.grid.context.connect(self._tile_menu)
        self.list = AppList(ctx)
        self.list.activated.connect(self._activate)
        self.list.context.connect(self._tile_menu)
        self.all_stack = QStackedWidget()
        for widget in (self.categories, self.grid, self.list):
            self.all_stack.addWidget(widget)
        home = QWidget()
        body = QVBoxLayout(home)
        body.setContentsMargins(28, 6, 28, 18)
        body.setSpacing(8)
        body.addWidget(SectionHeader("Fixado", self.pins_more))
        body.addWidget(self.pinned)
        body.addSpacing(10)
        body.addWidget(SectionHeader("Recente", self.recent_more))
        body.addWidget(self.recent)
        body.addSpacing(10)
        body.addWidget(SectionHeader("Todos", self.view))
        body.addWidget(self.all_stack)
        body.addStretch(1)
        self.home_scroll = QScrollArea()
        self.home_scroll.setWidgetResizable(True)
        self.home_scroll.setWidget(home)
        self.home_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # ---------- categoria aberta ----------
        self.category_page = QWidget()
        back = HoverButton("back", "", "Voltar", QSize(32, 32), 16, 6)
        back.clicked.connect(self.show_home)
        self.category_title = QLabel("")
        self.category_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        top = QHBoxLayout()
        top.addWidget(back)
        top.addWidget(self.category_title)
        top.addStretch(1)
        self.category_grid = TileGrid(ctx, "tile", 6, QSize(98, 92), 32)
        self.category_grid.activated.connect(self._activate)
        self.category_grid.context.connect(self._tile_menu)
        category_body = QWidget()
        category_layout = QVBoxLayout(category_body)
        category_layout.setContentsMargins(28, 6, 28, 18)
        category_layout.addLayout(top)
        category_layout.addWidget(self.category_grid)
        category_layout.addStretch(1)
        category_scroll = QScrollArea()
        category_scroll.setWidgetResizable(True)
        category_scroll.setWidget(category_body)
        self.category_page = category_scroll
        # ---------- resultados da busca ----------
        self.results = SearchResults(ctx, shell, detail=True)
        self.results.finished.connect(self.dismiss)
        results_page = QWidget()
        results_layout = QVBoxLayout(results_page)
        results_layout.setContentsMargins(28, 8, 28, 12)
        results_layout.addWidget(self.results)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.home_scroll)
        self.pages.addWidget(self.category_page)
        self.pages.addWidget(results_page)
        self.results_page = results_page
        # ---------- rodapé ----------
        self.footer = Footer()
        foot = QHBoxLayout(self.footer)
        foot.setContentsMargins(40, 0, 32, 0)
        foot.setSpacing(4)
        self.person = PersonButton()
        self.person.clicked.connect(self._account_menu)
        foot.addWidget(self.person)
        foot.addStretch(1)
        self.settings_btn = HoverButton("settings", "", "Ajustes do Ventude", QSize(40, 40), 18, 6)
        self.settings_btn.clicked.connect(lambda: (self.dismiss(), shell.open_settings()))  # type: ignore[attr-defined]
        self.explorer_btn = HoverButton("folder", "", "Explorador de Arquivos", QSize(40, 40), 18, 6)
        self.explorer_btn.clicked.connect(
            lambda: shell.launch("app", "explorer.exe", "Explorador", record=False)
        )  # type: ignore[attr-defined]
        self.power_btn = HoverButton("power", "", "Energia", QSize(40, 40), 18, 6)
        self.power_btn.clicked.connect(lambda: shell.show_power(self.power_btn))  # type: ignore[attr-defined]
        for button in (self.settings_btn, self.explorer_btn, self.power_btn):
            foot.addWidget(button)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        top_area = QWidget()
        top_layout = QVBoxLayout(top_area)
        top_layout.setContentsMargins(32, 26, 32, 14)
        top_layout.addWidget(self.input)
        root.addWidget(top_area)
        root.addWidget(self.pages, 1)
        root.addWidget(self.footer)
        ctx.indexer.apps_ready.connect(lambda _n: self.refresh_apps())  # type: ignore[attr-defined]
        ctx.indexer.ready.connect(lambda _n: self.refresh_apps())  # type: ignore[attr-defined]
        self._recent_files: list[apps.RecentFile] = []
        self._loading_recent = False
        view = getattr(ctx.settings, "start_view", "category")  # type: ignore[attr-defined]
        self.view.setCurrentIndex(max(0, [k for k, _ in VIEWS].index(view) if view in dict(VIEWS) else 0))

    # ---------- pessoa ----------
    def set_person(self, name: str, picture: str) -> None:
        self.person.set_person(name, picture)

    def set_power_badge(self, pending: bool) -> None:
        self.power_btn.set_icon("power-badge" if pending else "power", theme.WARN if pending else theme.TEXT)
        self.power_btn.setToolTip("Energia · atualização do Windows esperando" if pending else "Energia")

    # ---------- dados ----------
    def refresh(self) -> None:
        self.refresh_pins()
        self.refresh_recent()
        self.refresh_apps()

    def refresh_pins(self) -> None:
        pins = self.ctx.store.pins("start")  # type: ignore[attr-defined]
        tiles = [Tile(p.label, p.kind, p.target, pin_id=p.id, source="pinned") for p in pins]
        self.pinned.limit = 0 if self.expanded_pins else 6 * PINNED_ROWS
        self.pinned.set_tiles(tiles)
        self.pins_more.setVisible(len(tiles) > 6 * PINNED_ROWS)
        self.pins_more.set_text("Mostrar menos" if self.expanded_pins else "Mostrar mais")

    def refresh_recent(self) -> None:
        self._apply_recent()
        if not self._loading_recent:
            self._loading_recent = True
            self.ctx.run(
                apps.recent_files,
                16,
                done=self._recent_loaded,
                fail=lambda _e: self._recent_loaded([]),
                owner=self,
            )  # type: ignore[attr-defined]

    def _recent_loaded(self, files: list[apps.RecentFile]) -> None:
        self._loading_recent = False
        self._recent_files = files
        self._apply_recent()

    def _apply_recent(self) -> None:
        now = time.time()
        tiles: list[Tile] = []
        seen: set[str] = set()
        for launch in self.ctx.store.launches(10):  # type: ignore[attr-defined]
            if launch.kind == "web" or launch.target.lower() in seen:
                continue
            seen.add(launch.target.lower())
            tiles.append(
                Tile(
                    launch.label,
                    launch.kind,
                    launch.target,
                    apps.relative_time(launch.ts, now),
                    ts=launch.ts,
                    source="recent-launch",
                )
            )
        for item in self._recent_files:
            if item.path.lower() in seen:
                continue
            seen.add(item.path.lower())
            kind = "folder" if item.is_dir else "file"
            tiles.append(
                Tile(
                    item.name,
                    kind,
                    item.path,
                    apps.relative_time(item.ts, now),
                    ts=item.ts,
                    source="recent-file",
                )
            )
        tiles.sort(key=lambda t: t.ts, reverse=True)
        self.recent.limit = 2 * (RECENT_ROWS * 3 if self.expanded_recent else RECENT_ROWS)
        self.recent.set_tiles(tiles)
        self.recent_more.setVisible(len(tiles) > 2 * RECENT_ROWS)
        self.recent_more.set_text("Mostrar menos" if self.expanded_recent else "Mostrar tudo")

    def app_tiles(self) -> list[Tile]:
        return [Tile(e.name, e.kind, e.path, source="app") for e in self.ctx.indexer.apps]  # type: ignore[attr-defined]

    def refresh_apps(self) -> None:
        tiles = self.app_tiles()
        groups: dict[str, list[Tile]] = {}
        for tile in tiles:
            groups.setdefault(apps.category(tile.label, tile.target), []).append(tile)
        categories = [
            Tile(name, "category", name, children=sorted(groups[name], key=lambda t: t.label.lower()))
            for name in apps.CATEGORY_ORDER
            if name in groups
        ]
        self.categories.set_tiles(categories)
        self.grid.set_tiles(sorted(tiles, key=lambda t: t.label.lower()))
        self.list.set_tiles(tiles)
        self._sync_all_height()
        if self.pages.currentWidget() is self.category_page and self.category_title.text():
            self._fill_category(self.category_title.text(), groups.get(self.category_title.text(), []))

    # ---------- interação ----------
    def _toggle_pins(self) -> None:
        self.expanded_pins = not self.expanded_pins
        self.refresh_pins()

    def _toggle_recent(self) -> None:
        self.expanded_recent = not self.expanded_recent
        self._apply_recent()

    def _view_changed(self, index: int) -> None:
        key = self.view.itemData(index)
        self.all_stack.setCurrentIndex(index)
        widget = self.all_stack.currentWidget()
        self.all_stack.setFixedHeight(widget.height())
        if getattr(self.ctx.settings, "start_view", "") != key:  # type: ignore[attr-defined]
            self.ctx.update_settings(start_view=key)  # type: ignore[attr-defined]

    def _sync_all_height(self) -> None:
        self.all_stack.setFixedHeight(self.all_stack.currentWidget().height())

    def _open_category(self, tile: Tile) -> None:
        self._fill_category(tile.label, tile.children)
        self.pages.setCurrentWidget(self.category_page)
        self.category_grid.play_reveal(self.animation_ms() + 120)

    def _fill_category(self, title: str, children: list[Tile]) -> None:
        self.category_title.setText(title)
        self.category_grid.set_tiles(children)

    def show_home(self) -> None:
        self.pages.setCurrentWidget(self.home_scroll)

    def _activate(self, tile: Tile) -> None:
        if tile.kind == "category":
            self._open_category(tile)
            return
        self.shell.launch(tile.kind, tile.target, tile.label)  # type: ignore[attr-defined]

    def _tile_menu(self, tile: Tile, pos: QPoint) -> None:
        self.shell.tile_menu(self, tile, pos)  # type: ignore[attr-defined]

    def _account_menu(self) -> None:
        self.shell.account_menu(self.person)  # type: ignore[attr-defined]

    def _typed(self, text: str) -> None:
        if text.strip():
            self.results.set_query(text)
            self.pages.setCurrentWidget(self.results_page)
        else:
            self.pages.setCurrentWidget(self.home_scroll)

    # ---------- abrir e fechar ----------
    def open_menu(self, rect: QRect) -> None:
        self.opened_at = time.monotonic()
        self.input.clear()
        self.expanded_pins = self.expanded_recent = False
        self.show_home()
        self.refresh()
        self.home_scroll.verticalScrollBar().setValue(0)
        self.open_at(rect)
        QTimer.singleShot(0, self._sync_all_height)
        duration = self.animation_ms() + 160
        self.pinned.play_reveal(duration)
        self.recent.play_reveal(duration + 80)
        self.activateWindow()
        try:
            from ventude.services.winutil import force_foreground

            force_foreground(int(self.winId()))
        except Exception:  # noqa: BLE001
            pass
        self.input.setFocus()

    def dismiss(self) -> None:
        self.close_animated()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.input and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() == Qt.Key.Key_Escape:
                if self.input.text():
                    self.input.clear()
                else:
                    self.dismiss()
                return True
            if self.input.text().strip() and self.results.handle_key(event):
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self.pages.currentWidget() is self.category_page:
                self.show_home()
            else:
                self.dismiss()
            return
        text = event.text()
        if text and text.isprintable() and not text.isspace():
            self.input.setFocus()
            self.input.insert(text)
            return
        super().keyPressEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self.isVisible():
            if time.monotonic() - self.opened_at > 0.35:
                QTimer.singleShot(60, self._close_if_inactive)

    def _close_if_inactive(self) -> None:
        from PySide6.QtWidgets import QApplication

        if (
            self.isVisible()
            and not self.isActiveWindow()
            and not self.closing
            and QApplication.activePopupWidget() is None
        ):
            if not self.shell.flyout_open():  # type: ignore[attr-defined]
                self.dismiss()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.input.setFocus()
        super().mousePressEvent(event)
