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


def test_claude_expired_token_and_errors(home, monkeypatch):
    write_claude(home, (time.time() - 10) * 1000)
    with pytest.raises(ProviderError, match="expirou"):
        claude.fetch(ctx(home))


def test_claude_401_message(home, monkeypatch):
    write_claude(home, (time.time() + 3600) * 1000)

    def boom(url, **kw):
        raise net.NetError("HTTP 401", 401)

    monkeypatch.setattr(net, "get_json", boom)
    with pytest.raises(ProviderError, match="login"):
        claude.fetch(ctx(home))


def test_claude_web_fallback(home, monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append((url, kw["headers"]["Cookie"]))
        if url.endswith("/organizations"):
            return [{"uuid": "org1", "capabilities": ["chat", "claude_pro"]}]
        return USAGE

    monkeypatch.setattr(net, "get_json", fake_get)
    report = claude.fetch(ctx(home, secrets={"claude_session": "sessionKey=sk-ant-sid01-x; other=1"}))
    assert report.source == "claude.ai" and report.plan == "Pro"
    assert calls[0][1] == "sessionKey=sk-ant-sid01-x"
    assert calls[1][0].endswith("/organizations/org1/usage")


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


def test_grok_fetch_web_cookie(monkeypatch, home):
    def fake_post(url, body, **kw):
        assert kw["headers"]["Cookie"] == "sso=abc"
        if body["modelName"] == "grok-4-heavy":
            raise net.NetError("HTTP 403", 403)
        return {"remainingQueries": 10, "totalQueries": 20, "windowSizeSeconds": 7200}

    monkeypatch.setattr(net, "post_json", fake_post)
    report = grok.fetch(ctx(home, secrets={"grok_cookie": "Cookie: sso=abc"}))
    assert report.source == "grok.com"
    assert len(report.windows) == 1  # resultados idênticos não se repetem


def test_grok_setup(home):
    assert grok.fetch(ctx(home)).state == "setup"
