"""Lê o que o usuário cola do navegador: cookie puro, cabeçalhos ou "Copiar como cURL".

Aceita o que o Chrome, o Edge e o Firefox geram (cURL bash e cmd, PowerShell e a
lista de cabeçalhos). Guardar o comando inteiro ajuda com o Cloudflare: o
User-Agent e os cabeçalhos extras do navegador vão junto na consulta.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# Cabeçalhos que não devem ser repetidos (o transporte calcula sozinho).
_SKIP = {
    "cookie",
    "content-length",
    "host",
    "accept-encoding",
    "connection",
    "priority",
}


@dataclass
class Pasted:
    cookie: str = ""
    user_agent: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    models: list[str] = field(default_factory=list)

    def cookie_value(self, name: str) -> str:
        for part in self.cookie.split(";"):
            key, _, value = part.strip().partition("=")
            if key.strip() == name:
                return value.strip()
        return ""


def _unescape_cmd(text: str) -> str:
    """'Copiar como cURL (cmd)' escapa aspas e símbolos com ^."""
    if '^"' not in text and "^%" not in text:
        return text
    return re.sub(r"\^(.)", r"\1", text, flags=re.S)


def _quoted(text: str, flags: str) -> list[str]:
    """Valores entre aspas depois de -H/-b etc. (aspas simples ou duplas)."""
    pattern = rf"(?:{flags})\s+(?:\$)?('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")"
    values = []
    for match in re.finditer(pattern, text):
        raw = match.group(1)[1:-1]
        values.append(raw.replace("\\'", "'").replace('\\"', '"'))
    return values


def parse_pasted(text: str) -> Pasted:
    text = (text or "").strip()
    out = Pasted()
    if not text:
        return out
    text = _unescape_cmd(text)
    lowered = text.lower()

    # "curl ..." (Chrome, Edge, bash) ou "curl.exe ..." (Firefox > Copiar como cURL (Windows))
    if re.match(r"curl(?:\.exe)?\s", lowered) or re.search(r"\ncurl(?:\.exe)?\s", lowered):
        for header in _quoted(text, r"-H|--header"):
            name, _, value = header.partition(":")
            name, value = name.strip().lower(), value.strip()
            if not name:
                continue
            if name == "cookie":
                out.cookie = value
            elif name == "user-agent":
                out.user_agent = value
            elif name not in _SKIP:
                out.headers[name] = value
        for cookie in _quoted(text, r"-b|--cookie"):
            out.cookie = cookie.strip()
        for body in _quoted(text, r"--data-raw|--data|--data-binary|-d"):
            out.models += _models_in(body)
        if agent := _quoted(text, r"-A|--user-agent"):
            out.user_agent = agent[0]
        return out

    if "new-object system.net.cookie" in lowered or "invoke-webrequest" in lowered:
        pairs = re.findall(r'System\.Net\.Cookie\(\s*"([^"]+)"\s*,\s*"([^"]*)"', text, re.IGNORECASE)
        out.cookie = "; ".join(f"{k}={v}" for k, v in pairs)
        if agent := re.search(r'-UserAgent\s+"([^"]+)"', text, re.IGNORECASE):
            out.user_agent = agent.group(1)
        for name, value in re.findall(r'"([A-Za-z0-9-]+)"\s*=\s*"([^"]*)"', text):
            if name.lower() not in _SKIP and name.lower() != "user-agent":
                out.headers[name.lower()] = value
        if body := re.search(r"-Body\s+'([^']*)'|-Body\s+\"((?:[^\"\\]|\\.)*)\"", text):
            out.models += _models_in(body.group(1) or body.group(2) or "")
        return out

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 1 and all(":" in line for line in lines[:2]):  # lista de cabeçalhos
        for line in lines:
            name, _, value = line.partition(":")
            name, value = name.strip().lower(), value.strip()
            if name == "cookie":
                out.cookie = value
            elif name == "user-agent":
                out.user_agent = value
        return out

    single = " ".join(lines)
    if single.lower().startswith("cookie:"):
        single = single[7:].strip()
    out.cookie = single.strip().strip("'\"")
    return out


def _models_in(body: str) -> list[str]:
    try:
        data = json.loads(body)
    except ValueError:
        return []
    model = data.get("modelName") if isinstance(data, dict) else None
    return [model] if isinstance(model, str) and model else []


def jwt_claims(token: str) -> dict:
    """Lê o conteúdo (payload) de um JWT sem validar a assinatura: só para exp, sub etc."""
    import base64

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode()).decode("utf-8", "replace"))
    except (IndexError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
