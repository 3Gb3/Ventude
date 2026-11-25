"""Datas e durações em português, curtas e legíveis."""

from __future__ import annotations

import re
import time
from datetime import UTC, date, datetime

WEEKDAYS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
WEEKDAYS_SHORT = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
WEEKDAYS_LETTER = ["S", "T", "Q", "Q", "S", "S", "D"]
MONTHS = [
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]


def parse_instant(value: object) -> float | None:
    """ISO 8601 (com Z, frações longas) ou epoch em s/ms -> epoch em segundos."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number <= 0:
            return None
        return number / 1000 if number > 1e11 else number
    if isinstance(value, dict):  # protobuf Timestamp {"seconds": ...}
        seconds = value.get("seconds")
        return parse_instant(int(seconds)) if seconds is not None else None
    text = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return parse_instant(float(text))
    text = text.replace("Z", "+00:00").replace("z", "+00:00")
    # Python aceita no máximo 6 casas decimais nos segundos.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


def duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "menos de 1 min"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}min" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def resets_text(resets_at: float | None, now: float | None = None) -> str:
    if not resets_at:
        return ""
    now = time.time() if now is None else now
    left = resets_at - now
    if left <= 0:
        return "Renovado"
    if left < 36 * 3600:
        return f"Renova em {duration(left)}"
    moment = datetime.fromtimestamp(resets_at)
    return f"Renova {WEEKDAYS_SHORT[moment.weekday()]}, {moment:%H:%M}"


def short_reset(resets_at: float | None, now: float | None = None) -> str:
    """Versão curta para espaços pequenos: '2h 10min', 'sex 14:00', 'livre'."""
    text = resets_text(resets_at, now)
    if text == "Renovado":
        return "livre"
    return text.removeprefix("Renova em ").removeprefix("Renova ").replace(",", "")


def ago(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return "nunca"
    now = time.time() if now is None else now
    delta = max(0, now - ts)
    if delta < 60:
        return "agora mesmo"
    if delta < 3600:
        return f"há {int(delta // 60)} min"
    if delta < 86400:
        return f"há {int(delta // 3600)} h"
    days = int(delta // 86400)
    return "ontem" if days == 1 else f"há {days} dias"


def long_date(day: date) -> str:
    return f"{day.day} de {MONTHS[day.month - 1]}"


def weekday_name(day: date) -> str:
    name = WEEKDAYS[day.weekday()]
    return name.capitalize() + ("" if day.weekday() >= 5 else "-feira")


def month_title(year: int, month: int) -> str:
    return f"{MONTHS[month - 1].capitalize()} {year}"


def clock(moment: datetime, h24: bool) -> str:
    if h24:
        return moment.strftime("%H:%M")
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment:%M} {'PM' if moment.hour >= 12 else 'AM'}"
