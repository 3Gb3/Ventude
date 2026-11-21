import json
import time

import pytest

from ventude.core import net
from ventude.limits import antigravity, claude, grok
from ventude.limits.model import FetchContext, ProviderError, Report, Window

NO_SECRET = {}.get


def ctx(home, mode="auto", secrets=None, previous=None):
    values = secrets or {}
    return FetchContext(mode, home, lambda name: values.get(name, ""), previous)


# ---------- modelo ----------
def test_window_used_resets_to_zero_after_renewal():
    w = Window("session", "Sessão", 80, resets_at=100)
    assert w.used_at(50) == 80
    assert w.used_at(150) == 0


def test_headline_picks_tightest_and_prefers_gemini():
    r = Report("claude", "ok", [Window("a", "A", 10), Window("b", "B", 70), Window("c", "C", None)])
    assert r.headline().key == "b"
    ag = Report("antigravity", "ok", [Window("x", "Claude Opus", 95), Window("y", "Gemini 3 Pro", 20)])
    assert ag.headline().key == "y"
    assert Report.from_dict(ag.to_dict()) == ag


# ---------- Claude ----------
USAGE = {
    "five_hour": {"utilization": 6.0, "resets_at": "2025-11-04T04:59:59.943648+00:00"},
    "seven_day": {"utilization": 35.0, "resets_at": "2025-11-06T03:59:59.943679+00:00"},
    "seven_day_oauth_apps": None,
    "seven_day_opus": {"utilization": 0.0, "resets_at": None},
    "seven_day_sonnet": {"utilization": 12.0, "resets_at": "2025-11-06T03:59:59+00:00"},
    "extra_usage": {"is_enabled": True, "utilization": 24.5},
}


def test_claude_parse_usage():
    windows = claude.parse_usage(USAGE)
    assert [w.key for w in windows] == ["session", "weekly", "weekly_sonnet", "extra"]
    assert windows[0].used == 6.0 and windows[0].resets_at == pytest.approx(1762232399.943648)


def test_claude_plan_labels():
    assert claude.plan_label("max", "default_claude_max_20x") == "Max 20x"
    assert claude.plan_label("max", "default_claude_max_5x") == "Max 5x"
    assert claude.plan_label("pro") == "Pro"
    assert claude.plan_label("") == ""


def write_claude(home, expires_ms):
    folder = home / ".claude"
    folder.mkdir()
    (folder / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "tok",
                    "refreshToken": "ref",
                    "expiresAt": expires_ms,
                    "subscriptionType": "max",
                    "rateLimitTier": "default_claude_max_5x",
                }
            }
        )
    )


def test_claude_fetch_from_code(home, monkeypatch):
    write_claude(home, (time.time() + 3600) * 1000)
    seen = {}

    def fake_get(url, **kw):
        seen.update(url=url, headers=kw["headers"])
        return USAGE

    monkeypatch.setattr(net, "get_json", fake_get)
    report = claude.fetch(ctx(home))
    assert report.state == "ok" and report.plan == "Max 5x" and report.source == "Claude Code"
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["headers"]["anthropic-beta"] == claude.OAUTH_BETA
    raw = json.loads((home / ".claude" / ".credentials.json").read_text())
    assert raw["claudeAiOauth"]["refreshToken"] == "ref"  # nunca reescreve o login


def test_claude_expired_token_is_renewed_and_saved(home, monkeypatch):
    write_claude(home, (time.time() - 10) * 1000)
    path = home / ".claude" / ".credentials.json"
    raw = json.loads(path.read_text())
    raw["mcpOAuth"] = {"keep": True}
    path.write_text(json.dumps(raw))
    posts, gets = [], []

    def fake_post(url, body, **kw):
        posts.append((url, body))
        return {"access_token": "tok2", "refresh_token": "ref2", "expires_in": 28800, "scope": "a b"}

    def fake_get(url, **kw):
        gets.append(kw["headers"]["Authorization"])
        return USAGE

    monkeypatch.setattr(net, "post_json", fake_post)
    monkeypatch.setattr(net, "get_json", fake_get)
    report = claude.fetch(ctx(home))
    assert report.state == "ok" and gets == ["Bearer tok2"]
    expected = {"grant_type": "refresh_token", "refresh_token": "ref", "client_id": claude.CLIENT_ID}
    assert posts[0][1] == expected
    saved = json.loads(path.read_text())
    oauth = saved["claudeAiOauth"]
    assert oauth["accessToken"] == "tok2" and oauth["refreshToken"] == "ref2"
    assert oauth["expiresAt"] > time.time() * 1000 and oauth["scopes"] == ["a", "b"]
    assert oauth["subscriptionType"] == "max" and saved["mcpOAuth"] == {"keep": True}
    assert not list(path.parent.glob("*.ventude-tmp"))


