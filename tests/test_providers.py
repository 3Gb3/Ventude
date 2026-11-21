import base64
import json
import sqlite3
import time

import pytest

from ventude.core import net
from ventude.core.config import Settings
from ventude.limits import codex, copilot, cursor
from ventude.limits.model import FetchContext, ProviderError


def ctx(home, mode="auto", secrets=None):
    values = secrets or {}
    return FetchContext(mode, home, lambda name: values.get(name, ""), None)


def jwt(**claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{body}.sig"


# ---------- Codex ----------
WHAM = {
    "plan_type": "plus",
    "rate_limit": {
        "primary_window": {"used_percent": 42, "limit_window_seconds": 18000, "reset_after_seconds": 3600},
        "secondary_window": {"used_percent": 12.5, "limit_window_seconds": 604800, "reset_at": 1893492000},
    },
}


def test_codex_parse_windows():
    windows, plan = codex.parse_usage(WHAM, now=1000)
    assert [(w.key, w.label, w.used) for w in windows] == [
        ("session", "Sessão · 5h", 42.0),
        ("weekly", "Semana", 12.5),
    ]
    assert windows[0].resets_at == 4600 and windows[1].resets_at == 1893492000 and plan == "Plus"


def test_codex_fetch_from_cli_login(home, monkeypatch):
    folder = home / ".codex"
    folder.mkdir()
    token = jwt(exp=time.time() + 3600)
    (folder / "auth.json").write_text(json.dumps({"tokens": {"access_token": token, "account_id": "acc"}}))
    seen = {}

    def fake(method, url, **kw):
        seen.update(url=url, headers=kw["headers"])
        return WHAM

    monkeypatch.setattr(net, "browser_json", fake)
    report = codex.fetch(ctx(home))
    assert report.state == "ok" and report.source == "Codex CLI"
    assert seen["headers"]["Authorization"] == f"Bearer {token}"
    assert seen["headers"]["ChatGPT-Account-Id"] == "acc"
    assert codex.fetch(ctx(home, "off")).state == "off"


def test_codex_from_chatgpt_curl(home, monkeypatch):
    calls = []

    def fake(method, url, **kw):
        calls.append((url, kw["headers"]))
        if url == codex.SESSION_URL:
            return {"accessToken": "tok", "account": {"id": "acc2"}}
        return WHAM

    monkeypatch.setattr(net, "browser_json", fake)
    curl = "curl 'https://chatgpt.com/' -b '__Secure-next-auth.session-token=abc' -H 'user-agent: UA'"
    report = codex.fetch(ctx(home, secrets={"codex_session": curl}))
    assert report.source == "chatgpt.com"
    assert calls[0][1]["Cookie"] == "__Secure-next-auth.session-token=abc"
    assert calls[1][1]["Authorization"] == "Bearer tok" and calls[1][1]["ChatGPT-Account-Id"] == "acc2"
    assert codex.fetch(ctx(home)).state == "setup"


# ---------- Cursor ----------
def make_state(path, token):
    path.parent.mkdir(parents=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
    con.executemany(
        "INSERT INTO ItemTable VALUES (?, ?)",
        [
            ("cursorAuth/accessToken", token),
            ("cursorAuth/cachedEmail", "ana@example.com"),
            ("cursorAuth/stripeMembershipType", "pro"),
        ],
    )
    con.commit()
    con.close()


def test_cursor_reads_local_login_and_summary(home, monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(appdata))
    token = jwt(sub="auth0|user_123", exp=time.time() + 3600)
    make_state(appdata / "Cursor" / "User" / "globalStorage" / "state.vscdb", token)
    assert cursor.read_login(home) == (token, "ana@example.com", "pro")
    seen = []

    def fake(method, url, **kw):
        seen.append((url, kw["headers"]["Cookie"]))
        return {
            "billingCycleEnd": "2030-01-01T00:00:00Z",
            "membershipType": "pro",
            "individualUsage": {
                "plan": {"enabled": True, "used": 1500, "limit": 2000},
                "onDemand": {"enabled": False, "used": 0, "limit": None},
            },
        }

    monkeypatch.setattr(net, "browser_json", fake)
    report = cursor.fetch(ctx(home))
    assert report.state == "ok" and report.plan == "Pro" and report.account == "ana@example.com"
    assert seen[0][1] == f"WorkosCursorSessionToken=user_123%3A%3A{token}"
    plan = report.windows[0]
    assert plan.key == "plan" and plan.used == 75.0 and "US$ 15,00 de US$ 20,00" == plan.detail
    assert len(report.windows) == 1  # uso extra desligado não aparece


def test_cursor_old_request_format_and_pasted_cookie(home, monkeypatch):
    def fake(method, url, **kw):
        if url == cursor.SUMMARY_URL:
            return {}
        assert url.endswith("?user=user_9")
        return {"gpt-4": {"numRequests": 125, "maxRequestUsage": 500}, "startOfMonth": "2030-01-05T00:00:00Z"}

    monkeypatch.setattr(net, "browser_json", fake)
    report = cursor.fetch(ctx(home, secrets={"cursor_cookie": "WorkosCursorSessionToken=user_9%3A%3Aabc"}))
    assert report.source == "cursor.com"
    assert report.windows[0].label == "Requisições rápidas" and report.windows[0].used == 25.0
    with pytest.raises(ProviderError, match="WorkosCursorSessionToken"):
        cursor.fetch(ctx(home, secrets={"cursor_cookie": "foo=bar"}))
    assert cursor.fetch(ctx(home)).state == "setup"


# ---------- Copilot ----------
PAID = {
    "copilot_plan": "individual",
    "quota_reset_date": "2030-02-01",
    "quota_snapshots": {
        "chat": {"unlimited": True},
        "completions": {"unlimited": True},
        "premium_interactions": {
            "entitlement": 300,
            "remaining": 75,
            "percent_remaining": 25.0,
            "unlimited": False,
        },
    },
}


def test_copilot_parse_paid_and_free():
    windows, plan = copilot.parse_user(PAID)
    assert plan == "Pro" and [(w.key, w.used) for w in windows] == [("premium", 75.0)]
    assert windows[0].detail == "75 de 300 restantes" and windows[0].resets_at
    free = {
        "copilot_plan": "free",
        "monthly_quotas": {"chat": 50, "completions": 2000},
        "limited_user_quotas": {"chat": 40, "completions": 1000},
        "limited_user_reset_date": "2030-02-01",
    }
    windows, plan = copilot.parse_user(free)
    assert plan == "Free" and [(w.key, w.used) for w in windows] == [("chat", 20.0), ("completions", 50.0)]


def test_copilot_fetch_with_local_token(home, monkeypatch, tmp_path):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    (local / "github-copilot").mkdir(parents=True)
    (local / "github-copilot" / "apps.json").write_text(
        json.dumps({"github.com:Iv1.abc": {"user": "ana", "oauth_token": "ghu_x"}})
    )

    def fake(url, **kw):
        assert kw["headers"]["Authorization"] == "token ghu_x"
        return PAID

    monkeypatch.setattr(net, "get_json", fake)
    report = copilot.fetch(ctx(home))
    assert report.state == "ok" and report.account == "ana" and report.windows[0].key == "premium"

    def denied(url, **kw):
        raise net.NetError("HTTP 401", 401)

    monkeypatch.setattr(net, "get_json", denied)
    with pytest.raises(ProviderError, match="token"):
        copilot.fetch(ctx(home))


# ---------- ajustes ----------
def test_ring_choice_settings():
    s = Settings()
    assert s.ring_enabled("claude") and not s.ring_enabled("cursor")  # Cursor desligado por padrão
    s = s.copy(cursor_mode="auto", ring_providers=s.with_ring("cursor", True))
    assert s.ring_enabled("cursor") and s.ring_providers.endswith("cursor")
    s = s.copy(ring_providers=s.with_ring("claude", False))
    assert not s.ring_enabled("claude") and s.provider_mode("claude") == "auto"
    assert Settings.from_dict({"codex_mode": "bogus"}).codex_mode == "off"


def test_firefox_windows_curl_exe_is_parsed():
    from ventude.limits.web import parse_pasted

    pasted = parse_pasted(
        'curl.exe "https://grok.com/monitoring" --compressed -X POST '
        '-H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:160.0) Gecko/20100101 Firefox/160.0" '
        '-H "Accept: */*" -H "Referer: https://grok.com/" '
        '-H "Cookie: sso=abc; sso-rw=def; cf_clearance=x" --data-raw "{}"'
    )
    assert pasted.cookie_value("sso") == "abc" and pasted.cookie_value("sso-rw") == "def"
    assert pasted.user_agent.endswith("Firefox/160.0")
    assert "curl" not in pasted.cookie and pasted.headers["referer"] == "https://grok.com/"
