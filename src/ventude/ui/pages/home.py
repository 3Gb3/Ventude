"""Início: relógio, clima, limites das IAs, mídia e tarefas num relance."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QMouseEvent, QPainter
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QLineEdit, QSlider, QVBoxLayout, QWidget

from ventude.core.timefmt import (
    WEEKDAYS_LETTER,
    WEEKDAYS_SHORT,
    clock,
    long_date,
    resets_text,
    short_reset,
    weekday_name,
)
from ventude.limits.model import Report, Window
from ventude.services.media import MediaState, format_time
from ventude.services.weather import Forecast, Place, icon_for
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.pages.base import Page
from ventude.ui.widgets import (
    Card,
    CheckCircle,
    ElidedLabel,
    IconButton,
    Ring,
    hbox,
    label,
    rounded_pixmap,
    vbox,
)


class WeekStrip(QWidget):
    """A semana atual, com hoje em destaque e um ponto nos dias com compromisso."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(44)
        self.days: list[date] = []
        self.marked: set[date] = set()

    def set_week(self, today: date, marked: set[date]) -> None:
        monday = today - timedelta(days=today.weekday())
        self.days = [monday + timedelta(days=i) for i in range(7)]
        self.today, self.marked = today, marked
        self.update()

    def paintEvent(self, _event: object) -> None:
        if not self.days:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        step = self.width() / 7
        for index, day in enumerate(self.days):
            x = index * step
            p.setFont(theme.font(10, theme.SEMI))
            p.setPen(theme.color(theme.MUTED))
            p.drawText(QRectF(x, 0, step, 14), Qt.AlignmentFlag.AlignCenter, WEEKDAYS_LETTER[index])
            box = QRectF(x + step / 2 - 12, 17, 24, 24)
            is_today = day == self.today
            if is_today:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.color(theme.ACCENT))
                p.drawRoundedRect(box, 8, 8)
            p.setFont(theme.font(12, theme.SEMI if is_today else QFont.Weight.Normal))
            p.setPen(theme.color(theme.ACCENT_TEXT if is_today else theme.TEXT))
            p.drawText(box.adjusted(0, -2, 0, -2), Qt.AlignmentFlag.AlignCenter, str(day.day))
            if day in self.marked:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.color(theme.ACCENT_TEXT if is_today else theme.ACCENT))
                p.drawEllipse(QPointF(box.center().x(), box.bottom() - 4), 1.6, 1.6)


def icon_label(name: str, color: str, size: int) -> QLabel:
    widget = QLabel()
    widget.setFixedSize(size, size)
    widget.setPixmap(icons.pixmap(name, color, size))
    return widget


def day_word(day: date, today: date) -> str:
    delta = (day - today).days
    if delta == 0:
        return "Hoje"
    if delta == 1:
        return "Amanhã"
    if 1 < delta < 7:
        return f"{WEEKDAYS_SHORT[day.weekday()].capitalize()} {day.day}"
    return f"{day:%d/%m}"


class ClockCard(Card):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(clickable=True, spacing=2)
        self.ctx = ctx
        self.weekday = label("", "Muted")
        self.time = label("")
        self.time.setFont(theme.font(46, theme.SEMI, display=True))
        self.date = label("", "Muted")
        self.week = WeekStrip()
        self.event = ElidedLabel("", "")
        self.event.setStyleSheet("font-size: 12px; font-weight: 600;")
        self.event_when = label("", "Faint")
        self.body.addWidget(self.weekday)
        self.body.addWidget(self.time)
        self.body.addWidget(self.date)
        self.body.addStretch(1)
        self.body.addWidget(self.week)
        self.body.addSpacing(6)
        self.body.addLayout(hbox(icon_label("calendar", theme.ACCENT, 14), self.event, spacing=7))
        self.body.addWidget(self.event_when)
        self.clicked.connect(lambda: ctx.navigate_requested.emit("agenda"))
        ctx.data_changed.connect(lambda kind: kind == "events" and self.refresh_event())
        self._day: date | None = None

    def tick(self) -> None:
        now = datetime.now()
        self.time.setText(clock(now, self.ctx.settings.clock_24h))
        if self._day != now.date():
            self._day = now.date()
            self.weekday.setText(weekday_name(now.date()))
            self.date.setText(long_date(now.date()))
            self.refresh_event()
        elif now.second == 0:
            self.refresh_event()

    def refresh_event(self) -> None:
        today = date.today()
        marked = {e.day for e in self.ctx.store.upcoming_events(today - timedelta(days=today.weekday()), 7)}
        self.week.set_week(today, marked)
        event = self.ctx.store.next_event()
        if not event:
            self.event.setText("Agenda livre")
            self.event_when.setText("Nada marcado nos próximos dias")
            return
        self.event.setText(event.title)
        self.event_when.setText(f"{day_word(event.day, today)} · {event.when}")


