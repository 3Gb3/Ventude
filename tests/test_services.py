from ventude.services import launcher
from ventude.services.market import Quote, dump_watchlist, format_price, parse_chart, parse_watchlist
from ventude.services.media import MediaState, build_state, format_time
from ventude.services.news import parse_feed
from ventude.services.weather import Place, icon_for, parse_forecast

FORECAST = {
    "current": {
        "temperature_2m": 26.4,
        "apparent_temperature": 28.1,
        "relative_humidity_2m": 60,
        "wind_speed_10m": 11.2,
        "weather_code": 2,
        "is_day": 0,
    },
    "daily": {
        "time": ["2026-11-19", "2026-11-20"],
        "weather_code": [2, 61],
        "temperature_2m_max": [30, 25],
        "temperature_2m_min": [19, 18],
        "precipitation_probability_max": [10, 70],
    },
}


def test_weather_parse_and_icons():
    f = parse_forecast(FORECAST, "São Paulo")
    assert f.temp == 26.4 and f.description == "Parcialmente nublado"
    assert f.icon == "cloud-moon"  # noite
    assert f.days[1]["rain"] == 70
    assert icon_for(0) == "sun" and icon_for(63) == "rain" and icon_for(95) == "storm"
    place = Place("Cachoeirinha", "Brasil", -29.9, -51.1, "RS")
    assert Place.from_json(place.to_json()) == place
    assert Place.from_json("") is None and Place.from_json("{bad") is None


CHART = {
    "chart": {
        "result": [
            {
                "meta": {"regularMarketPrice": 110.0, "currency": "USD"},
                "indicators": {"quote": [{"close": [90.0, None, 100.0, 109.0]}]},
            }
        ]
    }
}


def test_market_parse_and_format():
    q = parse_chart(CHART, "AAPL", "Apple")
    assert q.price == 110.0 and round(q.change_pct, 2) == 10.0
    assert q.history == [90.0, 100.0, 110.0]
    assert parse_chart({"chart": {"result": []}}, "X", "X") is None
    assert format_price(Quote("AAPL", "Apple", 1234.5, 0, "USD")) == "US$ 1.234,50"
    assert format_price(Quote("^BVSP", "Ibov", 158432.4, 0, "BRL")) == "158.432 pts"
    assert format_price(Quote("BRL=X", "Dólar", 5.3123, 0, "BRL")) == "R$ 5,31"
    items = parse_watchlist(" aapl:Apple, ^BVSP , aapl:dup,")
    assert items == [("AAPL", "Apple"), ("^BVSP", "^BVSP")]
    assert dump_watchlist(items) == "AAPL:Apple,^BVSP"


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Notícia &amp; mais</title><link>https://a.com/1</link>
<pubDate>Wed, 19 Nov 2026 10:00:00 -0300</pubDate><description>&lt;p&gt;Resumo&lt;/p&gt;</description></item>
<item><title></title><link>https://a.com/2</link></item>
</channel></rss>"""
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom</title><link rel="alternate" href="https://b.com"/><updated>2026-11-19T12:00:00Z</updated>
<summary>Oi</summary></entry></feed>"""


def test_news_parse_rss_and_atom():
    rss = parse_feed(RSS, "G1")
    assert len(rss) == 1 and rss[0].title == "Notícia & mais" and rss[0].summary == "Resumo"
    assert rss[0].published > 0
    atom = parse_feed(ATOM, "X")
    assert atom[0].link == "https://b.com" and atom[0].published > 0


def test_launcher_helpers(tmp_path):
    assert launcher.normalize_url("github.com") == "https://github.com"
    assert launcher.normalize_url("ftp://x.com") == ""
    assert launcher.normalize_url("localhost") == ""
    assert launcher.default_label("https://www.youtube.com/x", "url") == "Youtube"
    assert launcher.kind_for(str(tmp_path)) == "folder"
    exe = tmp_path / "App.exe"
    exe.write_text("")
    assert launcher.kind_for(str(exe)) == "app"
    assert launcher.default_label(str(exe), "app") == "App"
    assert launcher.is_valid("folder", str(tmp_path)) and not launcher.is_valid("folder", "/nao/existe")
    ok, message = launcher.open_target("file", "/nao/existe")
    assert not ok and message


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_media_build_state():
    props = Obj(title=" Song ", artist="Band", album_title="LP")
    controls = Obj(
        is_play_pause_toggle_enabled=True,
        is_next_enabled=True,
        is_previous_enabled=False,
        is_playback_position_enabled=True,
    )
    playback = Obj(playback_status=5, controls=controls)
    timeline = Obj(start_time=0, end_time=180 * 10_000_000, position=30 * 10_000_000)
    state = build_state(props, playback, timeline, "Spotify.exe")
    assert isinstance(state, MediaState)
    assert state.title == "Song" and state.status == "paused" and state.can_seek
    assert state.position_at() == 30
    assert build_state(Obj(title=""), playback, timeline) is None
    assert format_time(3725) == "1:02:05"
