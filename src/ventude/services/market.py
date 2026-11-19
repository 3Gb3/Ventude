"""Cotações pelo Yahoo Finance (sem chave), com histórico curto para o gráfico."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ventude.core import net
from ventude.core.store import Store

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
TTL = 5 * 60


@dataclass
class Quote:
    symbol: str
    name: str
    price: float
    change_pct: float | None
    currency: str = ""
    history: list[float] = field(default_factory=list)


def parse_watchlist(raw: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for part in raw.split(","):
        symbol, _, name = part.strip().partition(":")
        symbol = symbol.strip().upper()
        if symbol and all(s != symbol for s, _ in out):
            out.append((symbol, name.strip() or symbol))
    return out


def dump_watchlist(items: list[tuple[str, str]]) -> str:
    return ",".join(f"{s}:{n}" if n and n != s else s for s, n in items)


def parse_chart(data: Any, symbol: str, name: str) -> Quote | None:
    result = ((data or {}).get("chart") or {}).get("result") or []
    if not result:
        return None
    meta = result[0].get("meta") or {}
    quotes = ((result[0].get("indicators") or {}).get("quote") or [{}])[0]
    closes = [float(c) for c in quotes.get("close") or [] if c is not None]
    price = meta.get("regularMarketPrice")
    if price is None:
        if not closes:
            return None
        price = closes[-1]
    price = float(price)
    previous = closes[-2] if len(closes) >= 2 else meta.get("chartPreviousClose")
    change = (price - float(previous)) / float(previous) * 100 if previous else None
    if closes:
        closes[-1] = price
    return Quote(symbol, name, price, change, meta.get("currency") or "", closes[-30:])


def format_price(quote: Quote) -> str:
    value = quote.price
    if quote.symbol.startswith("^"):
        return f"{value:,.0f}".replace(",", ".") + " pts"
    decimals = 2 if value >= 1 else 4
    text = f"{value:,.{decimals}f}".replace(",", "§").replace(".", ",").replace("§", ".")
    prefix = {"BRL": "R$ ", "USD": "US$ ", "EUR": "€ "}.get(quote.currency, "")
    if quote.symbol.endswith("=X"):  # câmbio: BRL=X é o dólar em reais
        prefix = "R$ "
    return prefix + text


class MarketService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def quote(self, symbol: str, name: str, *, force: bool = False) -> Quote | None:
        key = f"mkt:{symbol}"
        if not force and (cached := self.store.cache_get(key)):
            return Quote(**cached)
        data = net.get_json(
            CHART.format(symbol=symbol),
            params={"interval": "1d", "range": "1mo"},
            headers={"User-Agent": net.BROWSER_UA},
            timeout=12,
        )
        quote = parse_chart(data, symbol, name)
        if quote:
            self.store.cache_set(key, asdict(quote), TTL)
        return quote

    def quotes(self, watchlist: list[tuple[str, str]], *, force: bool = False) -> list[Quote]:
        out: list[Quote] = []
        for symbol, name in watchlist:
            try:
                quote = self.quote(symbol, name, force=force)
            except net.NetError:
                quote = None
            if quote:
                out.append(quote)
        return out