class DayCell(QWidget):
    """Um dos próximos dias no card de clima. Clique para abrir os detalhes dele."""

    clicked = Signal(str)

    def __init__(self, day: dict) -> None:
        super().__init__()
        self.iso = day["date"]
        when = date.fromisoformat(self.iso)
        name = label(WEEKDAYS_SHORT[when.weekday()].capitalize(), "Faint")
        temps = label(f"{day['max']:.0f}° {day['min']:.0f}°", "")
        temps.setStyleSheet("font-size: 11px; font-weight: 600;")
        glyph = icon_for(day["code"])
        self.setLayout(hbox(icon_label(glyph, tint(glyph), 16), vbox(name, temps, spacing=0), spacing=6))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Ver os detalhes deste dia")
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.iso)
        event.accept()


class WeatherCard(Card):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(clickable=True, spacing=3)
        self.ctx = ctx
        self.city = ElidedLabel("Clima", "Caption")
        self.icon = QLabel()
        self.icon.setFixedSize(40, 40)
        self.temp = label("—")
        self.temp.setFont(theme.font(40, theme.SEMI, display=True))
        self.desc = ElidedLabel("Escolha sua cidade", "Muted")
        self.range = label("", "Faint")
        self.days = QHBoxLayout()
        self.days.setSpacing(6)
        self.body.addWidget(self.city)
        self.body.addSpacing(2)
        self.body.addLayout(hbox(self.icon, self.temp, None, spacing=8))
        self.body.addWidget(self.desc)
        self.body.addWidget(self.range)
        self.body.addStretch(1)
        self.body.addLayout(self.days)
        self.clicked.connect(lambda: ctx.navigate_requested.emit("weather"))
        self.icon.setPixmap(icons.pixmap("cloud-sun", theme.SUN, 38))
        self._key = ""
        self._loading = False

    def refresh(self, force: bool = False) -> None:
        place = Place.from_json(self.ctx.settings.weather_city_json)
        if not place:
            self._key = ""
            self.city.setText("Clima")
            self.temp.setText("—")
            self.desc.setText("Toque para escolher sua cidade")
            self.range.setText("")
            self._clear_days()
            return
        key = place.to_json()
        if (key == self._key and not force) or self._loading:
            return
        self._key, self._loading = key, True
        self.city.setText(place.name)
        self.ctx.run(
            lambda: self.ctx.weather.forecast(place, force=force),
            done=self._show,
            fail=self._fail,
            owner=self,
        )

    def _clear_days(self) -> None:
        while self.days.count():
            item = self.days.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _show(self, forecast: Forecast) -> None:
        self._loading = False
        self.temp.setText(f"{forecast.temp:.0f}°")
        self.desc.setText(forecast.description)
        self.icon.setPixmap(icons.pixmap(forecast.icon, tint(forecast.icon), 38))
        self._clear_days()
        if not forecast.days:
            return
        today = forecast.days[0]
        self.range.setText(f"Máx {today['max']:.0f}° · Mín {today['min']:.0f}°")
        for day in forecast.days[1:4]:
            cell = DayCell(day)
            cell.clicked.connect(self._open_day)
            self.days.addWidget(cell)
        self.days.addStretch(1)

    def _open_day(self, iso: str) -> None:
        self.ctx.weather_focus = iso
        self.ctx.navigate_requested.emit("weather")

    def _fail(self, _error: BaseException) -> None:
        self._loading = False
        self._key = ""
        self.desc.setText("Clima indisponível agora")


