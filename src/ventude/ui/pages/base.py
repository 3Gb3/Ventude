from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ventude.ui.context import AppContext
from ventude.ui.widgets import label


class Page(QWidget):
    """Uma aba do painel. on_show() roda sempre que a aba aparece."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(12)

    def on_show(self) -> None:  # pragma: no cover - sobrescrito
        pass

    def header(self, title: str, subtitle: str = "") -> tuple[QHBoxLayout, QLabel]:
        row = QHBoxLayout()
        row.setSpacing(10)
        column = QVBoxLayout()
        column.setSpacing(1)
        column.addWidget(label(title, "H1"))
        sub = label(subtitle, "Faint")
        column.addWidget(sub)
        row.addLayout(column)
        row.addStretch(1)
        self.root.addLayout(row)
        return row, sub


def scroller(content: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(content)
    return area
