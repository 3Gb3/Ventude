"""Clima: agora, próximos 7 dias e os detalhes de cada dia (hora a hora), pelo Open-Meteo."""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QLinearGradient, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ventude.core.timefmt import MONTHS, WEEKDAYS, WEEKDAYS_SHORT, ago
from ventude.services.weather import Forecast, Place, describe, icon_for, uv_level, wind_direction
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.pages.base import Page
from ventude.ui.widgets import (
    Card,
    ElidedLabel,
    EmptyState,
    IconButton,
    TempRange,
    hbox,
    label,
    vbox,
)

PLACE_ROLE = Qt.ItemDataRole.UserRole


def _tint(icon_name: str) -> str:
    return theme.SUN if icon_name in ("sun", "cloud-sun") else theme.SKY


def _chip(icon_name: str, text: str) -> QWidget:
    holder = QWidget()
    glyph = label()
    glyph.setPixmap(icons.pixmap(icon_name, theme.TEXT_2, 14))
    holder.setLayout(hbox(glyph, label(text, "Muted"), spacing=5))
    return holder


def _clear(layout: QHBoxLayout | QVBoxLayout) -> None:
    while layout.count():
        child = layout.takeAt(0)
        if child.widget():
            child.widget().deleteLater()


def day_title(day: date, today: date) -> str:
    delta = (day - today).days
    full = f"{WEEKDAYS[day.weekday()]}, {day.day} de {MONTHS[day.month - 1]}"
    if delta == 0:
        return f"Hoje · {full}"
    if delta == 1:
        return f"Amanhã · {full}"
    return full[:1].upper() + full[1:]


def _hhmm(iso: str) -> str:
    return iso[11:16] if len(iso) >= 16 else "—"


def _num(value: float | None, suffix: str = "", digits: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}".replace(".", ",") + suffix


def hour_text(hour: dict[str, Any]) -> str:
    """Linha de detalhes de uma hora (usada no gráfico ao passar o mouse)."""
    parts = [f"{_hhmm(hour['time'])} · {describe(hour['code'])}", f"{hour['temp']:.0f}°"]
    if hour.get("feels") is not None:
        parts[-1] += f" (sensação {hour['feels']:.0f}°)"
    if hour.get("humidity") is not None:
        parts.append(f"Umidade {hour['humidity']:.0f}%")
    rain = f"Chuva {hour.get('rain', 0)}%"
    if (hour.get("precip") or 0) > 0:
        rain += f" · {_num(hour['precip'], ' mm', 1)}"
    parts.append(rain)
    if hour.get("wind") is not None:
        parts.append(f"Vento {hour['wind']:.0f} km/h")
    if hour.get("uv") is not None and hour.get("is_day"):
        parts.append(f"UV {hour['uv']:.0f}")
    return " · ".join(parts)


class DayRow(QWidget):
    """Um dia da semana. Clique para ver os detalhes."""

    clicked = Signal(str)

    def __init__(self, day: dict[str, Any], today: date, low: float, high: float) -> None:
        super().__init__()
        self.iso = day["date"]
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(34)
        self.setToolTip("Ver umidade, chuva, vento e a previsão hora a hora")
        when = date.fromisoformat(self.iso)
        delta = (when - today).days
        name = "Hoje" if delta == 0 else "Amanhã" if delta == 1 else WEEKDAYS_SHORT[when.weekday()]
        name_label = label(name.capitalize(), "")
        name_label.setFixedWidth(66)
        glyph = label()
        glyph.setFixedWidth(22)
        glyph.setPixmap(icons.pixmap(icon_for(day["code"]), _tint(icon_for(day["code"])), 20))
        rain = label(f"{day['rain']}%" if day.get("rain", 0) >= 20 else "", "")
        rain.setStyleSheet(f"color: {theme.SKY}; font-size: 11px; font-weight: 600;")
        rain.setFixedWidth(34)
        desc = ElidedLabel(describe(day["code"]), "Faint")
        lo = label(f"{day['min']:.0f}°", "Muted")
        lo.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lo.setFixedWidth(34)
        bar = TempRange()
        bar.set_range(day["min"], day["max"], low, high)
        hi = label(f"{day['max']:.0f}°", "")
        hi.setFixedWidth(34)
        arrow = label()
        arrow.setPixmap(icons.pixmap("chevron-right", theme.MUTED, 14))
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 6, 0)
        row.setSpacing(12)
        for widget, stretch in ((name_label, 0), (glyph, 0), (rain, 0), (desc, 2), (lo, 0)):
            row.addWidget(widget, stretch)
        row.addWidget(bar, 3, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(hi)
        row.addWidget(arrow)
        for child in (name_label, glyph, rain, desc, lo, bar, hi, arrow):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _event: object) -> None:
        if not self.underMouse():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color(theme.SURFACE_2))
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.iso)