def test_claude_expired_without_refresh(home, monkeypatch):
    write_claude(home, (time.time() - 10) * 1000)
    with pytest.raises(ProviderError, match="expirou"):
        claude.fetch(FetchContext("auto", home, NO_SECRET, None, {"claude_refresh": False}))

    def invalid(url, body, **kw):
        raise net.NetError("HTTP 400", 400, '{"error": "invalid_grant"}')

    monkeypatch.setattr(net, "post_json", invalid)
    with pytest.raises(ProviderError, match="/login"):
        claude.fetch(ctx(home))


def test_claude_refresh_tries_next_endpoint(home, monkeypatch):
    write_claude(home, (time.time() - 10) * 1000)
    urls = []

    def fake_post(url, body, **kw):
        urls.append(url)
        if len(urls) == 1:
            raise net.NetError("HTTP 404", 404, "not found")
        return {"access_token": "tok3", "expires_in": 3600}

    monkeypatch.setattr(net, "post_json", fake_post)
    monkeypatch.setattr(net, "get_json", lambda url, **kw: USAGE)
    assert claude.fetch(ctx(home)).state == "ok"
    assert urls == list(claude.TOKEN_URLS)
    oauth = json.loads((home / ".claude" / ".credentials.json").read_text())["claudeAiOauth"]
    assert oauth["accessToken"] == "tok3" and oauth["refreshToken"] == "ref"


def test_claude_401_message(home, monkeypatch):
    write_claude(home, (time.time() + 3600) * 1000)

    def boom(url, **kw):
        raise net.NetError("HTTP 401", 401)

    monkeypatch.setattr(net, "get_json", boom)
    with pytest.raises(ProviderError, match="login"):
        claude.fetch(FetchContext("auto", home, NO_SECRET, None, {"claude_refresh": False}))


def test_claude_web_fallback(home, monkeypatch):
    calls = []

    def fake_browser(method, url, **kw):
        calls.append((url, kw["headers"]["Cookie"], kw["headers"].get("User-Agent")))
        if url.endswith("/organizations"):
            return [{"uuid": "org1", "capabilities": ["chat", "claude_pro"]}]
        return USAGE

    monkeypatch.setattr(net, "browser_json", fake_browser)
    report = claude.fetch(ctx(home, secrets={"claude_session": "sessionKey=sk-ant-sid01-x; other=1"}))
    assert report.source == "claude.ai" and report.plan == "Pro"
    assert calls[0][1] == "sessionKey=sk-ant-sid01-x; other=1" and calls[0][2] is None
    assert calls[1][0].endswith("/organizations/org1/usage")
    curl = (
        "curl 'https://claude.ai/api/organizations' -H 'user-agent: Mozilla/5.0 Test' "
        "-b 'lastActiveOrg=org2; sessionKey=sk-ant-sid01-abc_DEF-1'"
    )
    calls.clear()

    def two_orgs(method, url, **kw):
        calls.append((url, kw["headers"]["Cookie"], kw["headers"].get("User-Agent")))
        if url.endswith("/organizations"):
            return [{"uuid": "org1", "capabilities": ["chat"]}, {"uuid": "org2", "capabilities": ["chat"]}]
        return USAGE

    monkeypatch.setattr(net, "browser_json", two_orgs)
    claude.fetch(ctx(home, "web", secrets={"claude_session": curl}))
    assert calls[0][2] == "Mozilla/5.0 Test" and "sessionKey=sk-ant-sid01-abc_DEF-1" in calls[0][1]
    assert calls[1][0].endswith("/organizations/org2/usage")
    assert claude.normalize_session_key("  sk-ant-sid01-zz-yy  ") == "sk-ant-sid01-zz-yy"


