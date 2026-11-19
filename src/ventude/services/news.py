"""Manchetes por RSS público (sem chave)."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
from html import unescape

from ventude.core import net
from ventude.core.store import Store
from ventude.core.timefmt import parse_instant

FEEDS: dict[str, str] = {
    "G1": "https://g1.globo.com/rss/g1/",
    "BBC Brasil": "https://feeds.bbci.co.uk/portuguese/rss.xml",
    "Tecnoblog": "https://tecnoblog.net/feed/",
}
TTL = 15 * 60
ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass(frozen=True)
class NewsItem:
    title: str
    link: str
    source: str
    published: float = 0.0
    summary: str = ""


def _clean(raw: str | None) -> str:
    if not raw:
        return ""
    out, inside = [], False
    for ch in raw:
        if ch == "<":
            inside = True
        elif ch == ">":
            inside = False
        elif not inside:
            out.append(ch)
    return " ".join(unescape("".join(out)).split())


def _rss_date(text: str) -> float:
    if not text:
        return 0.0
    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return parse_instant(text) or 0.0


def parse_feed(xml_text: str, source: str, limit: int = 15) -> list[NewsItem]:
    root = ET.fromstring(xml_text)
    items: list[NewsItem] = []
    for node in root.findall("./channel/item"):
        title = _clean(node.findtext("title"))
        link = (node.findtext("link") or "").strip()
        if title and link:
            items.append(
                NewsItem(
                    title,
                    link,
                    source,
                    _rss_date(node.findtext("pubDate") or ""),
                    _clean(node.findtext("description"))[:240],
                )
            )
    for node in root.findall(f"{ATOM}entry"):
        title = _clean(node.findtext(f"{ATOM}title"))
        link_el = node.find(f"{ATOM}link[@rel='alternate']")
        if link_el is None:
            link_el = node.find(f"{ATOM}link")
        link = link_el.get("href", "") if link_el is not None else ""
        if title and link:
            published = node.findtext(f"{ATOM}updated") or node.findtext(f"{ATOM}published")
            items.append(
                NewsItem(
                    title,
                    link,
                    source,
                    parse_instant(published) or 0.0,
                    _clean(node.findtext(f"{ATOM}summary"))[:240],
                )
            )
    return items[:limit]


class NewsService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def fetch(self, sources: list[str], *, force: bool = False) -> list[NewsItem]:
        everything: list[NewsItem] = []
        for name in sources:
            url = FEEDS.get(name)
            if not url:
                continue
            key = f"news:{name}"
            cached = None if force else self.store.cache_get(key)
            if cached is not None:
                everything.extend(NewsItem(**i) for i in cached)
                continue
            try:
                items = parse_feed(net.get_text(url, timeout=12), name)
            except (net.NetError, ET.ParseError):
                stale = self.store.cache_get(key, allow_expired=True) or []
                everything.extend(NewsItem(**i) for i in stale)
                continue
            self.store.cache_set(key, [asdict(i) for i in items], TTL)
            everything.extend(items)
        now = time.time()
        return sorted(everything, key=lambda i: i.published or now - 86400 * 30, reverse=True)
