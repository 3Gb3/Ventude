"""Lançador unificado da Ventude Shell 3.0 (Iniciar + Pesquisa em uma única folha em Ink)."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ventude.services import apps, power, winutil
from ventude.ui import brand, icons, motion, theme
from ventude.ui.shell.background import BackgroundView
from ventude.ui.shell.common import Avatar, HoverButton, ShellWindow, app_pixmap, draw_pixmap, shell_font
from ventude.ui.shell.items import find_matching_pin
from ventude.ui.shell.notifications import NotificationsView
from ventude.ui.shell.results import SearchResults
from ventude.ui.shell.tiles import AppList, Tile, TileGrid

LAUNCHER_WIDTH = 680
HEIGHT_EMPTY = 472
HEIGHT_ACTIVE = 560
SHADOW_PAD = 12
REOPEN_FAST_S = 3.0

SCOPES = (
    (">", "Comandos"),
    ("@", "Janelas"),
    ("/", "Arquivos"),
    ("=", "Calcular"),
    ("?", "Web"),
    ("!", "Energia"),
)

POWER_BUTTONS = (
    ("1", "lock", "Bloquear", "lock"),
    ("2", "sleep", "Suspender", "moon"),
    ("3", "restart", "Reiniciar", "refresh"),
    ("4", "shutdown", "Desligar", "power"),
    ("5", "logoff", "Sair", "log-out"),
)


class ScopeChip(QWidget):
    """Pílula de escopo (>, @, /, =, ?, !) na linha do buscador."""

    clicked = Signal(str)

    def __init__(self, prefix: str, tip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.prefix = prefix
        self.active = False
        self.setFixedSize(26, 24)
        self.setToolTip(f"{prefix} · {tip}")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_active(self, on: bool) -> None:
        if on != self.active:
            self.active = on
            self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.active:
            p.setPen(QPen(QColor(theme.ACCENT), 1.0))
            p.setBrush(QColor(110, 231, 183, 30))
        elif self.underMouse():
            p.setPen(QPen(QColor(theme.HAIRLINE), 1.0))
            p.setBrush(QColor(theme.HALO))
        else:
            p.setPen(QPen(QColor(theme.HAIRLINE), 1.0))
            p.setBrush(QColor(theme.INK_KEY))
        p.drawRoundedRect(rect, 11.0, 11.0)
        p.setPen(QColor(theme.ACCENT if self.active else theme.TEXT_2))
        p.setFont(shell_font(12, QFont.Weight.DemiBold, tabular=True))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.prefix)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.prefix)


class LauncherField(QWidget):
    """Campo de entrada superior com chips de escopo e toggle de Todos os apps."""

    scope_toggled = Signal(str)
    all_apps_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(44)
        self.input = QLineEdit(self)
        self.input.setPlaceholderText("Buscar apps, sites, pastas, fotos, vídeos, músicas…")
        self.input.setFixedHeight(40)
        self.input.addAction(
            icons.icon("search", theme.ACCENT, 15), QLineEdit.ActionPosition.LeadingPosition
        )
        self.input.setStyleSheet(
            f"QLineEdit {{ background: {theme.INK_RAISED}; border: 1px solid {theme.HAIRLINE_QSS};"
            f" border-radius: 20px; padding: 0 12px 0 10px; font-size: 13px; color: {theme.TEXT}; }}"
            f"QLineEdit:focus {{ border: 1px solid {theme.ACCENT}; }}"
        )
        self.clear_btn = HoverButton("close", "", "Limpar", QSize(26, 26), 13, 10, self)
        self.clear_btn.hide()
        self.clear_btn.clicked.connect(self.input.clear)

        self.chips: dict[str, ScopeChip] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.input, 1)
        for prefix, tip in SCOPES:
            chip = ScopeChip(prefix, tip, self)
            chip.clicked.connect(self._toggle_scope)
            self.chips[prefix] = chip
            row.addWidget(chip)
        row.addWidget(self.clear_btn)
        self.all_btn = HoverButton("apps", "", "Todos os apps", QSize(32, 32), 16, 10, self)
        self.all_btn.clicked.connect(self.all_apps_clicked)
        row.addWidget(self.all_btn)

        self.input.textChanged.connect(self._sync_ui)

    def _sync_ui(self, text: str) -> None:
        stripped = text.lstrip()
        head = stripped[:1] if stripped else ""
        for prefix, chip in self.chips.items():
            chip.set_active(head == prefix)
        self.clear_btn.setVisible(bool(text))

    def _toggle_scope(self, prefix: str) -> None:
        cur = self.input.text().lstrip()
        if cur.startswith(prefix):
            self.input.setText(cur[1:].lstrip())
        elif cur and cur[0] in self.chips:
            self.input.setText(f"{prefix} {cur[1:].lstrip()}")
        else:
            self.input.setText(f"{prefix} {cur}" if cur else f"{prefix} ")
        self.input.setFocus()
        self.scope_toggled.emit(prefix)


class ContinueRow(QWidget):
    """Linha compacta da seção Continuar (com forget no hover)."""

    activated = Signal(object)  # Tile
    forgotten = Signal(object)  # Tile

    def __init__(self, ctx: object, tile: Tile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.tile = tile
        self.setFixedHeight(40)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _close_rect(self) -> QRectF:
        return QRectF(self.width() - 30, (self.height() - 24) / 2.0, 24.0, 24.0)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = self.underMouse()
        if hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.HALO))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), theme.R_ITEM, theme.R_ITEM)
        px = app_pixmap(self.ctx, self.tile.kind, self.tile.target, 20)
        draw_pixmap(p, px, QRectF(10, (self.height() - 20) / 2.0, 20, 20))
        sub_w = 88
        title_w = max(40.0, self.width() - 40 - sub_w - 34)
        p.setPen(QColor(theme.TEXT))
        font = shell_font(12)
        p.setFont(font)
        txt = QFontMetrics(font).elidedText(self.tile.label, Qt.TextElideMode.ElideRight, int(title_w))
        p.drawText(QRectF(40, 0, title_w, self.height()), Qt.AlignmentFlag.AlignVCenter, txt)
        p.setPen(QColor(theme.TEXT_2))
        p.setFont(shell_font(11))
        p.drawText(
            QRectF(self.width() - sub_w - 34, 0, sub_w, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            self.tile.subtitle,
        )
        if hover and self.tile.source == "recent-launch":
            cr = self._close_rect()
            draw_pixmap(p, icons.pixmap("close", theme.TEXT_2, 12), cr.adjusted(6, 6, -6, -6))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        if self.tile.source == "recent-launch" and self._close_rect().contains(pos):
            self.forgotten.emit(self.tile)
            return
        if self.rect().contains(pos.toPoint()):
            self.activated.emit(self.tile)


class ContinueList(QWidget):
    """Lista vertical de itens recentes; oculta antes de deletar para evitar sobreposição."""

    activated = Signal(object)
    forgotten = Signal(object)

    def __init__(self, ctx: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._rows: list[ContinueRow] = []
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(2)
        self.empty_label = QLabel("Os apps e pastas que você abrir aparecem aqui")
        self.empty_label.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;")
        self._box.addWidget(self.empty_label)

    def set_tiles(self, tiles: list[Tile]) -> None:
        for old in self._rows:
            self._box.removeWidget(old)
            old.hide()
            old.deleteLater()
        self._rows.clear()
        limited = tiles[:4]
        self.empty_label.setVisible(not limited)
        for tile in limited:
            row = ContinueRow(self.ctx, tile, self)
            row.activated.connect(self.activated)
            row.forgotten.connect(self.forgotten)
            self._box.addWidget(row)
            self._rows.append(row)


class LetterRail(QWidget):
    """Índice lateral A-Z de AllAppsView."""

    picked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._letters: list[str] = []
        self._hover = -1
        self.setFixedWidth(24)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_letters(self, letters: list[str]) -> None:
        self._letters = list(letters)
        self._hover = -1
        self.update()

    def paintEvent(self, _event: object) -> None:
        if not self._letters:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h_step = self.height() / max(1, len(self._letters))
        p.setFont(shell_font(10, QFont.Weight.DemiBold))
        for i, letter in enumerate(self._letters):
            rect = QRectF(0, i * h_step, float(self.width()), h_step)
            p.setPen(QColor(theme.ACCENT if i == self._hover else theme.TEXT_2))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, letter)

    def _pick_at(self, y: float) -> None:
        if not self._letters:
            return
        idx = max(0, min(len(self._letters) - 1, int(y / max(1.0, self.height() / len(self._letters)))))
        self._hover = idx
        self.update()
        self.picked.emit(self._letters[idx])

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pick_at(event.position().y())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick_at(event.position().y())

    def leaveEvent(self, _event: object) -> None:
        self._hover = -1
        self.update()


class AllAppsView(QWidget):
    """Todos os apps com chips de categoria + lista alfabética + trilho A-Z."""

    activated = Signal(object)
    context = Signal(object, QPoint)
    back_requested = Signal()

    def __init__(self, ctx: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._tiles: list[Tile] = []
        self._selected_cat = "Todos"
        self._chip_buttons: dict[str, QPushButton] = {}

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        back = HoverButton("back", "", "Voltar ao Início", QSize(28, 28), 14, 10)
        back.clicked.connect(self.back_requested)
        top.addWidget(back)

        self.chips_row = QHBoxLayout()
        self.chips_row.setContentsMargins(0, 0, 0, 0)
        self.chips_row.setSpacing(6)
        chips_host = QWidget()
        chips_host.setLayout(self.chips_row)
        chips_scroll = QScrollArea()
        chips_scroll.setWidgetResizable(True)
        chips_scroll.setFixedHeight(34)
        chips_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        chips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        chips_scroll.setWidget(chips_host)
        top.addWidget(chips_scroll, 1)

        self.app_list = AppList(ctx)
        self.app_list.activated.connect(self.activated)
        self.app_list.context.connect(self.context)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.app_list)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.rail = LetterRail(self)
        self.rail.picked.connect(self._jump_to_letter)

        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(4)
        mid.addWidget(self.scroll, 1)
        mid.addWidget(self.rail)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addLayout(top)
        root.addLayout(mid, 1)

    def set_tiles(self, tiles: list[Tile]) -> None:
        self._tiles = list(tiles)
        self._rebuild_chips()
        self._apply_filter()

    def _rebuild_chips(self) -> None:
        for btn in self._chip_buttons.values():
            self.chips_row.removeWidget(btn)
            btn.hide()
            btn.deleteLater()
        self._chip_buttons.clear()
        while self.chips_row.count():
            item = self.chips_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        present = {apps.category(t.label, t.target) for t in self._tiles}
        cats = ["Todos", *[c for c in apps.CATEGORY_ORDER if c in present]]
        if self._selected_cat not in cats:
            self._selected_cat = "Todos"
        for name in cats:
            btn = QPushButton(name)
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, cat=name: self._select_category(cat))
            self._chip_buttons[name] = btn
            self.chips_row.addWidget(btn)
        self.chips_row.addStretch(1)
        self._style_chips()

    def _style_chips(self) -> None:
        for name, btn in self._chip_buttons.items():
            on = name == self._selected_cat
            if on:
                btn.setStyleSheet(
                    f"QPushButton {{ background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};"
                    f" border: 1px solid {theme.ACCENT_LINE}; border-radius: 13px; padding: 0 10px;"
                    " font-size: 11px; font-weight: 600; }"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: {theme.INK_KEY}; color: {theme.TEXT_2};"
                    f" border: 1px solid {theme.HAIRLINE_QSS}; border-radius: 13px; padding: 0 10px;"
                    " font-size: 11px; }"
                    f"QPushButton:hover {{ background: {theme.HALO_QSS}; color: {theme.TEXT}; }}"
                )

    def _select_category(self, category: str) -> None:
        self._selected_cat = category
        self._style_chips()
        self._apply_filter()

    def _apply_filter(self) -> None:
        if self._selected_cat == "Todos":
            filtered = self._tiles
        else:
            filtered = [t for t in self._tiles if apps.category(t.label, t.target) == self._selected_cat]
        self.app_list.set_tiles(filtered)
        self.rail.set_letters(self.app_list.letters())
        self.scroll.verticalScrollBar().setValue(0)

    def _jump_to_letter(self, letter: str) -> None:
        y = self.app_list.y_for_letter(letter)
        if y is not None:
            self.scroll.verticalScrollBar().setValue(y)


class PowerCircleButton(QWidget):
    """Botão redondo de energia com atalho numérico 1..5."""

    clicked = Signal(str)

    def __init__(self, key_num: str, action: str, title: str, icon_name: str) -> None:
        super().__init__()
        self.key_num = key_num
        self.action = action
        self.title = title
        self.icon_name = icon_name
        self.armed = False
        self._pressed = False
        self.setFixedSize(98, 102)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_armed(self, on: bool) -> None:
        if on != self.armed:
            self.armed = on
            self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        circle = QRectF((self.width() - 62) / 2.0, 6.0, 62.0, 62.0)
        if self.armed:
            p.setPen(QPen(QColor(theme.DANGER), 1.5))
            p.setBrush(QColor(251, 113, 133, 45))
        elif self._pressed:
            p.setPen(QPen(QColor(theme.HAIRLINE), 1.0))
            p.setBrush(QColor(theme.HALO_PRESS))
        elif self.underMouse():
            p.setPen(QPen(QColor(theme.HAIRLINE), 1.0))
            p.setBrush(QColor(theme.HALO))
        else:
            p.setPen(QPen(QColor(theme.HAIRLINE), 1.0))
            p.setBrush(QColor(theme.INK_RAISED))
        p.drawEllipse(circle)
        fg = theme.DANGER if self.armed else theme.TEXT
        draw_pixmap(
            p,
            icons.pixmap(self.icon_name, fg, 22),
            QRectF(circle.center().x() - 11, circle.center().y() - 11, 22, 22),
        )
        # Keycap 1..5
        badge = QRectF(circle.right() - 14, circle.bottom() - 14, 18, 18)
        p.setPen(QPen(QColor(theme.HAIRLINE), 1.0))
        p.setBrush(QColor(theme.INK_KEY))
        p.drawRoundedRect(badge, 6.0, 6.0)
        p.setPen(QColor(theme.TEXT_2))
        p.setFont(shell_font(10, QFont.Weight.DemiBold, tabular=True))
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, self.key_num)

        p.setPen(QColor(theme.DANGER if self.armed else theme.TEXT))
        p.setFont(shell_font(12, QFont.Weight.DemiBold if self.armed else QFont.Weight.Normal))
        p.drawText(QRectF(0, 74, float(self.width()), 24), Qt.AlignmentFlag.AlignCenter, self.title)

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
            self.clicked.emit(self.action)


class PowerView(QWidget):
    """Submodo integrado de energia: 5 botões redondos + menu secundário + 2-passos sem cortina."""

    action_triggered = Signal(str)
    back_requested = Signal()

    def __init__(self, ctx: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._armed_key = ""
        self.buttons: dict[str, PowerCircleButton] = {}

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        back = HoverButton("back", "", "Voltar", QSize(28, 28), 14, 10)
        back.clicked.connect(self.back_requested)
        title = QLabel("Energia")
        title.setStyleSheet("font-size: 14px; font-weight: 600;")
        more = HoverButton("chevron-down", "", "Mais opções", QSize(28, 28), 14, 10)
        more.clicked.connect(lambda: self._show_more(more))
        top.addWidget(back)
        top.addSpacing(4)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(more)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        cards.addStretch(1)
        for num, act, label_text, icon_name in POWER_BUTTONS:
            btn = PowerCircleButton(num, act, label_text, icon_name)
            btn.clicked.connect(self.trigger)
            self.buttons[act] = btn
            cards.addWidget(btn)
        cards.addStretch(1)

        self.warn = QLabel("")
        self.warn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.warn.setStyleSheet(f"color: {theme.DANGER}; font-size: 12px;")
        self.warn.hide()

        self.hint = QLabel("Pressione 1–5 ou clique para escolher · Esc volta")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        root.addLayout(top)
        root.addStretch(1)
        root.addLayout(cards)
        root.addWidget(self.warn)
        root.addWidget(self.hint)
        root.addStretch(1)

    def reset_arm(self) -> None:
        self._armed_key = ""
        self.warn.hide()
        for btn in self.buttons.values():
            btn.set_armed(False)

    def trigger(self, action_key: str) -> None:
        action = power.BY_KEY.get(action_key)
        if action is None:
            return
        power_anim = bool(getattr(getattr(self.ctx, "settings", None), "power_animation", True))
        if action.destructive and not power_anim and self._armed_key != action_key:
            self._armed_key = action_key
            for k, btn in self.buttons.items():
                btn.set_armed(k == action_key)
            self.warn.setText(f"⚠ {action.title}: clique de novo ou pressione Enter para confirmar")
            self.warn.show()
            return
        self.reset_arm()
        self.action_triggered.emit(action_key)

    def handle_key(self, event: QKeyEvent) -> bool:
        idx_map = {
            Qt.Key.Key_1: "lock",
            Qt.Key.Key_2: "sleep",
            Qt.Key.Key_3: "restart",
            Qt.Key.Key_4: "shutdown",
            Qt.Key.Key_5: "logoff",
        }
        key = event.key()
        if key in idx_map:
            self.trigger(idx_map[key])
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._armed_key:
            self.trigger(self._armed_key)
            return True
        return False

    def _show_more(self, anchor: QWidget) -> None:
        menu = brand.ink_menu(self)
        pwr = getattr(self.ctx, "power", None)
        pending = bool(getattr(pwr, "pending", False))
        for action in power.ACTIONS:
            if action.key in self.buttons:
                continue
            if action.needs_update and not pending:
                continue
            act = menu.addAction(icons.icon(action.icon, theme.TEXT_2, 15), action.title)
            act.triggered.connect(lambda _c=False, k=action.key: self.action_triggered.emit(k))
        menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height() + 4)))


class PersonButton(HoverButton):
    """Avatar + primeiro nome no StatusLine."""

    def __init__(self) -> None:
        super().__init__("", "", "Sua conta", QSize(180, 30), 16, theme.R_ITEM)
        self.text = "Você"
        self.avatar = Avatar("Você", "", 22, self)
        self.avatar.move(6, 4)
        self.avatar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_person(self, name: str, picture: str) -> None:
        self.text = name.split()[0] if name.strip() else "Você"
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
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), theme.R_ITEM, theme.R_ITEM)
        p.setPen(QColor(theme.TEXT_2))
        p.setFont(shell_font(12))
        p.drawText(self.rect().adjusted(34, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, self.text)


class BadgeButton(HoverButton):
    """Botão de ícone com um selo de número (notificações novas, apps em segundo plano)."""

    def __init__(self, icon: str, tip: str, accent: bool = True) -> None:
        super().__init__(icon, "", tip, QSize(30, 30), 15, theme.R_ITEM)
        self.count = 0
        self.accent = accent
        self.base_tip = tip

    def set_count(self, count: int) -> None:
        self.count = max(0, int(count))
        self.setToolTip(f"{self.base_tip} · {self.count}" if self.count else self.base_tip)
        self.update()

    def paintEvent(self, event: object) -> None:
        super().paintEvent(event)
        if not self.count:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = str(self.count) if self.count < 10 else "9+"
        p.setFont(shell_font(8, QFont.Weight.Bold))
        w = max(13.0, p.fontMetrics().horizontalAdvance(text) + 6.0)
        rect = QRectF(self.width() - w - 1, 1, w, 13)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT if self.accent else theme.INK_KEY))
        p.drawRoundedRect(rect, 6.5, 6.5)
        p.setPen(QColor(theme.ACCENT_TEXT if self.accent else theme.TEXT_2))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


class StatusLine(QWidget):
    """Linha inferior: conta à esquerda; segundo plano, notificações, Controles, Ajustes e Energia."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(34)
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 6, 0)
        row.setSpacing(6)
        self.person = PersonButton()
        self.background_btn = BadgeButton("apps", "Em segundo plano", accent=False)
        self.notif_btn = BadgeButton("bell", "Notificações")
        small = QSize(30, 30)
        controls_tip = "Controles: cafeína, foco, conta-gotas, texto da tela (Win + A)"
        self.controls_btn = HoverButton("sliders", "", controls_tip, small, 15, theme.R_ITEM)
        self.settings_btn = HoverButton("settings", "", "Ajustes do Ventude", QSize(30, 30), 15, theme.R_ITEM)
        self.power_btn = HoverButton("power", "", "Energia (Ctrl+P)", QSize(30, 30), 15, theme.R_ITEM)
        row.addWidget(self.person)
        row.addStretch(1)
        row.addWidget(self.background_btn)
        row.addWidget(self.notif_btn)
        row.addSpacing(6)
        row.addWidget(self.controls_btn)
        row.addWidget(self.settings_btn)
        row.addWidget(self.power_btn)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setPen(QPen(QColor(theme.HAIRLINE), 1.0))
        p.drawLine(0, 0, self.width(), 0)