def test_claude_web_cloudflare_message(home, monkeypatch):
    def blocked(method, url, **kw):
        raise net.NetError("HTTP 403", 403, "<title>Just a moment...</title>")

    monkeypatch.setattr(net, "browser_json", blocked)
    with pytest.raises(ProviderError, match="Cloudflare"):
        claude.fetch(ctx(home, "web", secrets={"claude_session": "sk-ant-sid01-x"}))


def test_claude_setup_and_off(home):
    assert claude.fetch(ctx(home)).state == "setup"
    assert claude.fetch(ctx(home, "off")).state == "off"


# ---------- Antigravity ----------
WIN_CMD = (
    r'"C:\Users\Ana\AppData\Local\Programs\Antigravity\resources\app\extensions\antigravity\bin'
    r'\language_server_windows_x64.exe" --enable_lsp --csrf_token 3f2a-77 '
    r"--extension_server_port 53110 --app_data_dir antigravity"
)
STATUS = {
    "userStatus": {
        "email": "ana@example.com",
        "planStatus": {
            "planInfo": {"planName": "Pro", "monthlyPromptCredits": 50000},
            "availablePromptCredits": 40000,
        },
        "cascadeModelConfigData": {
            "clientModelConfigs": [
                {
                    "label": "Gemini 3 Pro (High)",
                    "quotaInfo": {"remainingFraction": 0.75, "resetTime": "2030-01-01T10:00:00Z"},
                },
                {
                    "label": "Gemini 3 Pro (Low)",
                    "quotaInfo": {"remainingFraction": 0.75, "resetTime": "2030-01-01T10:00:00Z"},
                },
                {"label": "Claude Sonnet 4.5", "quotaInfo": {"resetTime": "2030-01-01T08:00:00Z"}},
                {"label": "GPT-OSS 120B", "quotaInfo": {}},
            ]
        },
    }
}


def test_antigravity_parse_windows_process_and_flags():
    proc = antigravity.parse_process(1234, WIN_CMD, [53111, 53112])
    assert proc and proc.csrf == "3f2a-77" and proc.extension_port == 53110
    assert proc.ports == [53111, 53112]
    windsurf = WIN_CMD.replace("Antigravity", "Windsurf").replace("antigravity", "windsurf")
    assert antigravity.parse_process(1, windsurf) is None
    assert antigravity.parse_process(1, WIN_CMD.replace("--csrf_token 3f2a-77", "")) is None
    assert antigravity.parse_flag('x --csrf_token="a b"', "csrf_token") == "a b"


def test_antigravity_netstat_any_language():
    text = """
  Proto  Endereço local         Endereço externo       Estado         PID
  TCP    127.0.0.1:53111        0.0.0.0:0              ESCUTANDO       1234
  TCP    127.0.0.1:53111        127.0.0.1:60000        ESTABELECIDA    1234
  TCP    0.0.0.0:53112          0.0.0.0:0              LISTENING       1234
  TCP    [::1]:53113            [::]:0                 LISTENING       1234
  TCP    127.0.0.1:9999         0.0.0.0:0              LISTENING       999
"""
    assert antigravity.parse_netstat(text, 1234) == [53111, 53112, 53113]
    lsof = "language 1234 u 12u IPv4 TCP 127.0.0.1:53111 (LISTEN)\nx TCP 127.0.0.1:53112 (LISTEN)"
    assert antigravity.parse_lsof(lsof) == [53111, 53112]


def test_antigravity_extract_groups_models_and_zero_fraction():
    windows, plan, account = antigravity.extract(STATUS)
    labels = {w.label: w for w in windows}
    assert plan == "Pro" and account == "ana@example.com"
    assert labels["Gemini 3 Pro"].used == 25.0
    assert "High" in labels["Gemini 3 Pro"].detail
    assert labels["Claude Sonnet 4.5"].used == 100.0  # sem fração + com renovação = esgotado
    assert "GPT-OSS 120B" not in labels
    assert labels["Créditos de prompt"].used == 20.0
    assert windows[0].label.startswith("Gemini")


