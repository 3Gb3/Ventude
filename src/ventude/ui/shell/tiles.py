"""Grades desenhadas à mão do Menu Iniciar: blocos (ícone em cima, nome embaixo), linhas (ícone ao lado,
duas linhas de texto) e cartões de categoria (quatro ícones). Entram em cascata quando o Iniciar abre."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QEasingCurve, QMimeData, QPoint, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QMouseEvent, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from ventude.ui import theme
from ventude.ui.shell.common import app_pixmap, draw_pixmap, shell_font


@dataclass
class Tile:
    label: str
    kind: str  # app | uwp | folder | file | url | system | category
    target: str
    subtitle: str = ""
    pin_id: int = 0
    ts: float = 0.0
    source: str = ""  # de onde veio (pinned | recent-launch | recent-file | app)
    children: list[Tile] = field(default_factory=list)  # categoria: os apps dela

    @property
    def icon_kind(self) -> str:
        return self.kind


def two_lines(text: str, metrics: QFontMetrics, width: int) -> list[str]:
    """Quebra o nome em até duas linhas (a segunda com reticências), como no Iniciar do Windows."""
    if metrics.horizontalAdvance(text) <= width:
        return [text]
    words = text.split()
    first = ""
    for index, word in enumerate(words):
        trial = f"{first} {word}".strip()
        if metrics.horizontalAdvance(trial) > width:
            if not first:  # palavra única maior que a linha
                return [metrics.elidedText(text, Qt.TextElideMode.ElideRight, width)]
            rest = " ".join(words[index:])
            return [first, metrics.elidedText(rest, Qt.TextElideMode.ElideRight, width)]
        first = trial
    return [first]


class TileGrid(QWidget):
    """Grade de blocos clicáveis. `mode`: tile | row | category."""

    activated = Signal(object)  # Tile
    context = Signal(object, QPoint)  # Tile, posição global

    def __init__(
        self,
        ctx: object,
        mode: str = "tile",
        columns: int = 6,
        cell: QSize = QSize(96, 88),
        icon: int = 32,
        parent: QWidget | None = None,
        *,
        drag_enabled: bool = False,
    ) -> None:
        super().__init__(parent)
        self.ctx, self.mode, self.columns, self.cell, self.icon = ctx, mode, columns, cell, icon
        self.drag_enabled = drag_enabled
        self.tiles: list[Tile] = []
        self.hover = -1
        self.pressed = -1
        self._drag_start: QPoint | None = None
        self._drag_tile: Tile | None = None
        self.limit = 0  # 0 = todos
        self.reveal = 1.0
        self.empty_text = ""
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.anim = QVariantAnimation(self)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._set_reveal)
        cache = getattr(ctx, "icons", None)
        if cache is not None:
            cache.changed.connect(lambda _name: self.update())

    def set_tiles(self, tiles: list[Tile]) -> None:
        self.tiles = list(tiles)
        self.hover = -1
        self._resize()
        self.update()

    def visible_tiles(self) -> list[Tile]:
        return self.tiles[: self.limit] if self.limit else self.tiles

    def _resize(self) -> None:
        count = len(self.visible_tiles())
        rows = math.ceil(count / max(1, self.columns)) if count else (1 if self.empty_text else 0)
        self.setFixedHeight(rows * self.cell.height())

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.columns * self.cell.width(), self.height())

    def cell_width(self) -> float:
        return max(1.0, self.width() / max(1, self.columns))

    def rect_for(self, index: int) -> QRectF:
        row, col = divmod(index, max(1, self.columns))
        w = self.cell_width()
        return QRectF(col * w, row * self.cell.height(), w, self.cell.height()).adjusted(2, 2, -2, -2)

    def index_at(self, pos: QPoint) -> int:
        for index in range(len(self.visible_tiles())):
            if self.rect_for(index).contains(pos.toPointF() if hasattr(pos, "toPointF") else pos):
                return index
        return -1

    # ---------- entrada em cascata ----------
    def play_reveal(self, duration: int = 420) -> None:
        if duration <= 0:
            self.reveal = 1.0
            self.update()
            return
        self.anim.stop()
        self.anim.setDuration(duration)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()

    def _set_reveal(self, value: object) -> None:
        self.reveal = float(value)  # type: ignore[arg-type]
        self.update()

    def _tile_alpha(self, index: int) -> float:
        if self.reveal >= 1.0:
            return 1.0
        count = max(1, len(self.visible_tiles()))
        start = min(index, 18) / max(count, 18) * 0.55
        return max(0.0, min(1.0, (self.reveal - start) / 0.45))

    # ---------- desenho ----------
    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tiles = self.visible_tiles()
        if not tiles and self.empty_text:
            p.setPen(QColor(theme.MUTED))
            p.setFont(shell_font(12))
            p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            return
        for index, tile in enumerate(tiles):
            alpha = self._tile_alpha(index)
            if alpha <= 0:
                continue
            rect = self.rect_for(index)
            p.setOpacity(alpha)
            offset = (1 - alpha) * 10
            rect = rect.translated(0, offset)
            if index == self.pressed:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.HALO_PRESS))
                p.drawRoundedRect(rect, theme.R_ITEM, theme.R_ITEM)
            elif index == self.hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.HALO))
                p.drawRoundedRect(rect, theme.R_ITEM, theme.R_ITEM)
            if self.mode == "category":
                self._paint_category(p, rect, tile)
            elif self.mode == "row":
                self._paint_row(p, rect, tile)
            else:
                self._paint_tile(p, rect, tile)
        p.setOpacity(1.0)

    def _icon(self, tile: Tile, size: int):  # noqa: ANN202
        return app_pixmap(self.ctx, tile.kind, tile.target, size)

    def _paint_tile(self, p: QPainter, rect: QRectF, tile: Tile) -> None:
        size = self.icon
        draw_pixmap(
            p, self._icon(tile, size), QRectF(rect.center().x() - size / 2, rect.top() + 12, size, size)
        )
        p.setPen(QColor(theme.TEXT))
        font = shell_font(12)
        p.setFont(font)
        metrics = QFontMetrics(font)
        lines = two_lines(tile.label, metrics, int(rect.width() - 8))
        y = rect.top() + 12 + size + 8
        for line in lines:
            p.drawText(QRectF(rect.left() + 4, y, rect.width() - 8, 16), Qt.AlignmentFlag.AlignHCenter, line)
            y += 15

    def _paint_row(self, p: QPainter, rect: QRectF, tile: Tile) -> None:
        size = self.icon
        draw_pixmap(
            p, self._icon(tile, size), QRectF(rect.left() + 10, rect.center().y() - size / 2, size, size)
        )
        x = rect.left() + 10 + size + 12
        width = rect.right() - x - 8
        font = shell_font(13)
        p.setFont(font)
        p.setPen(QColor(theme.TEXT))
        name = QFontMetrics(font).elidedText(tile.label, Qt.TextElideMode.ElideRight, int(width))
        if tile.subtitle:
            p.drawText(QRectF(x, rect.center().y() - 18, width, 18), Qt.AlignmentFlag.AlignBottom, name)
            sub_font = shell_font(12)
            p.setFont(sub_font)
            p.setPen(QColor(theme.TEXT_2))
            sub = QFontMetrics(sub_font).elidedText(tile.subtitle, Qt.TextElideMode.ElideRight, int(width))
            p.drawText(QRectF(x, rect.center().y() + 1, width, 17), Qt.AlignmentFlag.AlignTop, sub)
        else:
            p.drawText(QRectF(x, rect.top(), width, rect.height()), Qt.AlignmentFlag.AlignVCenter, name)

    def _paint_category(self, p: QPainter, rect: QRectF, tile: Tile) -> None:
        card = QRectF(rect.left() + 6, rect.top() + 4, rect.width() - 12, rect.height() - 34)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 12))
        p.drawRoundedRect(card, 12, 12)
        size = 30
        gap = 12
        block = size * 2 + gap
        x0, y0 = card.center().x() - block / 2, card.center().y() - block / 2
        for index, child in enumerate(tile.children[:4]):
            row, col = divmod(index, 2)
            draw_pixmap(
                p,
                self._icon(child, size),
                QRectF(x0 + col * (size + gap), y0 + row * (size + gap), size, size),
            )
        p.setPen(QColor(theme.TEXT))
        font = shell_font(12)
        p.setFont(font)
        label = QFontMetrics(font).elidedText(tile.label, Qt.TextElideMode.ElideRight, int(rect.width() - 8))
        p.drawText(
            QRectF(rect.left(), card.bottom() + 6, rect.width(), 18), Qt.AlignmentFlag.AlignHCenter, label
        )

    # ---------- mouse ----------
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        if (
            self.drag_enabled
            and self._drag_start is not None
            and self._drag_tile is not None
            and (event.buttons() & Qt.MouseButton.LeftButton)
            and (pos - self._drag_start).manhattanLength() >= 8
        ):
            tile = self._drag_tile
            self._drag_start = None
            self._drag_tile = None
            self.pressed = -1
            self.update()
            if tile.kind in ("app", "uwp", "folder", "file", "system", "url", "project") and tile.target:
                drag = QDrag(self)
                mime = QMimeData()
                payload = json.dumps({"kind": tile.kind, "label": tile.label, "target": tile.target})
                mime.setData("application/x-ventude-item", payload.encode("utf-8"))
                drag.setMimeData(mime)
                drag.setPixmap(self._icon(tile, 32))
                drag.exec(Qt.DropAction.CopyAction)
                return
        index = self.index_at(pos)
        if index != self.hover:
            self.hover = index
            tiles = self.visible_tiles()
            self.setToolTip(tiles[index].label if 0 <= index < len(tiles) else "")
            self.update()

    def leaveEvent(self, _event: object) -> None:
        self.hover = self.pressed = -1
        self._drag_start = None
        self._drag_tile = None
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            self.pressed = self.index_at(pos)
            tiles = self.visible_tiles()
            if self.drag_enabled and 0 <= self.pressed < len(tiles):
                self._drag_start = pos
                self._drag_tile = tiles[self.pressed]
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_start = None
        self._drag_tile = None
        index = self.index_at(event.position().toPoint())
        tiles = self.visible_tiles()
        pressed, self.pressed = self.pressed, -1
        self.update()
        if not 0 <= index < len(tiles):
            return
        if event.button() == Qt.MouseButton.LeftButton and index == pressed:
            self.activated.emit(tiles[index])
        elif event.button() == Qt.MouseButton.RightButton:
            self.context.emit(tiles[index], event.globalPosition().toPoint())


class AppList(QWidget):
    """Todos os apps em lista alfabética, com a letra de cada grupo (Exibição: Lista)."""

    activated = Signal(object)
    context = Signal(object, QPoint)
    ROW, HEADER = 40, 30

    def __init__(self, ctx: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.rows: list[tuple[str, object]] = []  # ("header", "A") | ("tile", Tile)
        self.hover = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        cache = getattr(ctx, "icons", None)
        if cache is not None:
            cache.changed.connect(lambda _name: self.update())

    def set_tiles(self, tiles: list[Tile]) -> None:
        rows: list[tuple[str, object]] = []
        letter = ""
        for tile in sorted(tiles, key=lambda t: t.label.lower()):
            first = tile.label[:1].upper()
            first = first if first.isalpha() else "#"
            if first != letter:
                letter = first
                rows.append(("header", letter))
            rows.append(("tile", tile))
        self.rows = rows
        self.setFixedHeight(sum(self.HEADER if kind == "header" else self.ROW for kind, _ in rows))
        self.update()

    def _geometry(self) -> list[QRectF]:
        out, y = [], 0.0
        for kind, _value in self.rows:
            height = self.HEADER if kind == "header" else self.ROW
            out.append(QRectF(0, y, self.width(), height))
            y += height
        return out

    def index_at(self, pos: QPoint) -> int:
        for index, rect in enumerate(self._geometry()):
            if rect.contains(pos.toPointF()) and self.rows[index][0] == "tile":
                return index
        return -1

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        visible = self.visibleRegion().boundingRect()
        for index, rect in enumerate(self._geometry()):
            if rect.bottom() < visible.top() or rect.top() > visible.bottom():
                continue
            kind, value = self.rows[index]
            if kind == "header":
                p.setPen(QColor(theme.TEXT_2))
                p.setFont(shell_font(12, QFont.Weight.DemiBold))
                p.drawText(rect.adjusted(12, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, str(value))
                continue
            tile: Tile = value  # type: ignore[assignment]
            if index == self.hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.HALO))
                p.drawRoundedRect(rect.adjusted(2, 1, -2, -1), theme.R_ITEM, theme.R_ITEM)
            draw_pixmap(
                p,
                app_pixmap(self.ctx, tile.kind, tile.target, 24),
                QRectF(12, rect.center().y() - 12, 24, 24),
            )
            p.setPen(QColor(theme.TEXT))
            p.setFont(shell_font(13))
            p.drawText(rect.adjusted(48, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter, tile.label)

    def letters(self) -> list[str]:
        return [str(val) for kind, val in self.rows if kind == "header"]

    def y_for_letter(self, letter: str) -> int | None:
        target = letter.upper()
        for (kind, val), rect in zip(self.rows, self._geometry(), strict=False):
            if kind == "header" and str(val).upper() == target:
                return round(rect.top())
        return None

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
        if index < 0:
            return
        tile = self.rows[index][1]
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(tile)
        elif event.button() == Qt.MouseButton.RightButton:
            self.context.emit(tile, event.globalPosition().toPoint())


def connect_grid(
    grid: QWidget, activate: Callable[[Tile], None], menu: Callable[[Tile, QPoint], None]
) -> None:
    grid.activated.connect(activate)  # type: ignore[attr-defined]
    grid.context.connect(menu)  # type: ignore[attr-defined]
