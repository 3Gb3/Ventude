"""Sites conhecidos da busca: digitar "youtube", "gmail" ou "netflix" oferece abrir no navegador.

Também reconhece endereços digitados ("terra.com.br", "github.com/user", "localhost:3000").
Só monta links; quem abre é o navegador padrão (`webbrowser`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Site:
    name: str
    url: str
    words: tuple[str, ...] = ()  # apelidos ("yt", "zap"...)

    @property
    def host(self) -> str:
        return re.sub(r"^https?://(www\.)?", "", self.url).rstrip("/")


SITES: tuple[Site, ...] = (
    Site("YouTube", "https://www.youtube.com", ("yt", "videos")),
    Site("YouTube Music", "https://music.youtube.com", ("ytmusic",)),
    Site("Google", "https://www.google.com", ("pesquisa",)),
    Site("Gmail", "https://mail.google.com", ("email", "e-mail")),
    Site("Google Drive", "https://drive.google.com", ("drive",)),
    Site("Google Maps", "https://maps.google.com", ("maps", "mapa", "mapas")),
    Site("Google Agenda", "https://calendar.google.com", ("calendar",)),
    Site("Google Fotos", "https://photos.google.com", ("photos",)),
    Site("Google Tradutor", "https://translate.google.com", ("tradutor", "translate")),
    Site("Google Docs", "https://docs.google.com", ("docs",)),
    Site("WhatsApp Web", "https://web.whatsapp.com", ("whatsapp", "zap", "wpp")),
    Site("Instagram", "https://www.instagram.com", ("insta", "ig")),
    Site("Facebook", "https://www.facebook.com", ("face", "fb")),
    Site("X (Twitter)", "https://x.com", ("twitter",)),
    Site("TikTok", "https://www.tiktok.com", ()),
    Site("Reddit", "https://www.reddit.com", ()),
    Site("LinkedIn", "https://www.linkedin.com", ()),
    Site("Pinterest", "https://www.pinterest.com", ()),
    Site("Threads", "https://www.threads.net", ()),
    Site("Bluesky", "https://bsky.app", ()),
    Site("Discord", "https://discord.com/app", ()),
    Site("Twitch", "https://www.twitch.tv", ()),
    Site("Kick", "https://kick.com", ()),
    Site("Netflix", "https://www.netflix.com", ()),
    Site("Prime Video", "https://www.primevideo.com", ("amazon prime",)),
    Site("Disney+", "https://www.disneyplus.com", ("disney",)),
    Site("Max", "https://www.max.com", ("hbo",)),
    Site("Globoplay", "https://globoplay.globo.com", ()),
    Site("Crunchyroll", "https://www.crunchyroll.com", ("anime",)),
    Site("Spotify", "https://open.spotify.com", ()),
    Site("Deezer", "https://www.deezer.com", ()),
    Site("ChatGPT", "https://chatgpt.com", ("openai", "gpt")),
    Site("Claude", "https://claude.ai", ("anthropic",)),
    Site("Gemini", "https://gemini.google.com", ()),
    Site("Perplexity", "https://www.perplexity.ai", ()),
    Site("Grok", "https://grok.com", ()),
    Site("GitHub", "https://github.com", ("git",)),
    Site("Stack Overflow", "https://stackoverflow.com", ()),
    Site("Vercel", "https://vercel.com/dashboard", ()),
    Site("Figma", "https://www.figma.com", ()),
    Site("Canva", "https://www.canva.com", ()),
    Site("Notion", "https://www.notion.so", ()),
    Site("Wikipedia", "https://pt.wikipedia.org", ("wiki",)),
    Site("Outlook", "https://outlook.live.com", ("hotmail",)),
    Site("Microsoft 365", "https://www.office.com", ("office",)),
    Site("OneDrive", "https://onedrive.live.com", ()),
    Site("Amazon", "https://www.amazon.com.br", ()),
    Site("Mercado Livre", "https://www.mercadolivre.com.br", ("ml",)),
    Site("Shopee", "https://shopee.com.br", ()),
    Site("AliExpress", "https://pt.aliexpress.com", ()),
    Site("Magalu", "https://www.magazineluiza.com.br", ("magazine luiza",)),
    Site("iFood", "https://www.ifood.com.br", ()),
    Site("Steam", "https://store.steampowered.com", ()),
    Site("Epic Games", "https://store.epicgames.com", ("epic",)),
    Site("g1", "https://g1.globo.com", ("globo", "noticias", "notícias")),
    Site("UOL", "https://www.uol.com.br", ()),
    Site("Terra", "https://www.terra.com.br", ()),
    Site("ge", "https://ge.globo.com", ("globo esporte",)),
    Site("Nubank", "https://app.nubank.com.br", ()),
    Site("Gov.br", "https://www.gov.br", ("gov",)),
)

_DOMAIN = re.compile(
    r"^(?:https?://)?(?:localhost|\d{1,3}(?:\.\d{1,3}){3}|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,})"
    r"(?::\d{2,5})?(?:[/?#]\S*)?$",
    re.IGNORECASE,
)


def match(text: str, limit: int = 3) -> list[Site]:
    """Sites cujo nome ou apelido começa com o texto (mínimo 2 letras)."""
    lowered = text.strip().lower()
    if len(lowered) < 2:
        return []
    ranked: list[tuple[int, int, Site]] = []
    for order, site in enumerate(SITES):
        name = site.name.lower()
        host = site.host.lower()
        if name == lowered or lowered in site.words:
            rank = 0
        elif name.startswith(lowered) or host.startswith(lowered):
            rank = 1
        elif any(w.startswith(lowered) for w in site.words) or any(
            part.startswith(lowered) for part in name.split()
        ):
            rank = 2
        else:
            continue
        ranked.append((rank, order, site))
    ranked.sort(key=lambda t: (t[0], t[1]))
    return [site for _, _, site in ranked[:limit]]


def as_url(text: str) -> str:
    """'terra.com.br' -> 'https://terra.com.br'; '' se o texto não parece um endereço."""
    raw = text.strip()
    if not raw or " " in raw or not _DOMAIN.match(raw):
        return ""
    if raw.lower().startswith(("http://", "https://")):
        return raw
    if raw.lower().startswith(("localhost", "127.", "192.168.", "10.")):
        return f"http://{raw}"
    return f"https://{raw}"