def test_antigravity_keeps_each_model_and_groups_codenames():
    def quota(fraction):
        return {"remainingFraction": fraction, "resetTime": "2030-01-01T10:00:00Z"}

    data = {
        "clientModelConfigs": [
            {"label": "Gemini 3 Pro (High)", "quotaInfo": quota(0.9)},
            {"label": "Gemini 3 Flash", "quotaInfo": quota(0.9)},
            {"label": "Claude Opus 4.5 (Thinking)", "quotaInfo": quota(0.9)},
            {"label": "GPT-OSS 120B (Medium)", "quotaInfo": quota(0.5)},
            {"modelName": "argon", "quotaInfo": quota(0.1)},
            {"modelName": "MODEL_PLACEHOLDER_M9", "quotaInfo": {"remainingFraction": 1}},
        ]
    }
    windows, _, _ = antigravity.extract(data)
    by_label = {w.label: w for w in windows}
    # mesma cota e mesmo horário não juntam mais modelos diferentes
    assert {"Gemini 3 Pro (High)", "Gemini 3 Flash", "Claude Opus 4.5 (Thinking)"} <= set(by_label)
    assert by_label["Argon"].group == "Outros" and by_label["Gemini 3 Flash"].group == "Gemini"
    assert by_label["M9"].group == "Outros"
    assert by_label["GPT-OSS 120B (Medium)"].group == "GPT"
    assert [w.group for w in windows] == ["Gemini", "Gemini", "Claude", "GPT", "Outros", "Outros"]
    report = Report("antigravity", "ok", windows)
    assert report.headline().group == "Gemini"  # o argon (90%) não vai para o anel
    assert report.headline(pinned="model:argon").label == "Argon"
    assert report.headline(pinned="model:sumiu").group == "Gemini"
    old = {"provider": "antigravity", "state": "ok", "windows": [{"key": "a", "label": "A", "used": 1}]}
    assert Report.from_dict(old).windows[0].group == ""


def test_antigravity_extract_summary_format():
    data = {
        "groups": [
            {
                "displayName": "Gemini",
                "buckets": [
                    {
                        "displayName": "Gemini Pro",
                        "remaining": {"remainingFraction": 0.4},
                        "resetTime": 1893492000,
                    },
                ],
            }
        ]
    }
    windows, _, _ = antigravity.extract(data)
    assert windows[0].label == "Gemini Pro" and windows[0].used == 60.0
    assert windows[0].resets_at == 1893492000


def test_antigravity_fetch_uses_discovered_port(monkeypatch, home):
    proc = antigravity.parse_process(1234, WIN_CMD, [53111])
    monkeypatch.setattr(antigravity, "find_processes", lambda: [proc])
    calls = []

    def fake_post(url, body, **kw):
        calls.append((url, kw["headers"]["X-Codeium-Csrf-Token"], kw["insecure"]))
        if url.startswith("https://127.0.0.1:53111"):
            return STATUS
        raise net.NetError("refused")

    monkeypatch.setattr(net, "post_json", fake_post)
    provider = antigravity.AntigravityProvider()
    report = provider.fetch(ctx(home))
    assert report.state == "ok" and report.source == "Antigravity"
    assert calls[0] == ("https://127.0.0.1:53111" + antigravity.RPC + "GetUserStatus", "3f2a-77", True)
    calls.clear()
    provider.fetch(ctx(home))  # reaproveita a porta encontrada
    assert len(calls) == 1


def test_antigravity_closed(monkeypatch, home):
    monkeypatch.setattr(antigravity, "find_processes", lambda: [])
    provider = antigravity.AntigravityProvider()
    assert provider.fetch(ctx(home)).state == "setup"
    previous = Report("antigravity", "ok", [Window("m", "Gemini", 10)])
    with pytest.raises(ProviderError, match="fechado"):
        provider.fetch(ctx(home, previous=previous))


# ---------- Grok ----------
def test_grok_cli_token_selection(home):
    folder = home / ".grok"
    folder.mkdir()
    (folder / "auth.json").write_text(
        json.dumps(
            {
                "https://accounts.x.ai/sign-in": {"key": "old"},
                "https://auth.x.ai::client": {
                    "key": "new",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "email": "a@x",
                },
            }
        )
    )
    token, expires, email = grok.read_cli_token(home)
    assert token == "new" and email == "a@x" and expires > time.time()


