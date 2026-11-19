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
                Window("session", "Sessão · 5h", 62, now + 2 * H + 14 * 60),
                Window("weekly", "Semana · todos os modelos", 38, now + 3 * 86400 + 5 * H),
                Window("weekly_opus", "Semana · Opus", 21, now + 3 * 86400 + 5 * H),
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
                    "Gemini 3 Pro (High), Gemini 3 Pro (Low)",
                ),
                Window("model:gemini-3-flash", "Gemini 3 Flash", 8, now + 4 * H + 5 * 60),
                Window("model:claude", "Claude · 2 modelos", 91, now + 52 * 60),
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
    store.cache_set(
        "wx:-23.550,-46.633",
        {
            "place": "São Paulo, São Paulo, Brasil",
            "temp": 27.0,
            "feels": 29.0,
            "humidity": 61,
            "wind": 12.0,
            "code": 2,
            "is_day": True,
            "days": [
                {"date": (today + timedelta(days=i)).isoformat(), "code": c, "max": mx, "min": mn, "rain": r}
                for i, (c, mx, mn, r) in enumerate(
                    [
                        (2, 30, 19, 10),
                        (61, 26, 18, 70),
                        (80, 24, 17, 85),
                        (3, 25, 16, 30),
                        (1, 28, 17, 5),
                        (0, 31, 19, 0),
                        (95, 29, 20, 90),
                    ]
                )
            ],
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
    store.save_settings(store.load_settings().copy(weather_city_json=city.to_json()))
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
    print(f"Capturas em {out.resolve()}")


if __name__ == "__main__":
    main()
