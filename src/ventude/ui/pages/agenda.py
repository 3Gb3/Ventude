"""Agenda: calendário do mês e compromissos do dia, guardados só neste PC."""

from __future__ import annotations

import re
from calendar import monthcalendar
from datetime import date, time, timedelta

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import QAbstractButton, QGridLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ventude.core.store import Event
from ventude.core.timefmt import WEEKDAYS_LETTER, WEEKDAYS_SHORT, long_date, month_title, weekday_name
from ventude.ui import theme
from ventude.ui.context import AppContext
from ventude.ui.pages.base import Page, scroller
from ventude.ui.widgets import Card, ElidedLabel, EmptyState, IconButton, hbox, label


def parse_times(text: str) -> tuple[time | None, time | None]:
    """'14', '14:30', '14h', '9-10', '14:00 - 15:30' -> (início, fim)."""
    found = re.findall(r"(\d{1,2})(?:[:h.](\d{2}))?", text)
    times = []
    for hour, minute in found[:2]:
        h, m = int(hour), int(minute or 0)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError("horário inválido")
        times.append(time(h, m))
    if text.strip() and not times:
        raise ValueError("horário inválido")
    start = times[0] if times else None
    end = times[1] if len(times) > 1 else None
    return start, end


class DayButton(QAbstractButton):
    picked = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(36, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.day: date | None = None
        self.selected = self.today = self.other = self.marked = False
        self.clicked.connect(lambda: self.day and self.picked.emit(self.day))

    def set_day(self, day: date, *, selected: bool, today: bool, other: bool, marked: bool) -> None:
        self.day, self.selected, self.today, self.other, self.marked = day, selected, today, other, marked
        self.setToolTip(f"{day:%d/%m/%Y}")
        self.update()

    def paintEvent(self, _event: object) -> None:
        if not self.day:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(3, 1, 30, 30)
        p.setPen(Qt.PenStyle.NoPen)
        if self.selected:
            p.setBrush(theme.color(theme.ACCENT))
            p.drawRoundedRect(r, 10, 10)
        elif self.underMouse():
            p.setBrush(theme.color(theme.SURFACE_2))
            p.drawRoundedRect(r, 10, 10)
        if self.today and not self.selected:
            p.setPen(theme.color(theme.ACCENT, 0.8))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        if self.selected:
            ink = theme.ACCENT_TEXT
        elif self.other:
            ink = theme.MUTED
        else:
            ink = theme.TEXT
        p.setPen(theme.color(ink))
        p.setFont(theme.font(12, theme.SEMI if self.today or self.selected else QFont.Weight.Normal))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(self.day.day))
        if self.marked:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color(theme.ACCENT_TEXT if self.selected else theme.ACCENT))
            p.drawEllipse(QRectF(16.5, 25, 3.5, 3.5))


class EventRow(QWidget):
    def __init__(self, event: Event, on_delete) -> None:  # noqa: ANN001
        super().__init__()
        chip = label(event.when, "")
        chip.setFixedWidth(104)
        chip.setStyleSheet(
            f"background: {theme.SURFACE_2}; border-radius: 8px; padding: 5px 8px;"
            f"color: {theme.ACCENT}; font-size: 12px; font-weight: 600;"
        )
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = ElidedLabel(event.title)
        delete = IconButton("trash", "Remover compromisso", 28, 14)
        delete.clicked.connect(lambda: on_delete(event))
        self.setLayout(hbox(chip, title, delete, spacing=10))


class AgendaPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        self.cursor = date.today().replace(day=1)
        self.selected = date.today()

        calendar = Card(margins=(14, 12, 14, 12), spacing=8)
        calendar.setFixedWidth(300)
        self.month = label("", "H2")
        prev = IconButton("chevron-left", "Mês anterior", 28, 16)
        nxt = IconButton("chevron-right", "Próximo mês", 28, 16)
        today = QPushButton("Hoje")
        today.setCursor(Qt.CursorShape.PointingHandCursor)
        today.setStyleSheet("padding: 4px 10px;")
        prev.clicked.connect(lambda: self._shift(-1))
        nxt.clicked.connect(lambda: self._shift(1))
        today.clicked.connect(self._today)
        calendar.body.addLayout(hbox(self.month, None, today, prev, nxt, spacing=4))
        grid = QGridLayout()
        grid.setSpacing(2)
        for column, letter in enumerate(WEEKDAYS_LETTER):
            head = label(letter, "Caption")
            head.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(head, 0, column)
        self.days: list[DayButton] = []
        for r in range(6):
            for c in range(7):
                button = DayButton()
                button.picked.connect(self._pick)
                grid.addWidget(button, r + 1, c)
                self.days.append(button)
        calendar.body.addLayout(grid)
        calendar.body.addSpacing(6)
        calendar.body.addWidget(label("Próximos dias", "Caption"))
        self.upcoming = QVBoxLayout()
        self.upcoming.setSpacing(4)
        calendar.body.addLayout(self.upcoming)
        calendar.body.addStretch(1)

        side = QVBoxLayout()
        side.setSpacing(10)
        self.day_title = label("", "H1")
        self.day_sub = label("", "Faint")
        side.addLayout(_stack(self.day_title, self.day_sub))
        self.list_box = QWidget()
        self.rows = QVBoxLayout(self.list_box)
        self.rows.setContentsMargins(0, 0, 4, 0)
        self.rows.setSpacing(6)
        self.rows.addStretch(1)
        self.empty = EmptyState("calendar", "Nada marcado", "Adicione um compromisso abaixo.")
        self.list_scroll = scroller(self.list_box)
        side.addWidget(self.list_scroll, 1)
        side.addWidget(self.empty, 1)
        self.title_in = QLineEdit()
        self.title_in.setPlaceholderText("Novo compromisso")
        self.time_in = QLineEdit()
        self.time_in.setPlaceholderText("14:00 ou 9-10")
        self.time_in.setFixedWidth(118)
        add = QPushButton("Adicionar")
        add.setObjectName("Primary")
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        for widget in (self.title_in, self.time_in):
            widget.returnPressed.connect(self._add)
        add.clicked.connect(self._add)
        side.addLayout(hbox(self.title_in, self.time_in, add, spacing=8))
        self.error = label("", "Error")
        self.error.hide()
        side.addWidget(self.error)

        body = hbox(calendar, spacing=16)
        body.addLayout(side, 1)
        self.root.addLayout(body, 1)
        ctx.data_changed.connect(lambda kind: kind == "events" and self.refresh())
        self.refresh()

    def _shift(self, delta: int) -> None:
        month = self.cursor.month - 1 + delta
        self.cursor = date(self.cursor.year + month // 12, month % 12 + 1, 1)
        self.refresh()

    def _today(self) -> None:
        self.selected = date.today()
        self.cursor = self.selected.replace(day=1)
        self.refresh()

    def _pick(self, day: date) -> None:
        self.selected = day
        if day.month != self.cursor.month:
            self.cursor = day.replace(day=1)
        self.refresh()

    def refresh(self) -> None:
        year, month = self.cursor.year, self.cursor.month
        self.month.setText(month_title(year, month))
        marked = self.ctx.store.days_with_events(year, month)
        weeks = monthcalendar(year, month)
        first = date(year, month, 1)
        start = first.toordinal() - first.weekday()
        today = date.today()
        for index, button in enumerate(self.days):
            day = date.fromordinal(start + index)
            in_month = day.month == month
            button.setVisible(index < len(weeks) * 7)
            button.set_day(
                day,
                selected=day == self.selected,
                today=day == today,
                other=not in_month,
                marked=in_month and day.day in marked,
            )
        self._refresh_day()
        self._refresh_upcoming()

    def _refresh_upcoming(self) -> None:
        while self.upcoming.count():
            child = self.upcoming.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        today = date.today()
        events = [e for e in self.ctx.store.upcoming_events(today + timedelta(days=1), 14)][:4]
        for event in events:
            when = label(f"{WEEKDAYS_SHORT[event.day.weekday()]} {event.day.day}", "Faint")
            when.setFixedWidth(46)
            row = QWidget()
            row.setLayout(hbox(when, ElidedLabel(event.title, "Muted"), spacing=6))
            if event.start:
                row.layout().addWidget(label(f"{event.start:%H:%M}", "Faint"))
            self.upcoming.addWidget(row)
        if not events:
            self.upcoming.addWidget(label("Nada nas próximas duas semanas.", "Faint"))

    def _refresh_day(self) -> None:
        day = self.selected
        today = date.today()
        prefix = "Hoje" if day == today else "Amanhã" if (day - today).days == 1 else ""
        self.day_title.setText(weekday_name(day))
        self.day_sub.setText(" · ".join(x for x in (prefix, long_date(day), str(day.year)) if x))
        while self.rows.count() > 1:
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        events = self.ctx.store.events_on(day)
        for index, event in enumerate(events):
            self.rows.insertWidget(index, EventRow(event, self._delete))
        self.list_scroll.setVisible(bool(events))
        self.empty.setVisible(not events)

    def _add(self) -> None:
        title = self.title_in.text().strip()
        if not title:
            self.title_in.setFocus()
            return
        try:
            start, end = parse_times(self.time_in.text())
        except ValueError:
            self.error.setText("Horário inválido. Use 14:00, 14h ou 9-10.")
            self.error.show()
            return
        self.error.hide()
        self.ctx.store.add_event(title, self.selected, start, end)
        self.title_in.clear()
        self.time_in.clear()
        self.ctx.data_changed.emit("events")

    def _delete(self, event: Event) -> None:
        self.ctx.store.remove_event(event.id)
        self.ctx.data_changed.emit("events")

    def on_show(self) -> None:
        self.refresh()


def _stack(*widgets: QWidget) -> QVBoxLayout:
    column = QVBoxLayout()
    column.setSpacing(1)
    for widget in widgets:
        column.addWidget(widget)
    return column
