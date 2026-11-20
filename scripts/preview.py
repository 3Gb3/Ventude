"""Gera capturas da interface com dados de demonstração (não toca nos seus dados).

uv run python scripts/preview.py [pasta_de_saida]
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import date, timedelta
from datetime import time as dtime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["VENTUDE_HOME"] = tempfile.mkdtemp(prefix="ventude-preview-")
os.environ["VENTUDE_USER_HOME"] = tempfile.mkdtemp(prefix="ventude-home-")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QRadialGradient  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ventude.core.paths import app_data_dir  # noqa: E402
from ventude.core.store import Store  # noqa: E402
from ventude.core.tasks import Tasks  # noqa: E402
from ventude.limits.model import Report, Window  # noqa: E402
from ventude.services.media import MediaService, MediaState  # noqa: E402
from ventude.services.weather import Place  # noqa: E402

H = 3600


def demo_fetchers() -> dict:
    now = time.time()
    return {
        "claude": lambda ctx: Report(
            "claude",
            "ok",
            [
                Window("session", "Sessão · 5h", 62, now + 2 * H + 14 * 60, period=5 * H),
                Window("weekly", "Semana · todos os modelos", 38, now + 3 * 86400 + 5 * H, period=7 * 86400),
                Window("weekly_opus", "Semana · Opus", 21, now + 3 * 86400 + 5 * H, period=7 * 86400),
            ],
            plan="Max 5x",
            source="Claude Code",
        ),
        "antigravity": lambda ctx: Report(
            "antigravity",
            "ok",
            [
                Window(
                    "model:gemini-3-pro",
                    "Gemini 3 Pro",
                    24,
                    now + 3 * H + 40 * 60,
                    "Variantes: High, Low",
                    "Gemini",
                ),
                Window("model:gemini-3-flash", "Gemini 3 Flash", 8, now + 4 * H + 5 * 60, "", "Gemini"),
                Window("model:claude-sonnet-4-5", "Claude Sonnet 4.5", 91, now + 52 * 60, "", "Claude"),
                Window("model:gpt-oss-120b", "GPT-OSS 120B (Medium)", 40, now + 2 * H, "", "GPT"),
                Window("model:argon", "Argon", 63, now + 5 * H, "", "Outros"),
            ],
            plan="Google AI Pro",
            source="Antigravity",
        ),
        "grok": lambda ctx: Report(
            "grok",
            "ok",
            [
                Window("mode:grok-4-auto", "Auto", 46, None, "27 de 50 restantes · janela de 2h"),
                Window("mode:grok-4", "Expert", 80, now + 41 * 60, "4 de 20 restantes · janela de 2h"),
            ],
            plan="SuperGrok",
            source="grok.com",
        ),
        "codex": lambda ctx: Report(
            "codex",
            "ok",
            [
                Window("session", "Sessão · 5h", 35, now + 3 * H + 10 * 60),
                Window("weekly", "Semana", 58, now + 4 * 86400),
            ],
            plan="Plus",
            source="Codex CLI",
        ),
        "cursor": lambda ctx: Report(
            "cursor",
            "ok",
            [Window("plan", "Plano · ciclo", 72, now + 12 * 86400, "US$ 14,40 de US$ 20,00")],
            plan="Pro",
            source="Cursor",
        ),
        "copilot": lambda ctx: Report(
            "copilot",
            "ok",
            [Window("premium", "Requisições premium · mês", 41, now + 9 * 86400, "177 de 300 restantes")],
            plan="Pro",
            source="GitHub",
        ),
    }


def art() -> bytes:
    image = QImage(240, 240, QImage.Format.Format_ARGB32)
    p = QPainter(image)
    g = QLinearGradient(0, 0, 240, 240)
    g.setColorAt(0, QColor("#FF8A5B"))
    g.setColorAt(0.5, QColor("#C94B7C"))
    g.setColorAt(1, QColor("#3B2A8C"))
    p.fillRect(0, 0, 240, 240, g)
    p.setBrush(QColor(255, 255, 255, 60))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(150, 95), 60, 60)
    p.end()
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def seed(store: Store) -> None:
    today = date.today()
    store.add_event("Daily do time", today, dtime(9, 30), dtime(9, 45))
    store.add_event("Revisar PR do app", today, dtime(16, 0))
    store.add_event("Dentista", today + timedelta(days=2), dtime(14, 0), dtime(15, 0))
    store.add_event("Aniversário da Ana", today + timedelta(days=6))
    first = store.lists()[0]
    for text in (
        "Responder e-mails do cliente",
        "Atualizar o README",
        "Comprar café",
        "Pagar a conta de luz",
        "Ligar para a oficina",
    ):
        store.add_item(first.id, text)
    done = store.add_item(first.id, "Backup do notebook")
    store.set_done(done.id, True)
    store.add_list("Mercado")
    store.add_shortcut("folder", "Projetos", str(Path.home()))
    store.add_shortcut("app", "Python", sys.executable)
    store.add_shortcut("url", "GitHub", "https://github.com")
    store.add_shortcut("url", "Claude", "https://claude.ai")
    store.add_shortcut("folder", "Downloads", "/tmp")
    store.add_shortcut("url", "Gmail", "https://mail.google.com")
    import math as _math

    now_ts = time.time()
    for provider, key, peak in (
        ("claude", "session", 62),
        ("antigravity", "model:gemini-3-pro", 24),
        ("cursor", "plan", 72),
    ):
        for step in range(0, 24 * 12):  # uma amostra a cada 5 min nas últimas 24 h
            ts = now_ts - (24 * 12 - step) * 300
            phase = (step % 60) / 60  # janela de 5h que renova
            used = min(100.0, peak * (0.15 + 0.85 * phase) + 4 * _math.sin(step / 7))
            store.add_sample(provider, key, ts, max(0.0, used))
    import math

    plan = [
        (2, 30, 19, 10, 2.0),
        (61, 26, 18, 70, 8.4),
        (80, 24, 17, 85, 14.2),
        (3, 25, 16, 30, 0.6),
        (1, 28, 17, 5, 0.0),
        (0, 31, 19, 0, 0.0),
        (95, 29, 20, 90, 22.0),
    ]
    days, hours = [], []
    for i, (code, mx, mn, rain, precip) in enumerate(plan):
        iso = (today + timedelta(days=i)).isoformat()
        humidity = []
        for hour in range(24):
            curve = (math.sin((hour - 9) / 24 * 2 * math.pi) + 1) / 2
            temp = mn + (mx - mn) * curve
            hum = round(88 - 38 * curve + (8 if rain > 50 else 0))
            humidity.append(hum)
            chance = max(0, min(100, int(rain * (0.4 + 0.6 * abs(math.sin((hour + i) / 3.1))))))
            hours.append(
                {
                    "time": f"{iso}T{hour:02d}:00",
                    "temp": round(temp, 1),
                    "feels": round(temp + 1.5, 1),
                    "humidity": hum,
                    "rain": chance,
                    "precip": round(precip / 10 * chance / 100, 1),
                    "code": code if 9 <= hour <= 20 else (1 if code in (0, 2) else code),
                    "is_day": 6 <= hour <= 18,
                    "wind": round(8 + 10 * curve, 1),
                    "uv": round(max(0.0, 9 * math.sin((hour - 6) / 13 * math.pi)), 1)
                    if 6 <= hour <= 19
                    else 0,
                }
            )
        days.append(
            {
                "date": iso,
                "code": code,
                "max": mx,
                "min": mn,
                "rain": rain,
                "precip": precip,
                "feels_max": mx + 2,
                "feels_min": mn - 1,
                "sunrise": f"{iso}T05:12",
                "sunset": f"{iso}T18:41",
                "uv": 9.0 if code in (0, 1, 2) else 5.0,
                "wind_max": 18.0 + i,
                "gusts": 34.0 + i,
                "wind_dir": 135.0,
                "hum_mean": round(sum(humidity) / 24),
                "hum_min": min(humidity),
                "hum_max": max(humidity),
            }
        )
    store.cache_set(
        "wx2:-23.550,-46.633",
        {
            "place": "São Paulo, São Paulo, Brasil",
            "temp": 27.0,
            "feels": 29.0,
            "humidity": 61,
            "wind": 12.0,
            "code": 2,
            "is_day": True,
            "days": days,
            "hours": hours,
        },
        3600,
    )
    import random

    random.seed(7)
    for symbol, name, price, currency in (
        ("^BVSP", "Ibovespa", 158432.0, "BRL"),
        ("BRL=X", "Dólar", 5.31, "BRL"),
        ("BTC-USD", "Bitcoin", 91250.0, "USD"),
        ("PETR4.SA", "Petrobras", 32.18, "BRL"),
        ("VALE3.SA", "Vale", 64.02, "BRL"),
        ("AAPL", "Apple", 271.4, "USD"),
    ):
        history, value = [], price * 0.95
        for _ in range(22):
            value *= 1 + random.uniform(-0.018, 0.02)
            history.append(value)
        history[-1] = price
        change = (price - history[-2]) / history[-2] * 100
        store.cache_set(
            f"mkt:{symbol}",
            {
                "symbol": symbol,
                "name": name,
                "price": price,
                "change_pct": change,
                "currency": currency,
                "history": history,
            },
            3600,
        )
    now = time.time()
    news = {
        "G1": [
            (
                "Frente fria derruba temperaturas no Sul e Sudeste a partir de sexta",
                "Previsão indica chuva forte em SP no fim de semana.",
            ),
            (
                "Copom mantém juros e sinaliza cautela para os próximos meses",
                "Decisão foi unânime entre os diretores.",
            ),
        ],
        "BBC Brasil": [
            (
                "Como a inteligência artificial está mudando o trabalho de programadores",
                "Ferramentas de código já escrevem boa parte do software.",
            ),
        ],
        "Tecnoblog": [
            (
                "Windows 11 ganha novo menu Iniciar com seções personalizáveis",
                "Atualização chega aos poucos para todos os usuários.",
            ),
            (
                "Review: o notebook leve que dura o dia todo longe da tomada",
                "Testamos bateria, tela e desempenho.",
            ),
        ],
    }
    for index, (source, items) in enumerate(news.items()):
        store.cache_set(
            f"news:{source}",
            [
                {
                    "title": t,
                    "link": "https://example.com",
                    "source": source,
                    "published": now - (index * 3 + j) * 1700,
                    "summary": s,
                }
                for j, (t, s) in enumerate(items)
            ],
            3600,
        )


def backdrop(width: int, height: int) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    p = QPainter(image)
    g = QLinearGradient(0, 0, width, height)
    g.setColorAt(0, QColor("#3A4D63"))
    g.setColorAt(1, QColor("#1E2733"))
    p.fillRect(0, 0, width, height, g)
    r = QRadialGradient(QPointF(width * 0.8, height * 0.9), width * 0.6)
    r.setColorAt(0, QColor(110, 231, 183, 40))
    r.setColorAt(1, QColor(110, 231, 183, 0))
    p.fillRect(0, 0, width, height, r)
    p.end()
    return image


def shot(island, path: Path, pad: int = 28) -> None:  # noqa: ANN001
    QApplication.processEvents()
    pixmap = island.grab()
    w, h = pixmap.width() / pixmap.devicePixelRatio(), pixmap.height() / pixmap.devicePixelRatio()
    canvas = backdrop(int(w + pad * 2), int(h + pad * 2))
    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawPixmap(QRectF(pad, pad, w, h), pixmap, QRectF(pixmap.rect()))
    p.end()
    canvas.save(str(path))


def settle(island, ms: int = 450) -> None:  # noqa: ANN001
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "preview")
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    from ventude.ui import theme
    from ventude.ui.context import AppContext
    from ventude.ui.island import Island

    theme.apply(app)
    store = Store(app_data_dir() / "ventude.db")
    seed(store)
    city = Place("São Paulo", "Brasil", -23.55, -46.633, "São Paulo")
    store.save_settings(
        store.load_settings().copy(
            weather_city_json=city.to_json(),
            codex_mode="auto",
            cursor_mode="auto",
            copilot_mode="auto",
            ring_providers="claude,antigravity,grok,cursor",
            pinned_limits_json='{"claude": "weekly"}',
        )
    )
    media = MediaService()
    ctx = AppContext(store, Tasks(), media=media, fetchers=demo_fetchers())
    island = Island(ctx)
    island.show()
    ctx.limits.refresh_all()
    settle(island, 800)
    shot(island, out / "00-ilha.png", 18)
    state = MediaState(
        "Midnight City",
        "M83",
        "Hurry Up, We're Dreaming",
        "playing",
        95,
        243,
        time.monotonic(),
        True,
        True,
        True,
        True,
    )
    media.state = state
    media.thumbnail = art()
    media.state_changed.emit(state)
    media.thumbnail_changed.emit(media.thumbnail)
    settle(island, 700)
    shot(island, out / "01-ilha-musica.png", 18)
    for index, key in enumerate(
        ("home", "limits", "shortcuts", "agenda", "tasks", "weather", "market", "news", "settings")
    ):
        island.expand(key)
        settle(island, 900 if index == 0 else 600)
        shot(island, out / f"{index + 2:02d}-{key}.png")
    from PySide6.QtWidgets import QScrollArea

    area = island.panel.pages["settings"].findChild(QScrollArea)
    for part in (1, 2, 3):
        area.verticalScrollBar().setValue(part * 440)
        settle(island, 200)
        shot(island, out / f"{index + 2:02d}-settings-{part + 1}.png")
    # ---------- telas da versão 1.1 ----------
    island.expand("weather")
    settle(island, 400)
    weather = island.panel.pages["weather"]
    weather.open_day((date.today() + timedelta(days=1)).isoformat())
    settle(island, 300)
    shot(island, out / "20-clima-dia.png")
    chart = weather.detail.chart
    chart.hover = 14
    weather.detail._hovered(14)
    chart.update()
    settle(island, 200)
    shot(island, out / "21-clima-dia-hora.png")
    weather.close_day()
    island.panel.drop_hint.show()
    island.panel.drop_hint.raise_()
    island.panel.select("shortcuts")
    settle(island, 500)
    shot(island, out / "22-arrastar.png")
    island.panel.drop_hint.hide()
    island.collapse()
    settle(island, 500)
    paused = MediaState(
        "Midnight City", "M83", "", "paused", 95, 243, time.monotonic(), True, True, True, True
    )
    media.state = paused
    media.state_changed.emit(paused)
    settle(island, 500)
    shot(island, out / "23-ilha-pausada.png", 18)
    island.expand("home")
    settle(island, 120)
    shot(island, out / "24-abrindo.png")
    settle(island, 500)
    island.panel.select("limits")
    settle(island, 90)
    shot(island, out / "25-trocando-aba.png")
    # ---------- telas da versão 1.2 ----------
    island.collapse()
    media.state = None
    media.state_changed.emit(None)
    settle(island, 700)
    shot(island, out / "30-ilha-clima.png", 18)
    from PySide6.QtCore import QMimeData, QPoint

    from ventude.ui.toast import Toast

    ctx.shelf.add_text("Endereço da reunião: Av. Paulista, 1000")
    ctx.shelf.add_paths([str(Path.home()), sys.executable, "https://github.com/3Gb3/Ventude_Notch"])
    ctx.timer.start_pomodoro()
    media.state = state
    media.state_changed.emit(state)
    settle(island, 800)
    shot(island, out / "32-ilha-timer-estante.png", 18)
    island.show_toast(
        Toast(
            "Claude: 85% usado",
            "Sessão · 5h chegou a 85%. Renova em 1h 10min.",
            icon="gauge",
            color="#E38B67",
            seconds=30,
            kind="limits",
        )
    )
    settle(island, 700)
    shot(island, out / "33-aviso-na-ilha.png", 18)
    island._cancel_toasts()
    island.expand("home")
    settle(island, 700)
    shot(island, out / "34-inicio-timer.png")
    island.panel.select("limits")
    settle(island, 600)
    shot(island, out / "35-ias-ritmo.png")
    island.panel.select("shelf")
    settle(island, 600)
    shot(island, out / "36-estante.png")
    island.panel.command.open_bar("t: revisar PR do app")
    settle(island, 300)
    shot(island, out / "37-comandos-tarefa.png")
    island.panel.command.open_bar("git")
    settle(island, 300)
    shot(island, out / "38-comandos-atalho.png")
    island.panel.command.close_bar()
    hint = island.panel.drop_hint
    hint.set_zones(["shelf", "shortcuts"])
    hint.set_active("shelf")
    hint.show()
    hint.raise_()
    settle(island, 200)
    shot(island, out / "39-soltar-duas-areas.png")
    hint.hide()
    _ = (QMimeData, QPoint)
    island.expand("settings")
    settle(island, 400)
    page = island.panel.pages["settings"]
    area.verticalScrollBar().setValue(page.providers.parentWidget().y() - 40)
    settle(island, 300)
    shot(island, out / "31-ajustes-ias.png")
    print(f"Capturas em {out.resolve()}")


if __name__ == "__main__":
    main()
