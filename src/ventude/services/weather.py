"""Clima pelo Open-Meteo (gratuito, sem chave)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from ventude.core import net
from ventude.core.store import Store

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
TTL = 20 * 60
WMO = {
    0: "Céu limpo",
    1: "Quase limpo",
    2: "Parcialmente nublado",
    3: "Nublado",
    45: "Neblina",
    48: "Neblina com geada",
    51: "Garoa fraca",
    53: "Garoa",
    55: "Garoa forte",
    56: "Garoa congelante",
    57: "Garoa congelante",
    61: "Chuva fraca",
    63: "Chuva",
    65: "Chuva forte",
    66: "Chuva congelante",
    67: "Chuva congelante",
    71: "Neve fraca",
    73: "Neve",
    75: "Neve forte",
    77: "Grãos de neve",
    80: "Pancadas fracas",
    81: "Pancadas de chuva",
    82: "Pancadas fortes",
    85: "Pancadas de neve",
    86: "Pancadas de neve",
    95: "Tempestade",
    96: "Tempestade com granizo",
    99: "Tempestade com granizo",
}


def describe(code: int) -> str:
    return WMO.get(int(code), "—")


def icon_for(code: int, is_day: bool = True) -> str:
    code = int(code)
    if code == 0:
        return "sun" if is_day else "moon"
    if code in (1, 2):
        return "cloud-sun" if is_day else "cloud-moon"
    if code in (45, 48):
        return "fog"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "storm"
    if 51 <= code <= 82:
        return "rain"
    return "cloud"


@dataclass(frozen=True)
class Place:
    name: str
    country: str
    lat: float
    lon: float
    admin1: str = ""

    @property
    def label(self) -> str:
        return ", ".join(p for p in (self.name, self.admin1, self.country) if p)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> Place | None:
        try:
            data = json.loads(raw) if raw else None
            return cls(**data) if isinstance(data, dict) else None
        except (ValueError, TypeError):
            return None


@dataclass
class Forecast:
    place: str
    temp: float
    feels: float
    humidity: int
    wind: float
    code: int
    is_day: bool
    days: list[dict[str, Any]] = field(default_factory=list)

    @property
    def description(self) -> str:
        return describe(self.code)

    @property
    def icon(self) -> str:
        return icon_for(self.code, self.is_day)


def parse_forecast(data: dict[str, Any], place_label: str) -> Forecast:
    cur = data["current"]
    daily = data.get("daily") or {}
    days = []
    for i, day in enumerate(daily.get("time") or []):
        days.append(
            {
                "date": day,
                "code": int(daily["weather_code"][i]),
                "max": float(daily["temperature_2m_max"][i]),
                "min": float(daily["temperature_2m_min"][i]),
                "rain": int((daily.get("precipitation_probability_max") or [0] * 99)[i] or 0),
            }
        )
    return Forecast(
        place=place_label,
        temp=float(cur["temperature_2m"]),
        feels=float(cur["apparent_temperature"]),
        humidity=int(cur["relative_humidity_2m"]),
        wind=float(cur["wind_speed_10m"]),
        code=int(cur["weather_code"]),
        is_day=bool(cur.get("is_day", 1)),
        days=days,
    )


class WeatherService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def search(self, query: str, limit: int = 6) -> list[Place]:
        query = query.strip()
        if len(query) < 2:
            return []
        data = net.get_json(
            GEOCODE, params={"name": query, "count": limit, "language": "pt", "format": "json"}
        )
        return [
            Place(
                name=i["name"],
                country=i.get("country", "") or "",
                lat=float(i["latitude"]),
                lon=float(i["longitude"]),
                admin1=i.get("admin1", "") or "",
            )
            for i in (data or {}).get("results") or []
        ]

    def forecast(self, place: Place, *, force: bool = False) -> Forecast:
        key = f"wx:{place.lat:.3f},{place.lon:.3f}"
        if not force and (cached := self.store.cache_get(key)):
            return Forecast(**cached)
        data = net.get_json(
            FORECAST,
            params={
                "latitude": place.lat,
                "longitude": place.lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "weather_code,wind_speed_10m,is_day",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 7,
            },
        )
        forecast = parse_forecast(data, place.label)
        self.store.cache_set(key, asdict(forecast), TTL)
        return forecast
