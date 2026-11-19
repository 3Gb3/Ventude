"""HTTP mínimo sobre a biblioteca padrão (sem dependências externas)."""

from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

USER_AGENT = "Ventude/1.0 (+desktop)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class NetError(Exception):
    def __init__(self, message: str, status: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _opener(url: str, insecure: bool) -> urllib.request.OpenerDirector:
    host = urllib.parse.urlsplit(url).hostname or ""
    handlers: list[Any] = []
    if host in _LOOPBACK:
        handlers.append(urllib.request.ProxyHandler({}))
        if insecure:
            # Só servidores locais (ex.: Antigravity) usam certificado autoassinado.
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    timeout: float = 15,
    insecure: bool = False,
) -> bytes:
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"
    data = None
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json, text/xml, */*"}
    hdrs.update(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with _opener(url, insecure).open(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:600]
        except Exception:  # noqa: BLE001
            pass
        raise NetError(f"HTTP {exc.code}", exc.code, body) from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise NetError("tempo esgotado") from None
        raise NetError(str(reason)) from None
    except TimeoutError:
        raise NetError("tempo esgotado") from None
    except (OSError, ValueError) as exc:
        raise NetError(str(exc)) from None


def get_json(url: str, **kwargs: Any) -> Any:
    return _decode(request("GET", url, **kwargs))


def post_json(url: str, body: Any, **kwargs: Any) -> Any:
    return _decode(request("POST", url, json_body=body, **kwargs))


def get_text(url: str, **kwargs: Any) -> str:
    return request("GET", url, **kwargs).decode("utf-8", "replace")


def _decode(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8", "replace") or "null")
    except json.JSONDecodeError:
        raise NetError("resposta inválida") from None