class StatTile(QFrame):
    def __init__(self, icon_name: str, title: str, color: str = theme.TEXT_2) -> None:
        super().__init__()
        self.setObjectName("Tile")
        self.setStyleSheet(
            f"QFrame#Tile {{ background: {theme.SURFACE_2}; border: 1px solid {theme.STROKE};"
            " border-radius: 12px; }"
        )
        glyph = label()
        glyph.setPixmap(icons.pixmap(icon_name, color, 14))
        self.value = label("—", "")
        self.value.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.sub = ElidedLabel("", "Faint")
        layout = vbox(hbox(glyph, label(title, "Caption"), None, spacing=6), self.value, self.sub, spacing=2)
        layout.setContentsMargins(12, 9, 10, 9)
        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set(self, value: str, sub: str) -> None:
        self.value.setText(value)
        self.sub.setText(sub)


class HourlyChart(QWidget):
    """Temperatura, ícone, chuva e umidade de cada hora do dia, com destaque ao passar o mouse."""

    hovered = Signal(int)
    GUTTER = 28

    def __init__(self) -> None:
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.hours: list[dict[str, Any]] = []
        self.now_index = -1
        self.hover = -1

    def set_hours(self, hours: list[dict[str, Any]], now_index: int = -1) -> None:
        self.hours, self.now_index, self.hover = hours, now_index, -1
        self.update()

    def _column(self) -> float:
        return (self.width() - self.GUTTER) / max(1, len(self.hours))

    def _x(self, index: int) -> float:
        return self.GUTTER + (index + 0.5) * self._column()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.hours:
            return
        index = int((event.position().x() - self.GUTTER) // self._column())
        index = index if 0 <= index < len(self.hours) else -1
        if index != self.hover:
            self.hover = index
            self.hovered.emit(index)
            self.update()

    def leaveEvent(self, _event: object) -> None:
        if self.hover != -1:
            self.hover = -1
            self.hovered.emit(-1)
            self.update()

    def paintEvent(self, _event: object) -> None:
        if not self.hours:
            p = QPainter(self)
            p.setPen(theme.color(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Sem previsão hora a hora para este dia.")
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h, col, n = self.width(), self.height(), self._column(), len(self.hours)
        step = 1 if col >= 44 else 2  # rótulos a cada 1 ou 2 horas, conforme a largura
        top_curve, bottom_curve = 62.0, h - 78.0
        rain_top, rain_bottom = h - 64.0, h - 30.0
        hum_y = h - 20.0

        # coluna destacada (mouse) e "agora"
        for index, color, alpha in ((self.now_index, theme.ACCENT, 0.08), (self.hover, theme.TEXT, 0.06)):
            if 0 <= index < n:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.color(color, alpha))
                p.drawRoundedRect(QRectF(self.GUTTER + index * col + 1, 0, col - 2, h), 8, 8)

        # ícones das linhas
        for name, y in (
            ("thermo", (top_curve + bottom_curve) / 2),
            ("rain", rain_top + 8),
            ("droplet", hum_y),
        ):
            p.drawPixmap(QPointF(4, y - 8), icons.pixmap(name, theme.MUTED, 15))

        temps = [float(hour["temp"]) for hour in self.hours]
        lo, hi = min(temps), max(temps)
        span = (hi - lo) or 1.0

        def y_of(value: float) -> float:
            return bottom_curve - (value - lo) / span * (bottom_curve - top_curve)

        points = [QPointF(self._x(i), y_of(t)) for i, t in enumerate(temps)]
        line = QPainterPath(points[0])
        for a, b in zip(points, points[1:], strict=False):
            mid = (a.x() + b.x()) / 2
            line.cubicTo(QPointF(mid, a.y()), QPointF(mid, b.y()), b)
        area = QPainterPath(line)
        area.lineTo(points[-1].x(), bottom_curve + 8)
        area.lineTo(points[0].x(), bottom_curve + 8)
        area.closeSubpath()
        gradient = QLinearGradient(0, top_curve, 0, bottom_curve + 8)
        gradient.setColorAt(0, theme.color(theme.SUN, 0.22))
        gradient.setColorAt(1, theme.color(theme.SUN, 0.0))
        p.fillPath(area, QBrush(gradient))
        pen = QPen(theme.color(theme.SUN), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawPath(line)

        bar_w = max(3.0, min(12.0, col * 0.42))
        for i, hour in enumerate(self.hours):
            x = self._x(i)
            past = 0 <= self.now_index and i < self.now_index
            fade = 0.45 if past else 1.0
            labelled = i % step == 0 or i == self.hover
            # hora
            if labelled:
                is_now = i == self.now_index
                p.setFont(theme.font(10, theme.SEMI))
                p.setPen(theme.color(theme.ACCENT if is_now else theme.MUTED, fade))
                text = "Agora" if is_now else f"{_hhmm(hour['time'])[:2]}h"
                p.drawText(QRectF(x - 24, 0, 48, 14), Qt.AlignmentFlag.AlignCenter, text)
                glyph = icon_for(hour["code"], hour.get("is_day", True))
                p.setOpacity(fade)
                p.drawPixmap(QPointF(x - 9, 18), icons.pixmap(glyph, _tint(glyph), 18))
                p.setOpacity(1.0)
                p.setFont(theme.font(11, theme.SEMI))
                p.setPen(theme.color(theme.TEXT, fade))
                pt = points[i]
                p.drawText(
                    QRectF(x - 22, pt.y() - 22, 44, 16), Qt.AlignmentFlag.AlignCenter, f"{hour['temp']:.0f}°"
                )
            # ponto
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color(theme.SUN if i != self.hover else theme.TEXT))
            radius = 3.6 if i == self.hover else (2.4 if labelled else 1.6)
            p.drawEllipse(points[i], radius, radius)
            # chuva
            chance = max(0, min(100, int(hour.get("rain") or 0)))
            p.setBrush(theme.color(theme.SURFACE_3))
            p.drawRoundedRect(QRectF(x - bar_w / 2, rain_top, bar_w, rain_bottom - rain_top), 3, 3)
            if chance:
                height = (rain_bottom - rain_top) * chance / 100
                p.setBrush(theme.color(theme.SKY, 0.9 * fade if chance >= 20 else 0.45 * fade))
                p.drawRoundedRect(QRectF(x - bar_w / 2, rain_bottom - height, bar_w, height), 3, 3)
            # umidade
            if labelled and hour.get("humidity") is not None:
                p.setFont(theme.font(10))
                p.setPen(theme.color(theme.TEXT_2, fade))
                p.drawText(
                    QRectF(x - 24, hum_y - 8, 48, 16),
                    Qt.AlignmentFlag.AlignCenter,
                    f"{hour['humidity']:.0f}%",
                )
        # linha de base da chuva
        p.setPen(QPen(theme.color(theme.STROKE_2), 1))
        p.drawLine(QPointF(self.GUTTER, rain_bottom + 0.5), QPointF(w, rain_bottom + 0.5))


class DayDetail(Card):
    """Detalhes de um dia: resumo, números do dia e o gráfico hora a hora."""

    back = Signal()
    step = Signal(int)

    def __init__(self) -> None:
        super().__init__(margins=(16, 12, 16, 12), spacing=10)
        self.back_btn = QPushButton(" Semana")
        self.back_btn.setObjectName("Chip")
        self.back_btn.setIcon(icons.icon("chevron-left", theme.TEXT_2, 14))
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.setToolTip("Voltar para a previsão da semana")
        self.back_btn.clicked.connect(self.back.emit)
        self.icon = label()
        self.icon.setFixedSize(36, 36)
        self.title = ElidedLabel("", "H2")
        self.desc = ElidedLabel("", "Muted")
        self.temps = label("", "")
        self.temps.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.prev_btn = IconButton("chevron-left", "Dia anterior", 30, 16)
        self.next_btn = IconButton("chevron-right", "Próximo dia", 30, 16)
        self.prev_btn.clicked.connect(lambda: self.step.emit(-1))
        self.next_btn.clicked.connect(lambda: self.step.emit(1))
        titles = vbox(self.title, self.desc, spacing=0)
        header = hbox(self.back_btn, 6, self.icon, spacing=8)
        header.addLayout(titles, 1)
        for item in (self.temps, 8, self.prev_btn, self.next_btn):
            if isinstance(item, int):
                header.addSpacing(item)
            else:
                header.addWidget(item)
        self.body.addLayout(header)
        self.tiles = {
            "humidity": StatTile("droplet", "Umidade", theme.SKY),
            "rain": StatTile("rain", "Chuva", theme.SKY),
            "wind": StatTile("wind", "Vento"),
            "uv": StatTile("sun", "Índice UV", theme.SUN),
            "feels": StatTile("thermo", "Sensação"),
            "sun": StatTile("sunrise", "Sol", theme.SUN),
        }
        row = hbox(spacing=8)
        for tile in self.tiles.values():
            row.addWidget(tile, 1)
        self.body.addLayout(row)
        self.info = ElidedLabel("", "Muted")
        self.body.addWidget(self.info)
        self.chart = HourlyChart()
        self.chart.hovered.connect(self._hovered)
        self.body.addWidget(self.chart, 1)
        self._idle_text = ""

    def show_day(
        self, day: dict[str, Any], hours: list[dict[str, Any]], today: date, index: int, count: int
    ) -> None:
        when = date.fromisoformat(day["date"])
        glyph = icon_for(day["code"])
        self.icon.setPixmap(icons.pixmap(glyph, _tint(glyph), 34))
        self.title.setText(day_title(when, today))
        self.desc.setText(describe(day["code"]))
        self.temps.setText(f"{day['max']:.0f}°  /  {day['min']:.0f}°")
        self.temps.setToolTip("Máxima / mínima")
        self.prev_btn.setEnabled(index > 0)
        self.next_btn.setEnabled(index < count - 1)

        hum = day.get("hum_mean")
        self.tiles["humidity"].set(
            _num(hum, "%"),
            f"de {_num(day.get('hum_min'), '%')} a {_num(day.get('hum_max'), '%')}"
            if hum is not None
            else "",
        )
        precip = day.get("precip")
        self.tiles["rain"].set(
            f"{day.get('rain', 0)}%", f"{_num(precip, ' mm', 1)} no dia" if precip is not None else ""
        )
        direction = wind_direction(day.get("wind_dir"))
        gusts = day.get("gusts")
        self.tiles["wind"].set(
            _num(day.get("wind_max"), " km/h"),
            " · ".join(x for x in (direction, f"rajadas {gusts:.0f}" if gusts is not None else "") if x),
        )
        uv = day.get("uv")
        self.tiles["uv"].set(_num(uv), uv_level(uv))
        self.tiles["feels"].set(_num(day.get("feels_max"), "°"), f"mín {_num(day.get('feels_min'), '°')}")
        self.tiles["sun"].set(_hhmm(day.get("sunrise", "")), f"pôr do sol {_hhmm(day.get('sunset', ''))}")

        now_index = -1
        if when == today:
            hour_now = datetime.now().hour
            now_index = next(
                (i for i, h in enumerate(hours) if int(_hhmm(h["time"])[:2] or 0) == hour_now), -1
            )
        self.chart.set_hours(hours, now_index)
        if 0 <= now_index < len(hours):
            self._idle_text = "Agora: " + hour_text(hours[now_index])
        else:
            self._idle_text = "Passe o mouse no gráfico para ver cada hora."
        self.info.setText(self._idle_text)

    def _hovered(self, index: int) -> None:
        hours = self.chart.hours
        self.info.setText(hour_text(hours[index]) if 0 <= index < len(hours) else self._idle_text)


class WeatherPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar cidade… (Enter)")
        self.search.returnPressed.connect(self._search)
        self.refresh_btn = IconButton("refresh", "Atualizar previsão", 34, 16)
        self.refresh_btn.clicked.connect(lambda: self.load(force=True))
        self.root.addLayout(hbox(self.search, self.refresh_btn, spacing=8))
        self.results = QListWidget()
        self.results.setMaximumHeight(170)
        self.results.itemClicked.connect(self._choose)
        self.results.hide()
        self.root.addWidget(self.results)

        self.hero = Card(margins=(20, 16, 20, 16))
        self.icon = label()
        self.icon.setFixedSize(64, 64)
        self.temp = label("—")
        self.temp.setFont(theme.font(54, theme.SEMI, display=True))
        self.place = ElidedLabel("", "H2")
        self.desc = label("", "Muted")
        self.chips = hbox(spacing=16)
        info = vbox(None, self.place, self.desc, self.chips, None, spacing=4)
        self.hero.body.addLayout(hbox(self.icon, self.temp, 18, info, spacing=10))
        self.root.addWidget(self.hero)

        self.week = Card(margins=(10, 8, 10, 8), spacing=0)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(1)
        self.week.body.addLayout(self.rows)
        self.week.body.addStretch(1)
        self.root.addWidget(self.week, 1)
        self.detail = DayDetail()
        self.detail.back.connect(self.close_day)
        self.detail.step.connect(self._step)
        self.detail.hide()
        self.root.addWidget(self.detail, 1)
        self.empty = EmptyState("sun", "Qual é a sua cidade?", "Busque acima e escolha na lista.")
        self.root.addWidget(self.empty, 1)
        self.status = label("Dados: Open-Meteo", "Faint")
        self.root.addWidget(self.status)
        self.forecast: Forecast | None = None
        self.day: str = ""  # dia aberto nos detalhes (ISO) ou vazio
        self._pending_day = ""
        self._loaded_at = 0.0
        ctx.weather_changed.connect(self._changed)
        ctx.weather_failed.connect(self._failed)
        self._show_empty(True)

    def _show_empty(self, empty: bool) -> None:
        self.empty.setVisible(empty)
        detail = bool(self.day) and not empty
        self.hero.setVisible(not empty and not detail)
        self.week.setVisible(not empty and not detail)
        self.detail.setVisible(detail)

    # ---------- busca ----------
    def _search(self) -> None:
        query = self.search.text().strip()
        if len(query) < 2:
            return
        self.status.setText("Buscando…")
        self.ctx.run(self.ctx.weather.search, query, done=self._results, fail=self._failed, owner=self)

    def _results(self, places: list[Place]) -> None:
        self.results.clear()
        for place in places:
            item = QListWidgetItem(icons.icon("search", theme.TEXT_2, 14), place.label)
            item.setData(PLACE_ROLE, place)
            self.results.addItem(item)
        self.results.setVisible(bool(places))
        self.status.setText(
            "Escolha uma cidade na lista." if places else "Nenhuma cidade encontrada com esse nome."
        )

    def _choose(self, item: QListWidgetItem) -> None:
        place: Place = item.data(PLACE_ROLE)
        self.results.hide()
        self.search.clear()
        self.day = ""
        self.forecast = None
        self.ctx.update_settings(weather_city_json=place.to_json())  # o app busca a nova cidade
        self.status.setText("Atualizando…")

    # ---------- previsão ----------
    def load(self, force: bool = False) -> None:
        place = Place.from_json(self.ctx.settings.weather_city_json)
        if not place:
            self.day = ""
            self._show_empty(True)
            self.status.setText("Dados: Open-Meteo, sem cadastro nem chave.")
            return
        if self.ctx.forecast is not None and not force:
            self._show(self.ctx.forecast)
            return
        self.status.setText("Atualizando…")
        self.ctx.refresh_weather(force=force)

    def _changed(self, forecast: Forecast | None) -> None:
        if forecast is not None:
            self._show(forecast)
        elif not Place.from_json(self.ctx.settings.weather_city_json):
            self.forecast, self.day = None, ""
            self._show_empty(True)

    def _failed(self, error: object) -> None:
        self.status.setText(f"Sem conexão com o Open-Meteo agora ({error}).")

    def _show(self, forecast: Forecast) -> None:
        self._loaded_at = time.time()
        self.forecast = forecast
        self.icon.setPixmap(icons.pixmap(forecast.icon, _tint(forecast.icon), 60))
        self.temp.setText(f"{forecast.temp:.0f}°")
        self.place.setText(forecast.place)
        self.desc.setText(forecast.description)
        _clear(self.chips)
        for icon_name, text in (
            ("thermo", f"Sensação {forecast.feels:.0f}°"),
            ("droplet", f"Umidade {forecast.humidity}%"),
            ("wind", f"Vento {forecast.wind:.0f} km/h"),
        ):
            self.chips.addWidget(_chip(icon_name, text))
        self.chips.addStretch(1)
        self._fill_week(forecast)
        self.status.setText(
            f"Open-Meteo · atualizado {ago(self._loaded_at)} · clique num dia para ver os detalhes"
        )
        if self._pending_day:
            self.day, self._pending_day = self._pending_day, ""
        if self.day and not any(d["date"] == self.day for d in forecast.days):
            self.day = ""
        self._render_day()
        self._show_empty(False)

    def _fill_week(self, forecast: Forecast) -> None:
        _clear(self.rows)
        days = forecast.days[:7]
        if not days:
            return
        low = min(d["min"] for d in days)
        high = max(d["max"] for d in days)
        today = date.today()
        for day in days:
            row = DayRow(day, today, low, high)
            row.clicked.connect(self.open_day)
            self.rows.addWidget(row)

    # ---------- detalhes do dia ----------
    def open_day(self, iso: str) -> None:
        if not self.forecast:
            self._pending_day = iso
            return
        self.day = iso
        self._render_day()
        self._show_empty(False)

    def close_day(self) -> None:
        self.day = ""
        self._show_empty(self.forecast is None)

    def _step(self, delta: int) -> None:
        if not self.forecast:
            return
        dates = [d["date"] for d in self.forecast.days]
        if self.day in dates:
            index = max(0, min(len(dates) - 1, dates.index(self.day) + delta))
            self.open_day(dates[index])

    def _render_day(self) -> None:
        if not self.forecast or not self.day:
            return
        days = self.forecast.days
        index = next((i for i, d in enumerate(days) if d["date"] == self.day), -1)
        if index < 0:
            return
        self.detail.show_day(days[index], self.forecast.hours_of(self.day), date.today(), index, len(days))

    def on_show(self) -> None:
        focus = self.ctx.weather_focus
        if focus:
            self.ctx.weather_focus = ""
            self.open_day(focus)
        self.load()