def test_grok_parse_billing_and_rate():
    w = grok.parse_billing(
        {"config": {"creditUsagePercent": 42.5, "currentPeriod": {"end": "2030-01-01T00:00:00Z"}}}
    )
    assert w.used == 42.5 and w.resets_at
    w2 = grok.parse_billing({"config": {"onDemandUsed": {"val": "25"}, "onDemandCap": {"val": "100"}}})
    assert w2.used == 25.0
    assert grok.parse_billing({"config": {}}) is None
    rate = grok.parse_rate(
        {"windowSizeSeconds": 7200, "remainingQueries": 24, "totalQueries": 25}, "grok-3", "Rápido", now=0
    )
    assert rate.used == 4.0 and "24 de 25" in rate.detail and "2h" in rate.detail and rate.resets_at is None
    empty = grok.parse_rate(
        {"remainingQueries": 0, "totalQueries": 25, "waitTimeSeconds": 600}, "x", "X", now=0
    )
    assert empty.used == 100.0 and empty.resets_at == 600
    nested = grok.parse_rate({"highEffortRateLimits": {"remainingQueries": 5, "totalQueries": 10}}, "x", "X")
    assert nested.used == 50.0


def test_grok_fetch_cli(monkeypatch, home):
    monkeypatch.setenv("GROK_OAUTH_TOKEN", "envtok")

    def fake_get(url, **kw):
        assert kw["headers"]["Authorization"] == "Bearer envtok"
        if url.endswith("/billing"):
            return {"config": {"creditUsagePercent": 10}}
        return {"subscription_tier_display": "SuperGrok"}

    monkeypatch.setattr(net, "get_json", fake_get)
    report = grok.fetch(ctx(home))
    assert report.source == "Grok CLI" and report.plan == "SuperGrok" and report.windows[0].used == 10


# Resposta real de GetGrokCreditsConfig (3% usado, ciclo semanal, Grok Bot 3%).
GROK_CREDITS = bytes.fromhex(
    "00000000520a500d00004040220c08fc8381d8061080dad6fd012a0c08fcf8a5d8061080dad6fd013a07"
    "08091500004040421e0802120c08fc8381d8061080dad6fd011a0c08fcf8a5d8061080dad6fd01580162"
    "006801800000000f677270632d7374617475733a300d0a"
)


def _no_credits(monkeypatch):
    def fail(method, url, **kw):
        raise net.NetError("HTTP 404", 404)

    monkeypatch.setattr(net, "browser_request", fail)


def test_grok_parse_credits():
    w = grok.parse_credits(GROK_CREDITS)
    assert w.used == 3.0 and w.label == "Limite semanal" and w.detail == "Grok Bot 3%"
    assert w.resets_at == 1795783804 and w.period == 7 * 86400
    assert grok.parse_credits(b"") is None
    assert grok.parse_credits(b"\x00\x00\x00\x00\x03\xff\xff\xff") is None


def test_grok_fetch_web_credits(monkeypatch, home):
    def fake_request(method, url, **kw):
        assert url == grok.WEB_CREDITS and kw["headers"]["Cookie"] == "sso=abc"
        assert kw["headers"]["Content-Type"] == "application/grpc-web+proto"
        return GROK_CREDITS

    def no_rate(*a, **kw):
        raise AssertionError("não deveria cair nas cotas por modelo")

    monkeypatch.setattr(net, "browser_request", fake_request)
    monkeypatch.setattr(net, "browser_json", no_rate)
    report = grok.fetch(ctx(home, "web", secrets={"grok_cookie": "Cookie: sso=abc"}))
    assert [w.key for w in report.windows] == ["credits"] and report.windows[0].used == 3.0


