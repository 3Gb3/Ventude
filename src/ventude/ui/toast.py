"""Avisos na própria ilha: ela cresce um pouco por alguns segundos e volta a ser pílula."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ventude.core.store import Event, Store
from ventude.ui import icons, theme
from ventude.ui.widgets import ElidedLabel, rounded_pixmap

TOAST_WIDTH = 400
TOAST_HEIGHT = 62


@dataclass
class Toast:
    title: str
    text: str = ""
    icon: str = "bell"
    color: str = theme.ACCENT
    art: bytes = b""
    seconds: float = 3.5
    kind: str = "info"  # limits | music | agenda | timer | tool | info
    extra: dict = field(default_factory=dict)


class ToastView(QWidget):
    """Conteúdo da ilha durante um aviso: ícone ou capa, título e uma linha de texto."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.icon = QLabel()
        self.icon.setFixedSize(40, 40)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = ElidedLabel("", "")
        self.title.setStyleSheet("font-size: 13px; font-weight: 600;")
        self.text = ElidedLabel("", "Muted")
        texts = QVBoxLayout()
        texts.setSpacing(1)
        texts.addStretch(1)
        texts.addWidget(self.title)
        texts.addWidget(self.text)
        texts.addStretch(1)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 22, 0)
        row.setSpacing(12)
        row.addWidget(self.icon)
        row.addLayout(texts, 1)
        self.hide()

    def show_toast(self, toast: Toast) -> None:
        art = rounded_pixmap(toast.art, 40, 10) if toast.art else None
        if art:
            self.icon.setPixmap(art)
            self.icon.setStyleSheet("")
        else:
            c = theme.color(toast.color)
            self.icon.setStyleSheet(
                f"background: rgba({c.red()},{c.green()},{c.blue()},0.16); border-radius: 13px;"
            )
            self.icon.setPixmap(icons.pixmap(toast.icon, toast.color, 20))
        self.title.setText(toast.title)
        self.text.setText(toast.text)
        self.text.setVisible(bool(toast.text))


class Reminders(QObject):
    """Lembra dos compromissos com horário 10 e 5 minutos antes (a Agenda deixa de ser só passiva)."""

    due = Signal(object, int)  # Event, minutos que faltam
    MARKS = (10, 5)

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self._fired: set[tuple[int, int]] = set()
        self.timer = QTimer(self, interval=20_000)
        self.timer.timeout.connect(self.check)

    def start(self) -> None:
        self.timer.start()
        self.check()

    def check(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        for event in self.store.events_on(now.date()) + self.store.events_on(now.date() + timedelta(days=1)):
            if event.start is None:
                continue
            starts = datetime.combine(event.day, event.start)
            left = (starts - now).total_seconds() / 60
            for mark in self.MARKS:
                key = (event.id, mark)
                if key in self._fired or not (mark - 1 < left <= mark):
                    if key not in self._fired and left <= mark - 1:
                        self._fired.add(key)  # já passou desse aviso (ex.: app aberto em cima da hora)
                    continue
                self._fired.add(key)
                self.due.emit(event, mark)


def event_toast(event: Event, minutes: int) -> Toast:
    when = event.when if event.day == date.today() else f"amanhã, {event.when}"
    return Toast(
        f"{event.title} em {minutes} min",
        f"Agenda · {when}",
        icon="calendar",
        color=theme.ACCENT,
        seconds=6,
        kind="agenda",
    )
