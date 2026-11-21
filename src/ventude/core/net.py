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


_CHALLENGE_MARKERS = (
    "just a moment",
    "cf-chl",
    "challenge-platform",
    "cf_chl_opt",
    "attention required! | cloudflare",
    "cf-mitigated",
)


class NetError(Exception):
    def __init__(self, message: str, status: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body

    @property
    def challenge(self) -> bool:
        """O Cloudflare barrou a requisição (desafio "Just a moment...")."""
        if self.status not in (403, 429, 503):
            return False
        body = (self.body or "").lower()
        return any(marker in body for marker in _CHALLENGE_MARKERS)


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
    data: bytes | None = None,
    timeout: float = 15,
    insecure: bool = False,
) -> bytes:
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"
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


# ---------- requisições com "cara de navegador" ----------
_browser_checked = False
_browser_ok = False


def browser_transport() -> str:
    """Qual transporte as requisições de sites (claude.ai, grok.com) usam."""
    global _browser_checked, _browser_ok
    if not _browser_checked:
        _browser_checked = True
        try:
            from curl_cffi import requests as _requests  # noqa: F401

            _browser_ok = True
        except Exception:  # noqa: BLE001
            _browser_ok = False
    return "curl_cffi" if _browser_ok else "urllib"


def browser_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    data: bytes | None = None,
    timeout: float = 15,
) -> bytes:
    """Requisição para sites atrás do Cloudflare (claude.ai, grok.com).

    O Python puro tem uma "assinatura" de TLS que o Cloudflare reconhece e bloqueia.
    Com o curl_cffi, a conexão fica igual à do Chrome. Sem ele, cai no urllib.
    `data` manda um corpo binário (ex.: gRPC-web) no lugar do JSON.
    """
    if browser_transport() != "curl_cffi":
        hdrs = {"User-Agent": BROWSER_UA, **(headers or {})}
        return request(method, url, headers=hdrs, json_body=json_body, data=data, timeout=timeout)
    from curl_cffi import requests as curl

    hdrs = {"Accept": "application/json, text/plain, */*", **(headers or {})}
    try:
        resp = curl.request(
            method,
            url,
            headers=hdrs,
            json=json_body,
            data=data,
            timeout=timeout,
            impersonate="chrome",
            allow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        if "timed out" in text.lower() or "timeout" in text.lower():
            raise NetError("tempo esgotado") from None
        raise NetError(text.split("\n")[0][:160] or "falha de conexão") from None
    if resp.status_code >= 400:
        body = ""
        try:
            body = resp.text[:600]
        except Exception:  # noqa: BLE001
            pass
        if resp.headers.get("cf-mitigated"):
            body = "cf-mitigated " + body
        raise NetError(f"HTTP {resp.status_code}", resp.status_code, body)
    return resp.content


def browser_json(method: str, url: str, **kwargs: Any) -> Any:
    return _decode(browser_request(method, url, **kwargs))