class LauncherWindow(ShellWindow):
    """Janela única de Lançador: HomeView, SearchResults, AllAppsView e PowerView."""

    def __init__(self, ctx: object, shell: object) -> None:
        super().__init__(ctx, radius=theme.R_SHEET, tool=True, focus=True, bg=theme.INK)
        self.shell = shell
        self.setWindowTitle("Ventude: Lançador")
        self._shadow = brand.ShadowCache()
        self.opened_at = 0.0
        self._last_closed_at = 0.0
        self._anchor_screen_rect = QRect()
        self._recent_files: list[apps.RecentFile] = []
        self._loading_recent = False
        self._expanded_pins = False
        self._active_height = False
        self._reveal_target = QPoint()
        self._reveal_offset = 18

        self._reveal = motion.Tween(self)
        self._reveal.progressed.connect(self._on_reveal_step)
        self._reveal.done.connect(self._on_reveal_done)

        # Conteúdo interno com margem da sombra
        self._content = QWidget(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW_PAD, SHADOW_PAD, SHADOW_PAD, SHADOW_PAD)
        outer.setSpacing(0)
        outer.addWidget(self._content)

        # Campo superior
        self.field = LauncherField(self._content)
        self.input = self.field.input
        self.input.textChanged.connect(self._typed)
        self.input.installEventFilter(self)
        self.field.all_apps_clicked.connect(self.toggle_all_apps)

        # HomeView (Fixados 8 colunas + Continuar + dicas)
        self.home_view = QWidget()
        self.pinned = TileGrid(ctx, "tile", 8, QSize(78, 84), 30, drag_enabled=True)
        self.pinned.empty_text = "Use + ou botão direito em um app para fixar aqui"
        self.pinned.activated.connect(self._activate_tile)
        self.pinned.context.connect(self._tile_menu)

        self.add_pin_btn = HoverButton("plus", "", "Fixar app ou pasta", QSize(26, 26), 13, 10)
        self.add_pin_btn.clicked.connect(self._pick_pin)
        self.more_pins_btn = HoverButton("chevron-down", "", "Mostrar mais fixados", QSize(26, 26), 13, 10)
        self.more_pins_btn.clicked.connect(self._toggle_more_pins)

        pinned_hdr = QHBoxLayout()
        pinned_hdr.setContentsMargins(2, 0, 2, 0)
        pinned_lbl = QLabel("fixados")
        pinned_lbl.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px; font-weight: 600;")
        pinned_hdr.addWidget(pinned_lbl)
        pinned_hdr.addStretch(1)
        pinned_hdr.addWidget(self.more_pins_btn)
        pinned_hdr.addWidget(self.add_pin_btn)

        cont_hdr = QHBoxLayout()
        cont_hdr.setContentsMargins(2, 4, 2, 0)
        cont_lbl = QLabel("continuar")
        cont_lbl.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px; font-weight: 600;")
        cont_hdr.addWidget(cont_lbl)
        cont_hdr.addStretch(1)

        self.continue_list = ContinueList(ctx)
        self.continue_list.activated.connect(self._activate_tile)
        self.continue_list.forgotten.connect(self._forget_recent)

        self.tip_bar = QWidget()
        tip_row = QHBoxLayout(self.tip_bar)
        tip_row.setContentsMargins(10, 4, 6, 4)
        tip_lbl = QLabel("dica: img:, video:, musica:, doc:, pasta: filtram · >, @, /, =, ?, ! trocam o modo")
        tip_lbl.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px;")
        tip_close = HoverButton("close", "", "Dispensar dica", QSize(20, 20), 11, 10)
        tip_close.clicked.connect(self._dismiss_tips)
        tip_row.addWidget(tip_lbl, 1)
        tip_row.addWidget(tip_close)
        self.tip_bar.setStyleSheet(
            f"background: {theme.INK_RAISED}; border: 1px solid {theme.HAIRLINE_QSS}; border-radius: 10px;"
        )

        home_col = QVBoxLayout(self.home_view)
        home_col.setContentsMargins(0, 0, 0, 0)
        home_col.setSpacing(6)
        home_col.addLayout(pinned_hdr)
        home_col.addWidget(self.pinned)
        home_col.addLayout(cont_hdr)
        home_col.addWidget(self.continue_list)
        home_col.addStretch(1)
        home_col.addWidget(self.tip_bar)

        # Resultados, Todos os apps e Energia
        self.results = SearchResults(ctx, shell)
        self.results.finished.connect(self.dismiss)

        self.all_apps = AllAppsView(ctx)
        self.all_apps.activated.connect(self._activate_tile)
        self.all_apps.context.connect(self._tile_menu)
        self.all_apps.back_requested.connect(self.show_home)

        self.power_view = PowerView(ctx)
        self.power_view.action_triggered.connect(self._run_power)
        self.power_view.back_requested.connect(self.show_home)

        self.background_view = BackgroundView(ctx, shell)
        self.background_view.back_requested.connect(self.show_home)
        self.background_view.finished.connect(self.dismiss)

        self.notifications_view = NotificationsView(ctx)
        self.notifications_view.back_requested.connect(self.show_home)
        self.notifications_view.finished.connect(self.dismiss)

        self.pages = QStackedWidget()
        self.pages.addWidget(self.home_view)
        self.pages.addWidget(self.results)
        self.pages.addWidget(self.all_apps)
        self.pages.addWidget(self.power_view)
        self.pages.addWidget(self.background_view)
        self.pages.addWidget(self.notifications_view)
        repaint = getattr(shell, "_repaint_docks", None)
        if callable(repaint):
            self.pages.currentChanged.connect(lambda _i: repaint())  # realce dos botões da Doca

        # Rodapé
        self.status = StatusLine()
        self.status.person.clicked.connect(lambda: shell.account_menu(self.status.person))  # type: ignore[attr-defined]
        self.status.settings_btn.clicked.connect(
            lambda: (self.dismiss(), shell.open_settings())  # type: ignore[attr-defined]
        )
        self.status.power_btn.clicked.connect(self.toggle_power)
        self.status.background_btn.clicked.connect(self.toggle_background)
        self.status.notif_btn.clicked.connect(self.toggle_notifications)
        self.status.controls_btn.clicked.connect(self._open_controls)
        self.background_view.count_changed.connect(self.status.background_btn.set_count)
        center = getattr(ctx, "notifications", None)
        if center is not None:
            center.changed.connect(self._sync_badges)
        self._sync_badges()

        body = QVBoxLayout(self._content)
        body.setContentsMargins(20, 18, 20, 10)
        body.setSpacing(12)
        body.addWidget(self.field)
        body.addWidget(self.pages, 1)
        body.addWidget(self.status)

        indexer = getattr(ctx, "indexer", None)
        if indexer is not None:
            indexer.apps_ready.connect(lambda _n: self.refresh_apps())
            indexer.ready.connect(lambda _n: self.refresh_apps())

    def panel_rect(self) -> QRectF:
        return QRectF(
            SHADOW_PAD,
            SHADOW_PAD,
            max(1, self.width() - SHADOW_PAD * 2),
            max(1, self.height() - SHADOW_PAD * 2),
        )

    def paint_background(self, p: QPainter) -> None:
        rect = self.panel_rect()
        self._shadow.paint(p, rect, theme.R_SHEET)
        brand.paint_ink(p, rect, theme.R_SHEET, bg=self.bg, alpha=self.alpha)

    def target_geometry(self, screen_rect: QRect, *, active: bool) -> QRect:
        """Retângulo do Lançador, sempre com a borda de baixo acima da Doca.

        A altura considera o mínimo que o conteúdo pede: com duas linhas de fixados, quatro itens em
        "continuar" e a dica, ele passa da altura padrão, e o Qt esticaria a janela para baixo, por cima
        da Doca. Por isso o cálculo parte da altura real e ancora pela borda de baixo.
        """
        body_h = (HEIGHT_ACTIVE if active else HEIGHT_EMPTY) + SHADOW_PAD * 2
        dock_clear = 80
        top_clear = 56
        w = min(LAUNCHER_WIDTH + SHADOW_PAD * 2, max(360, screen_rect.width() - 16))
        max_h = max(320, screen_rect.height() - top_clear - dock_clear)
        layout = self.layout()
        need = 0
        if layout is not None:
            layout.activate()
            need = layout.totalMinimumSize().height()
        h = max(min(body_h, max_h), need)
        x = screen_rect.left() + (screen_rect.width() - w) // 2
        bottom_y = screen_rect.bottom() - dock_clear - h + 1
        top_y = screen_rect.top() + top_clear
        y = max(top_y, bottom_y) if bottom_y >= top_y else max(screen_rect.top() + 4, bottom_y)
        return QRect(x, y, w, h)

    def refit(self) -> None:
        """Reajusta depois que o conteúdo muda com o Lançador aberto (fixados e recentes chegam depois)."""
        if self.isVisible() and not self._closing:
            self._sync_height(self._active_height)

    def resizeEvent(self, event: object) -> None:  # noqa: N802
        super().resizeEvent(event)
        # O Qt estica a janela para baixo quando o conteúdo cresce depois de aberta; reancora pela base.
        if self.isVisible() and not self._closing:
            QTimer.singleShot(0, self.refit)

    def _keep_on_top(self) -> None:
        app = QApplication.instance()
        if app is not None and app.platformName().lower() == "offscreen":
            return
        winutil.keep_topmost(int(self.winId()))

    def _sync_height(self, active: bool) -> None:
        self._active_height = active
        if self._anchor_screen_rect.isNull():
            screen = QApplication.primaryScreen()
            self._anchor_screen_rect = screen.geometry() if screen else QRect(0, 0, 1920, 1080)
        rect = self.target_geometry(self._anchor_screen_rect, active=active)
        if self.size() != rect.size() or self.geometry() != rect:
            self._reveal_target = rect.topLeft()
            self.setGeometry(rect)

    # ---------- abrir & fechar ----------
    def open_launcher(self, screen_rect: QRect, text: str = "", page: str = "") -> None:
        self._anchor_screen_rect = screen_rect
        self._closing = False
        self._reveal.stop()
        self._tween.stop()
        self._expanded_pins = False
        self.power_view.reset_arm()

        fast = (time.monotonic() - self._last_closed_at) <= REOPEN_FAST_S
        dur = motion.ms("gota_fast" if fast else "gota", getattr(self.ctx, "settings", None))

        if text:
            self.input.setText(text)
            self.results.set_query(text)
            self.pages.setCurrentWidget(self.results)
            rect = self.target_geometry(screen_rect, active=True)
        else:
            self.input.clear()
            self.pages.setCurrentWidget(self.home_view)
            rect = self.target_geometry(screen_rect, active=False)

        self.refresh()
        if page and not text:
            self._open_page(page)
            rect = self.target_geometry(screen_rect, active=self._active_height)
        self._reveal_target = rect.topLeft()
        self.opened_at = time.monotonic()

        if dur <= 0:
            self.setGeometry(rect)
            self.setWindowOpacity(1.0)
            self.show()
            self.shown.emit()
            self.raise_()
            self._keep_on_top()
            self.activateWindow()
            self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
            return

        self.setGeometry(rect)
        self.move(rect.x(), rect.y() + self._reveal_offset)
        self.setWindowOpacity(0.0)
        self.show()
        self.shown.emit()
        self.raise_()
        self._keep_on_top()
        self.activateWindow()
        # Foco no frame 0
        self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._reveal.play(dur, 0.0, 1.0, motion.named("enfase"))

    def _on_reveal_step(self, t: float) -> None:
        self.setWindowOpacity(min(1.0, t * 1.15))
        self.move(
            self._reveal_target.x(),
            self._reveal_target.y() + round(self._reveal_offset * (1.0 - t)),
        )

    def _on_reveal_done(self) -> None:
        self.move(self._reveal_target)
        self.setWindowOpacity(1.0)

    def dismiss(self) -> None:
        if not self.isVisible() or self._closing:
            return
        self._reveal.stop()
        self._last_closed_at = time.monotonic()
        self._closing = True
        settings = getattr(self.ctx, "settings", None)
        dur = motion.ms("recolher", settings)
        if dur <= 0:
            self._finish_close()
            return
        self._target_pos = self.pos()
        self._offset = QPoint(0, 10)
        self._tween.play(dur, 1.0, 0.0, motion.named("saida"))

    # ---------- modos ----------
    def show_home(self) -> None:
        self.power_view.reset_arm()
        self.pages.setCurrentWidget(self.home_view)
        self._sync_height(active=False)
        self.input.setFocus()

    def toggle_all_apps(self) -> None:
        if self.pages.currentWidget() is self.all_apps:
            self.show_home()
        else:
            self.show_all_apps()

    def show_all_apps(self) -> None:
        self.refresh_apps()
        self.pages.setCurrentWidget(self.all_apps)
        self._sync_height(active=True)

    def toggle_power(self) -> None:
        if self.pages.currentWidget() is self.power_view:
            self.show_home()
        else:
            self.show_power()

    def show_power(self) -> None:
        self.power_view.reset_arm()
        self.pages.setCurrentWidget(self.power_view)
        self._sync_height(active=False)
        self.power_view.setFocus()

    def _open_page(self, page: str) -> None:
        if page == "background":
            self.show_background()
        elif page == "notifications":
            self.show_notifications()
        elif page == "power":
            self.show_power()
        elif page == "apps":
            self.show_all_apps()

    def current_page(self) -> str:
        current = self.pages.currentWidget()
        names = {
            self.home_view: "home",
            self.results: "search",
            self.all_apps: "apps",
            self.power_view: "power",
            self.background_view: "background",
            self.notifications_view: "notifications",
        }
        return names.get(current, "home")

    def toggle_background(self) -> None:
        if self.pages.currentWidget() is self.background_view:
            self.show_home()
        else:
            self.show_background()

    def show_background(self) -> None:
        self.background_view.refresh()
        self.pages.setCurrentWidget(self.background_view)
        self._sync_height(active=False)
        self.background_view.setFocus()

    def toggle_notifications(self) -> None:
        if self.pages.currentWidget() is self.notifications_view:
            self.show_home()
        else:
            self.show_notifications()

    def show_notifications(self) -> None:
        self.notifications_view.refresh()
        self.pages.setCurrentWidget(self.notifications_view)
        self._sync_height(active=True)
        self.notifications_view.setFocus()
        self.notifications_view.mark_read()  # viu: o selo some (os cartões mantêm o risquinho até reabrir)

    def _sync_badges(self) -> None:
        center = getattr(self.ctx, "notifications", None)
        self.status.notif_btn.set_count(center.unread if center is not None else 0)

    def _open_controls(self) -> None:
        self.dismiss()
        opener = getattr(self.shell, "open_controls", None)
        if callable(opener):
            QTimer.singleShot(120, opener)

    def set_person(self, name: str, picture: str) -> None:
        self.status.person.set_person(name, picture)

    def set_power_badge(self, pending: bool) -> None:
        self.status.power_btn.set_icon(
            "power-badge" if pending else "power",
            theme.WARN if pending else theme.TEXT,
        )
        self.status.power_btn.setToolTip(
            "Energia · atualização do Windows esperando" if pending else "Energia (Ctrl+P)"
        )

    # ---------- dados ----------
    def refresh(self) -> None:
        self.refresh_pins()
        self.refresh_recent()
        self.refresh_apps()
        store = getattr(self.ctx, "store", None)
        dismissed = bool(store and store.meta("launcher_tips_dismissed") == "1")
        self.tip_bar.setVisible(not dismissed)

    def refresh_pins(self) -> None:
        store = getattr(self.ctx, "store", None)
        pins = store.pins("start") if store is not None else []
        tiles = [Tile(p.label, p.kind, p.target, pin_id=p.id, source="pinned") for p in pins]
        self.pinned.limit = 0 if self._expanded_pins else 16
        self.pinned.set_tiles(tiles)
        self.more_pins_btn.setVisible(len(tiles) > 16)
        self.more_pins_btn.set_icon("chevron-up" if self._expanded_pins else "chevron-down", theme.TEXT_2)
        self.refit()

    def _toggle_more_pins(self) -> None:
        self._expanded_pins = not self._expanded_pins
        self.refresh_pins()
        self._sync_height(active=self._expanded_pins)

    def refresh_recent(self) -> None:
        self._apply_recent()
        if not self._loading_recent:
            self._loading_recent = True
            self.ctx.run(  # type: ignore[attr-defined]
                apps.recent_files,
                8,
                done=self._recent_loaded,
                fail=lambda _e: self._recent_loaded([]),
                owner=self,
            )

    def _recent_loaded(self, files: list[apps.RecentFile]) -> None:
        self._loading_recent = False
        self._recent_files = files
        self._apply_recent()

    def _apply_recent(self) -> None:
        now = time.time()
        tiles: list[Tile] = []
        seen: set[str] = set()
        store = getattr(self.ctx, "store", None)
        if store is not None:
            for launch in store.launches(8):
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
            tiles.append(
                Tile(
                    item.name,
                    "folder" if item.is_dir else "file",
                    item.path,
                    apps.relative_time(item.ts, now),
                    ts=item.ts,
                    source="recent-file",
                )
            )
        tiles.sort(key=lambda t: t.ts, reverse=True)
        self.continue_list.set_tiles(tiles[:4])
        self.refit()

    def _forget_recent(self, tile: Tile) -> None:
        store = getattr(self.ctx, "store", None)
        if store is not None:
            store.forget_launch(tile.target)
        self._apply_recent()

    def refresh_apps(self) -> None:
        indexer = getattr(self.ctx, "indexer", None)
        app_entries = indexer.apps if indexer is not None else []
        tiles = [Tile(e.name, e.kind, e.path, source="app") for e in app_entries]
        self.all_apps.set_tiles(tiles)

    # ---------- ações ----------
    def _typed(self, text: str) -> None:
        if text.strip():
            self.results.set_query(text)
            self.pages.setCurrentWidget(self.results)
            self._sync_height(active=True)
        elif self.pages.currentWidget() is self.results:
            self.show_home()

    def _activate_tile(self, tile: Tile) -> None:
        if hasattr(self.shell, "launch"):
            self.shell.launch(tile.kind, tile.target, tile.label)  # type: ignore[attr-defined]
        else:
            from ventude.services import launcher

            launcher.open_target(tile.kind, tile.target)
            self.dismiss()

    def _tile_menu(self, tile: Tile, pos: QPoint) -> None:
        menu = brand.ink_menu(self)
        menu.addAction(icons.icon("external", theme.TEXT, 15), "Abrir", lambda: self._activate_tile(tile))
        if tile.kind == "app" and tile.target.lower().endswith((".exe", ".lnk")):
            menu.addAction(
                icons.icon("admin", theme.TEXT_2, 15),
                "Executar como administrador",
                lambda: apps.run_as_admin(tile.target),
            )
        store = getattr(self.ctx, "store", None)
        if store is not None and tile.target:
            shell_find = getattr(self.shell, "find_pin", None)
            dock_on = (
                shell_find("taskbar", tile.kind, tile.label, tile.target)
                if callable(shell_find)
                else find_matching_pin(store.pins("taskbar"), tile.kind, tile.label, tile.target)
            )
            menu.addAction(
                icons.icon("pin", theme.ACCENT if dock_on else theme.TEXT_2, 15),
                "Desafixar da doca" if dock_on else "Fixar na doca",
                lambda: self._toggle_pin("taskbar", tile),
            )
            start_on = (
                shell_find("start", tile.kind, tile.label, tile.target)
                if callable(shell_find)
                else find_matching_pin(store.pins("start"), tile.kind, tile.label, tile.target)
            )
            menu.addAction(
                icons.icon("pin", theme.ACCENT if start_on else theme.TEXT_2, 15),
                "Desafixar do Lançador" if start_on else "Fixar no Lançador",
                lambda: self._toggle_pin("start", tile),
            )
            if tile.pin_id > 0:
                menu.addAction(
                    icons.icon("arrow-up", theme.TEXT_2, 15),
                    "Mover para o início",
                    lambda: (store.move_pin_first(tile.pin_id), self.refresh_pins()),
                )
                menu.addAction(
                    icons.icon("edit", theme.TEXT_2, 15),
                    "Renomear…",
                    lambda: self._rename_pin(tile),
                )
        if tile.kind in ("app", "folder", "file") and Path(tile.target).exists():
            menu.addAction(
                icons.icon("folder-open", theme.TEXT_2, 15),
                "Abrir local do arquivo",
                lambda: apps.open_location(tile.target),
            )
        if tile.kind in ("app", "uwp"):
            menu.addAction(
                icons.icon("trash", theme.TEXT_2, 15),
                "Desinstalar…",
                lambda: apps.open_system("ms-settings:appsfeatures"),
            )
        menu.exec(pos)

    def _toggle_pin(self, place: str, tile: Tile) -> None:
        if hasattr(self.shell, "toggle_pin"):
            self.shell.toggle_pin(place, tile.kind, tile.label, tile.target)  # type: ignore[attr-defined]
        self.refresh_pins()

    def _rename_pin(self, tile: Tile) -> None:
        name, ok = QInputDialog.getText(self, "Renomear atalho", "Novo nome:", text=tile.label)
        clean = name.strip()
        if ok and clean and tile.pin_id > 0:
            self.ctx.store.rename_pin(tile.pin_id, clean)  # type: ignore[attr-defined]
            self.refresh_pins()

    def _pick_pin(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Fixar aplicativo ou arquivo",
            str(Path.home()),
            "Programas e arquivos (*.exe *.lnk *);;Todos (*.*)",
        )
        if path:
            p = Path(path)
            kind = "folder" if p.is_dir() else ("app" if p.suffix.lower() in (".exe", ".lnk") else "file")
            label_text = p.stem or p.name
            shell_find = getattr(self.shell, "find_pin", None)
            existing = (
                shell_find("start", kind, label_text, str(p))
                if callable(shell_find)
                else find_matching_pin(self.ctx.store.pins("start"), kind, label_text, str(p))  # type: ignore[attr-defined]
            )
            if existing is None:
                self.ctx.store.add_pin("start", kind, label_text, str(p), first=False)  # type: ignore[attr-defined]
                self.ctx.data_changed.emit("pins")  # type: ignore[attr-defined]
            self.refresh_pins()

    def _dismiss_tips(self) -> None:
        store = getattr(self.ctx, "store", None)
        if store is not None:
            store.set_meta("launcher_tips_dismissed", "1")
        self.tip_bar.hide()

    def _run_power(self, action_key: str) -> None:
        self.dismiss()
        if hasattr(self.shell, "power_action"):
            self.shell.power_action(action_key)  # type: ignore[attr-defined]
        else:
            self.ctx.request_power(action_key)  # type: ignore[attr-defined]

    # ---------- teclado e foco ----------
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.input and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            if key == Qt.Key.Key_Escape:
                if self.input.text():
                    self.input.clear()
                else:
                    self.dismiss()
                return True
            if key == Qt.Key.Key_P and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self.toggle_power()
                return True
            if self.pages.currentWidget() is self.results and self.results.handle_key(event):
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_P and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.toggle_power()
            return
        if key == Qt.Key.Key_Escape:
            if self.input.text():
                self.input.clear()
            elif self.pages.currentWidget() in (
                self.all_apps,
                self.power_view,
                self.background_view,
                self.notifications_view,
            ):
                self.show_home()
            else:
                self.dismiss()
            return
        if self.pages.currentWidget() is self.power_view and self.power_view.handle_key(event):
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
        if (
            self.isVisible()
            and not self.isActiveWindow()
            and not self._closing
            and QApplication.activePopupWidget() is None
            and QApplication.activeModalWidget() is None
        ):
            self.dismiss()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.pages.currentWidget() not in (self.power_view, self.background_view, self.notifications_view):
            self.input.setFocus()
        super().mousePressEvent(event)


__all__ = [
    "AllAppsView",
    "ContinueList",
    "HEIGHT_ACTIVE",
    "HEIGHT_EMPTY",
    "LAUNCHER_WIDTH",
    "LauncherField",
    "LauncherWindow",
    "LetterRail",
    "PowerView",
    "StatusLine",
]