def tint(icon_name: str) -> str:
    return theme.SUN if icon_name in ("sun", "cloud-sun") else theme.SKY


class LimitsCard(Card):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(clickable=True, spacing=10)
        self.ctx = ctx
        self.body.addWidget(label("Limites das IAs", "Caption"))
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(1)
        self.body.addStretch(1)
        self.body.addLayout(self.grid)
        self.empty = label("Nenhuma IA escolhida. Escolha em Ajustes > Limites das IAs.", "Muted", wrap=True)
        self.body.addWidget(self.empty)
        self.body.addStretch(1)
        self.columns: dict[str, tuple[Ring, QLabel, ElidedLabel, ElidedLabel]] = {}
        for index, provider in enumerate(ctx.limits.providers()):
            ring = Ring(66, 6)
            name = label("", "")
            name.setStyleSheet("font-size: 12px; font-weight: 600;")
            window = ElidedLabel("", "Faint")
            when = ElidedLabel("", "Faint")
            for widget in (name, window, when):
                widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(ring, 0, index, Qt.AlignmentFlag.AlignHCenter)
            self.grid.setRowMinimumHeight(1, 8)
            self.grid.addWidget(name, 2, index)
            self.grid.addWidget(window, 3, index)
            self.grid.addWidget(when, 4, index)
            self.columns[provider] = (ring, name, window, when)
        self.clicked.connect(lambda: ctx.navigate_requested.emit("limits"))
        ctx.limits.changed.connect(lambda _p: self.refresh())
        ctx.limits.ticked.connect(self.refresh)
        ctx.settings_changed.connect(lambda _s: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        settings = self.ctx.settings
        chosen = [
            p for p in self.columns if settings.ring_enabled(p) and self.ctx.limits.report(p).state != "off"
        ]
        size, thickness = {1: (66, 6), 2: (66, 6), 3: (66, 6), 4: (56, 5.5)}.get(len(chosen), (46, 5))
        visible = 0
        for provider, (ring, name, window, when) in self.columns.items():
            report = self.ctx.limits.report(provider)
            shown = provider in chosen
            for widget in (ring, name, window, when):
                widget.setVisible(shown)
            if not shown:
                continue
            visible += 1
            ring.set_size(size, thickness)
            name.setText(report.name)
            head = self.ctx.limits.headline(report)
            fill_ring(ring, report, head, compact=False)
            first, second = ring_caption(report, head)
            window.setText(first)
            when.setText(second)
        self.empty.setVisible(visible == 0)


def ring_caption(report: Report, head: Window | None) -> tuple[str, str]:
    """Duas linhas curtas: qual janela manda agora e quando ela renova."""
    if report.state == "setup":
        return "Configurar", ""
    if report.state == "loading":
        return "Consultando…", ""
    if not head:
        return ("Indisponível" if report.state == "error" else "Sem dados"), ""
    if head.key == "session":
        name = "Sessão 5h"
    elif head.key.startswith("weekly"):
        name = "Semana"
    else:
        name = head.label.split(" · ")[0]
    when = short_reset(head.resets_at)
    if when == "livre":
        return name, "renovado"
    if when and when[0].isdigit() or when.startswith("menos"):
        when = f"em {when}"
    return name, when


def fill_ring(ring: Ring, report: Report, head: Window | None, *, compact: bool) -> None:
    used = head.used_at() if head else None
    color = theme.usage_color(report.provider, used)
    text = "" if compact else ("—" if used is None else f"{used:.0f}%")
    ring.set_value(used, color, text, dim=report.state != "ok")
    tip = f"{report.name}: " + (
        f"{used:.0f}% usado em {head.label}. {resets_text(head.resets_at)}"
        if head and used is not None
        else (report.message or "sem dados")
    )
    ring.setToolTip(tip)


def pretty_source(app_id: str) -> str:
    lowered = app_id.lower()
    for key, name in (
        ("spotify", "Spotify"),
        ("msedge", "Edge"),
        ("chrome", "Chrome"),
        ("firefox", "Firefox"),
        ("308046b0af4a39cb", "Firefox"),
        ("zunemusic", "Media Player"),
        ("vlc", "VLC"),
        ("deezer", "Deezer"),
        ("tidal", "Tidal"),
        ("youtube", "YouTube Music"),
    ):
        if key in lowered:
            return name
    if lowered.endswith(".exe"):
        return app_id.rsplit("\\", 1)[-1][:-4].capitalize()
    return ""


class MediaCard(Card):
    ART = 108

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(margins=(14, 14, 16, 14), spacing=0)
        self.ctx = ctx
        self.media = ctx.media
        self.state: MediaState | None = None
        self._seeking = False
        self.art = label()
        self.art.setFixedSize(self.ART, self.ART)
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art.setStyleSheet(f"background: {theme.SURFACE_2}; border-radius: 16px;")
        self.title = ElidedLabel("", "H2")
        self.artist = ElidedLabel("", "Muted")
        self.pos = label("0:00", "Faint")
        self.dur = label("0:00", "Faint")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.slider.sliderReleased.connect(self._seek)
        self.prev = IconButton("skip-back", "Anterior", 34, 16, theme.TEXT)
        self.play = IconButton("play", "Tocar", 40, 18, theme.BG)
        self.play.setStyleSheet(
            f"QPushButton {{ background: {theme.TEXT}; border-radius: 20px; }}"
            "QPushButton:hover { background: #FFFFFF; }"
            f"QPushButton:disabled {{ background: {theme.SURFACE_3}; }}"
        )
        self.next = IconButton("skip-forward", "Próxima", 34, 16, theme.TEXT)
        self.prev.clicked.connect(self.media.previous)
        self.play.clicked.connect(self.media.play_pause)
        self.next.clicked.connect(self.media.next)
        self.source = label("Tocando agora", "Caption")
        bar = hbox(self.pos, self.slider, self.dur, spacing=8)
        controls = hbox(self.prev, self.play, self.next, None, spacing=4)
        self.player = QWidget()
        self.player.setLayout(
            vbox(None, self.source, self.title, self.artist, 8, bar, controls, None, spacing=3)
        )
        self.empty = QWidget()
        self.empty_text = label("", "Muted", wrap=True)
        self.empty.setLayout(vbox(None, label("Nada tocando", "H2"), self.empty_text, None, spacing=4))
        ctx.settings_changed.connect(lambda _s: self._empty_hint())
        self._empty_hint()
        self.body.addLayout(hbox(self.art, self.player, self.empty, spacing=16))
        self.tick = QTimer(self, interval=500, timeout=self._progress)
        self.media.state_changed.connect(self.set_state)
        self.media.thumbnail_changed.connect(self.set_art)
        self.set_state(self.media.state)
        self.set_art(self.media.thumbnail)

    def _empty_hint(self) -> None:
        if self.ctx.settings.media_source == "spotify":
            text = "Abra o Spotify e dê play. Para seguir outros players, troque em Ajustes > Música."
        else:
            text = "Abra o Spotify, o YouTube ou qualquer player do Windows."
        self.empty_text.setText(text)

    def set_state(self, state: MediaState | None) -> None:
        self.state = state
        self.player.setVisible(state is not None)
        self.empty.setVisible(state is None)
        if not state:
            self.tick.stop()
            return
        app = pretty_source(state.source_app)
        status = "Tocando agora" if state.is_playing else "Pausado"
        self.source.setText(f"{status} · {app}" if app else status)
        self.title.setText(state.title)
        self.artist.setText(" · ".join(x for x in (state.artist, state.album) if x) or "—")
        self.play.set_icon("pause" if state.is_playing else "play", theme.BG)
        self.play.setToolTip("Pausar" if state.is_playing else "Tocar")
        self.play.setEnabled(state.can_play_pause)
        self.prev.setEnabled(state.can_previous)
        self.next.setEnabled(state.can_next)
        self.slider.setEnabled(state.can_seek)
        self.slider.setRange(0, int(state.duration_s * 1000))
        self.dur.setText(format_time(state.duration_s) if state.duration_s else "—")
        self._progress()
        if state.is_playing and state.duration_s:
            self.tick.start()
        else:
            self.tick.stop()

    def set_art(self, data: bytes) -> None:
        art = rounded_pixmap(data, self.ART, 16)
        if art:
            self.art.setPixmap(art)
        else:
            self.art.setPixmap(icons.pixmap("music", theme.ACCENT, 30))

    def _progress(self) -> None:
        state = self.media.state or self.state
        if not state or self._seeking:
            return
        position = state.position_at()
        self.pos.setText(format_time(position))
        self.slider.setValue(int(position * 1000))

    def _seek(self) -> None:
        self._seeking = False
        self.media.seek(self.slider.value() / 1000)
        self._progress()


class TasksCard(Card):
    LIMIT = 4

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(spacing=6)
        self.ctx = ctx
        self.title = label("Tarefas", "Caption")
        open_btn = IconButton("chevron-right", "Abrir tarefas", 24, 14)
        open_btn.clicked.connect(lambda: ctx.navigate_requested.emit("tasks"))
        self.body.addLayout(hbox(self.title, None, open_btn))
        self.rows = QVBoxLayout()
        self.rows.setSpacing(2)
        self.body.addLayout(self.rows)
        self.more = label("", "Faint")
        self.body.addWidget(self.more)
        self.body.addStretch(1)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Nova tarefa…")
        self.input.returnPressed.connect(self._add)
        self.body.addWidget(self.input)
        ctx.data_changed.connect(lambda kind: kind == "tasks" and self.refresh())
        self.refresh()

    def _list(self) -> int:
        lists = self.ctx.store.lists()
        return lists[0].id if lists else self.ctx.store.add_list("Geral").id

    def refresh(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        list_id = self._list()
        name = next((lst.name for lst in self.ctx.store.lists() if lst.id == list_id), "Tarefas")
        pending = [i for i in self.ctx.store.items(list_id) if not i.done]
        self.title.setText(f"{name} · {len(pending)} pendente{'s' if len(pending) != 1 else ''}")
        for item in pending[: self.LIMIT]:
            check = CheckCircle()
            check.toggled.connect(lambda done, i=item.id: self._done(i, done))
            text = ElidedLabel(item.text)
            row = QWidget()
            row.setLayout(hbox(check, text, spacing=8))
            self.rows.addWidget(row)
        if not pending:
            self.rows.addWidget(label("Tudo em dia.", "Muted"))
        extra = len(pending) - self.LIMIT
        self.more.setText(f"+ {extra} na lista" if extra > 0 else "")
        self.more.setVisible(extra > 0)

    def _done(self, item_id: int, done: bool) -> None:
        self.ctx.store.set_done(item_id, done)
        QTimer.singleShot(250, lambda: self.ctx.data_changed.emit("tasks"))

    def _add(self) -> None:
        text = self.input.text().strip()
        if text:
            self.ctx.store.add_item(self._list(), text)
            self.input.clear()
            self.ctx.data_changed.emit("tasks")


class HomePage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        self.clock = ClockCard(ctx)
        self.weather = WeatherCard(ctx)
        self.limits = LimitsCard(ctx)
        self.media = MediaCard(ctx)
        self.tasks = TasksCard(ctx)
        top = hbox(spacing=12)
        top.addWidget(self.clock, 5)
        top.addWidget(self.weather, 4)
        top.addWidget(self.limits, 6)
        bottom = hbox(spacing=12)
        bottom.addWidget(self.media, 9)
        bottom.addWidget(self.tasks, 6)
        self.root.addLayout(top, 11)
        self.root.addLayout(bottom, 10)
        self.timer = QTimer(self, interval=1000, timeout=self._tick)
        self.timer.start()
        ctx.settings_changed.connect(lambda _s: (self.weather.refresh(), self._tick()))
        self.weather_timer = QTimer(self, interval=20 * 60_000)
        self.weather_timer.timeout.connect(lambda: self.weather.refresh(force=True))
        self.weather_timer.start()
        self._tick()
        self.clock.refresh_event()

    def _tick(self) -> None:
        if self.isVisible() or not self.clock.time.text():
            self.clock.tick()

    def on_show(self) -> None:
        self._tick()
        self.clock.refresh_event()
        self.weather.refresh()
        self.limits.refresh()