def test_grok_fetch_web_cookie(monkeypatch, home):
    _no_credits(monkeypatch)

    def fake_browser(method, url, **kw):
        assert method == "POST" and kw["headers"]["Cookie"] == "sso=abc"
        if kw["json_body"]["modelName"] == "grok-4-heavy":
            raise net.NetError("HTTP 400", 400)
        return {"remainingQueries": 10, "totalQueries": 20, "windowSizeSeconds": 7200}

    monkeypatch.setattr(net, "browser_json", fake_browser)
    report = grok.fetch(ctx(home, secrets={"grok_cookie": "Cookie: sso=abc"}))
    assert report.source == "grok.com"
    assert len(report.windows) == 1  # resultados idênticos não se repetem


def test_grok_web_from_curl_and_errors(monkeypatch, home):
    _no_credits(monkeypatch)
    seen = []
    curl = (  # "Copiar como cURL (cmd)" do Chrome
        r'curl ^"https://grok.com/rest/rate-limits^" ^'
        "\n"
        r'  -H ^"accept: */*^" ^'
        "\n"
        r'  -H ^"x-statsig-id: abc123^" ^'
        "\n"
        r'  -b ^"sso=s1; sso-rw=s2; cf_clearance=cf^" ^'
        "\n"
        r'  -H ^"user-agent: Mozilla/5.0 Edge^" ^'
        "\n"
        r'  --data-raw ^"^{^\^"requestKind^\^":^\^"DEFAULT^\^",^\^"modelName^\^":^\^"grok-5^\^"^}^"'
    )

    def fake_browser(method, url, **kw):
        seen.append((kw["json_body"]["modelName"], kw["headers"]))
        return {"remainingTokens": 50, "totalTokens": 200, "waitTimeSeconds": 60}

    monkeypatch.setattr(net, "browser_json", fake_browser)
    report = grok.fetch(ctx(home, "web", secrets={"grok_cookie": curl}))
    first_model, headers = seen[0]
    assert first_model == "grok-5"
    assert headers["Cookie"] == "sso=s1; sso-rw=s2; cf_clearance=cf"
    assert headers["User-Agent"] == "Mozilla/5.0 Edge" and headers["x-statsig-id"] == "abc123"
    assert report.windows[0].used == 75.0 and "tokens" in report.windows[0].detail

    def blocked(method, url, **kw):
        raise net.NetError("HTTP 403", 403, "cf-mitigated <html>")

    monkeypatch.setattr(net, "browser_json", blocked)
    with pytest.raises(ProviderError, match="Cloudflare"):
        grok.fetch(ctx(home, "web", secrets={"grok_cookie": "sso=1"}))
    with pytest.raises(ProviderError, match="sso"):
        grok.fetch(ctx(home, "web", secrets={"grok_cookie": "foo=bar"}))


def test_parse_pasted_formats():
    from ventude.limits.web import parse_pasted

    bash = (
        "curl 'https://grok.com/x' -H 'cookie: a=1; b=2' -H 'User-Agent: UA' "
        '--data-raw \'{"modelName":"m"}\''
    )
    p = parse_pasted(bash)
    assert p.cookie == "a=1; b=2" and p.user_agent == "UA" and p.models == ["m"]
    assert p.cookie_value("b") == "2"
    ps = (
        '$session.Cookies.Add((New-Object System.Net.Cookie("sso", "v1", "/", ".grok.com")))\n'
        '$session.Cookies.Add((New-Object System.Net.Cookie("sso-rw", "v2", "/", ".grok.com")))\n'
        'Invoke-WebRequest -UseBasicParsing -Uri "https://grok.com/rest/rate-limits" -UserAgent "UA2"'
    )
    p = parse_pasted(ps)
    assert p.cookie == "sso=v1; sso-rw=v2" and p.user_agent == "UA2"
    headers = parse_pasted("Host: grok.com\nCookie: x=1\nUser-Agent: UA3")
    assert headers.cookie == "x=1" and headers.user_agent == "UA3"
    assert parse_pasted("Cookie: z=9").cookie == "z=9"
    assert parse_pasted("").cookie == ""


def test_net_challenge_detection():
    assert net.NetError("x", 403, "<title>Just a moment...</title>").challenge
    assert not net.NetError("x", 403, '{"error":"forbidden"}').challenge
    assert not net.NetError("x", 500, "just a moment").challenge


def test_grok_setup(home):
    assert grok.fetch(ctx(home)).state == "setup"
