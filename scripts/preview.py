"""Gera capturas da interface com dados de demonstração (não toca nos seus dados).

uv run python scripts/preview.py [pasta_de_saida] [--shell]

--shell gera só as telas do Ventude Shell 3.0 ("Calma"): área de trabalho com Doca flutuante e Ilha,
Controles, Eco secundário, Lançador unificado (fixados, busca, projeto Git, todos, energia), prévias ao
vivo e o Interruptor nas cortinas de energia. Ícones: os reais do Windows para uma lista fixa de apps
comuns (quando instalados); o resto é de demonstração.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import date, timedelta
from datetime import time as dtime
from pathlib import Path

OUT = Path(next((a for a in sys.argv[1:] if not a.startswith("--")), "preview")).resolve()
SHELL_ONLY = "--shell" in sys.argv
# Tela de 1920×1080 no modo sem janela (o Qt lê a configuração de um arquivo relativo à pasta atual).
_screen_dir = tempfile.mkdtemp(prefix="ventude-screen-")
Path(_screen_dir, "screens.json").write_text(
    json.dumps(
        {
            "screens": [
                {"name": "Tela", "x": 0, "y": 0, "width": 1920, "height": 1080, "logicalDpi": 96, "dpr": 1}
            ]
        }
    ),
    encoding="utf-8",
)
os.chdir(_screen_dir)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen:configfile=screens.json")
if sys.platform == "win32":  # sem janela, o Qt só acha as fontes do Windows se apontarmos a pasta
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
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
                Window(
                    "credits", "Limite semanal", 46, now + 5 * 86400 + 3 * H, "Grok Bot 46%", period=7 * 86400
                ),
            ],
            plan="Premium",
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
    out = OUT
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
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
    if SHELL_ONLY:
        shell_v3_screens(ctx, island, media, out)
        print(f"Capturas em {out}")
        return
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
    shell_screens(ctx, island, media, out)
    shell_v3_screens(ctx, island, media, out)
    print(f"Capturas em {out.resolve()}")


def shell_screens(ctx, island, media, out: Path) -> None:  # noqa: ANN001
    """Telas do pacote "Ventude Shell & Sistema" (1.2)."""
    from ventude.services import indexer
    from ventude.services.audio import AppSession, AudioState, Device
    from ventude.services.hardware import Battery, Gpu, Snapshot
    from ventude.services.privacy import PrivacyState
    from ventude.ui.power_menu import PowerMenu

    island.collapse()
    ctx.timer.stop()
    ctx.shelf.clear()
    media.state = None
    media.state_changed.emit(None)
    ctx.caffeine._apply = lambda _flags: True  # prévia: não mexe no estado de energia do PC
    snap = Snapshot(
        cpu=23.0,
        cores=16,
        cpu_temp=58.0,
        gpu=Gpu("AMD Radeon RX 7600", 41.0, 3.1 * 1024**3, 8.0 * 1024**3, 64.0, 92.0, "pdh"),
        ram_used=15.2 * 1024**3,
        ram_total=31.9 * 1024**3,
        net_down=12.4 * 1024**2,
        net_up=420 * 1024,
        ping=18.0,
        disk_path="C:\\",
        disk_used=255 * 1024**3,
        disk_total=476 * 1024**3,
        battery=Battery(82, charging=True, plugged=True),
        top_cpu=[("Claude", 7.9), ("Firefox", 3.1), ("Discord", 0.7)],
        top_ram=[("Firefox", 3.5 * 1024**3), ("Discord", 3.0 * 1024**3), ("Claude", 2.9 * 1024**3)],
    )
    import math

    for index in range(40):
        wave = (math.sin(index / 4) + 1) / 2
        ctx.hardware.history["cpu"].append(12 + wave * 30)
        ctx.hardware.history["gpu"].append(20 + wave * 45)
        ctx.hardware.history["ram"].append(46 + wave * 3)
        ctx.hardware.history["net_down"].append(wave * 14 * 1024**2)
        ctx.hardware.history["net_up"].append((1 - wave) * 600 * 1024)
    ctx.update_settings(pinned_metrics_json='["cpu", "gpu", "gpu_temp", "net_down"]')
    ctx.hardware._done(snap)
    ctx.privacy.apply(PrivacyState(mic=("Discord",), cam=()))
    ctx.caffeine.start(2 * 3600)
    island._cancel_toasts()
    settle(island, 800)
    shot(island, out / "40-ilha-sistema-privacidade.png", 18)
    island._volume_feedback(0.68, False)
    settle(island, 500)
    shot(island, out / "41-ilha-volume.png", 18)
    island._cancel_toasts()
    island.expand("hardware")
    settle(island, 700)
    ctx.hardware._done(snap)
    settle(island, 300)
    shot(island, out / "42-sistema.png")
    island.panel.select("audio")
    settle(island, 400)
    ctx.audio.changed.emit(
        AudioState(
            outputs=(
                Device("1", "Alto-falantes (Redragon Gaming Headset)", "render", True),
                Device("2", "1 - LC24RG50 (AMD High Definition Audio Device)", "render"),
                Device("3", "Fones de ouvido (WH-1000XM4)", "render"),
            ),
            inputs=(
                Device("4", "Microfone (Redragon Gaming Headset)", "capture", True),
                Device("5", "Microfone (2- XWF-1080P)", "capture"),
            ),
            master=0.5,
            mic=0.9,
            sessions=(
                AppSession("discord", "Discord", "", (1,), 0.8, False, True),
                AppSession("spotify", "Spotify", "", (2,), 0.55, False, True),
                AppSession("firefox", "Firefox", sys.executable, (3,), 1.0, True),
                AppSession("system", "Sons do sistema", "", (4,), 1.0, False, system=True),
            ),
        )
    )
    settle(island, 300)
    shot(island, out / "43-audio.png")
    island.panel.select("home")
    ctx.indexer.files = [
        indexer.entry("Ventude_Notch", str(Path.cwd()), "folder"),
        indexer.entry("Projetos", str(Path.home()), "folder"),
    ]
    ctx.indexer.apps = [indexer.entry("Visual Studio Code", sys.executable, "app")]
    island.open_start(text="1450 * 5,85")
    settle(island, 300)
    shot(island, out / "44-busca-calculadora.png")
    island.panel.command.open_bar("/p ven", start=True)
    settle(island, 300)
    shot(island, out / "45-busca-pasta.png")
    island.panel.command.close_bar()
    island.panel.show_qr("https://github.com")
    settle(island, 300)
    shot(island, out / "46-qr.png")
    island.panel.qr.close_card()
    ctx.power.pending = True
    island.panel._power_changed(True)
    menu = PowerMenu(ctx.power.available())
    menu.choose("restart")
    menu.adjustSize()
    menu.show()
    settle(island, 300)
    shot(menu, out / "47-energia.png", 18)
    menu.hide()
    ctx.caffeine.stop()


# ---------- Ventude Shell (2.0) ----------
W, H = 1920, 1080
DEMO_APPS = (
    "Microsoft Edge",
    "Firefox",
    "Google Chrome",
    "Explorador de Arquivos",
    "Configurações",
    "Microsoft Store",
    "Fotos",
    "Calculadora",
    "Bloco de Notas",
    "Paint",
    "Ferramenta de Captura",
    "Relógio",
    "Terminal",
    "Windows PowerShell",
    "Spotify",
    "Discord",
    "WhatsApp",
    "Steam",
    "Visual Studio Code",
    "Cursor",
    "Claude",
    "ChatGPT",
    "Antigravity",
    "Xbox",
    "Outlook",
    "LinkedIn",
    "OBS Studio",
    "Notion",
    "Obsidian",
    "VLC media player",
    "Gerenciador de Tarefas",
    "Microsoft Teams",
    "Telegram",
    "Epic Games Launcher",
    "Riot Client",
    "HandBrake",
    "Blender",
)


def demo_catalog() -> list:
    """Apps de demonstração com os ícones reais do Windows (só os desta lista fixa, se instalados)."""
    from ventude.services import indexer

    found = indexer.scan_apps(indexer.start_menu_dirs())
    found += indexer.scan_store_apps({a.key for a in found})
    wanted = {name.lower(): name for name in DEMO_APPS}
    chosen: dict[str, object] = {}
    for entry in found:
        key = entry.name.lower()
        for wanted_key, label in wanted.items():
            if key == wanted_key or (key.startswith(wanted_key) and len(key) <= len(wanted_key) + 8):
                chosen.setdefault(label, indexer.entry(label, entry.path, entry.kind))
    if "Explorador de Arquivos" not in chosen:
        chosen["Explorador de Arquivos"] = indexer.entry(
            "Explorador de Arquivos", r"C:\Windows\explorer.exe", "app"
        )
    if "Bloco de Notas" not in chosen and Path(r"C:\Windows\System32\notepad.exe").exists():
        chosen["Bloco de Notas"] = indexer.entry("Bloco de Notas", r"C:\Windows\System32\notepad.exe", "app")
    return sorted(chosen.values(), key=lambda e: e.key)


def wallpaper() -> QImage:
    """Papel de parede de demonstração (ondas suaves em azul e verde)."""
    image = QImage(W, H, QImage.Format.Format_ARGB32)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, W, H)
    g.setColorAt(0, QColor("#0E1A2B"))
    g.setColorAt(0.55, QColor("#13293D"))
    g.setColorAt(1, QColor("#0B1320"))
    p.fillRect(0, 0, W, H, g)
    for cx, cy, radius, color in (
        (W * 0.78, H * 0.30, 620, QColor(110, 231, 183, 70)),
        (W * 0.22, H * 0.85, 700, QColor(80, 140, 255, 70)),
        (W * 0.55, H * 0.62, 420, QColor(180, 120, 255, 50)),
    ):
        r = QRadialGradient(QPointF(cx, cy), radius)
        r.setColorAt(0, color)
        r.setColorAt(1, QColor(color.red(), color.green(), color.blue(), 0))
        p.fillRect(0, 0, W, H, r)
    from PySide6.QtGui import QPainterPath, QPen

    for index in range(7):
        path = QPainterPath()
        y0 = H * 0.18 + index * 46
        path.moveTo(-50, y0)
        path.cubicTo(W * 0.3, y0 - 160 + index * 12, W * 0.62, y0 + 220 - index * 8, W + 50, y0 + 40)
        p.setPen(QPen(QColor(255, 255, 255, 10 + index * 3), 1.4))
        p.drawPath(path)
    p.end()
    return image


def mock_window(title: str, accent: str, w: int, h: int) -> QImage:
    """Janela de app de mentira (para a área de trabalho e as prévias)."""
    image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#1E2128"))
    p.drawRoundedRect(QRectF(0, 0, w, h), 8, 8)
    p.setBrush(QColor("#252932"))
    p.drawRoundedRect(QRectF(0, 0, w, 40), 8, 8)
    p.drawRect(QRectF(0, 20, w, 20))
    p.setPen(QColor("#C9CED6"))
    from ventude.ui import theme

    p.setFont(theme.font(13))
    p.drawText(QRectF(16, 0, w - 160, 40), Qt.AlignmentFlag.AlignVCenter, title)
    for index, x in enumerate((w - 110, w - 72, w - 34)):
        p.setPen(QColor("#8A919C") if index < 2 else QColor("#8A919C"))
        p.drawText(QRectF(x - 10, 0, 30, 40), Qt.AlignmentFlag.AlignCenter, ("–", "▢", "✕")[index])
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(accent))
    p.drawRoundedRect(QRectF(24, 64, w * 0.38, 18), 6, 6)
    p.setBrush(QColor("#2C313B"))
    for row in range(int((h - 120) / 34)):
        width = w * (0.82 - (row % 4) * 0.13)
        p.drawRoundedRect(QRectF(24, 104 + row * 34, width, 12), 6, 6)
    p.setBrush(QColor(accent).darker(160))
    p.drawRoundedRect(QRectF(w * 0.62, 64, w * 0.32, h * 0.42), 10, 10)
    p.end()
    return image


def blurred(image: QImage, rect) -> QImage:  # noqa: ANN001
    """Desfoque barato (reduz e amplia duas vezes): imita o acrylic do Windows nas prévias."""
    crop = image.copy(rect)
    w, h = max(1, crop.width()), max(1, crop.height())
    smooth = Qt.TransformationMode.SmoothTransformation
    ignore = Qt.AspectRatioMode.IgnoreAspectRatio
    small = crop.scaled(max(1, w // 6), max(1, h // 6), ignore, smooth)
    small = small.scaled(max(1, w // 18), max(1, h // 18), ignore, smooth)
    return small.scaled(w, h, ignore, smooth)


def put(
    canvas: QImage, widget, x: int, y: int, radius: int = 10, glass: bool = True, shadow: bool = True
) -> None:  # noqa: ANN001
    """Desenha a janela na área de trabalho de demonstração: sombra, vidro desfocado e o conteúdo."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPainterPath

    QApplication.processEvents()
    pixmap = widget.grab()
    w, h = (
        round(pixmap.width() / pixmap.devicePixelRatio()),
        round(pixmap.height() / pixmap.devicePixelRatio()),
    )
    rect = QRect(x, y, w, h)
    under = blurred(canvas, rect) if glass else None
    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if shadow:
        p.setPen(Qt.PenStyle.NoPen)
        for step in range(14, 0, -1):
            p.setBrush(QColor(0, 0, 0, 7))
            p.drawRoundedRect(
                QRectF(rect).adjusted(-step, -step + 6, step, step + 6), radius + step, radius + step
            )
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect), radius, radius)
    p.setClipPath(path)
    if under is not None:
        p.drawImage(rect.topLeft(), under)
    p.drawPixmap(QRectF(rect), pixmap, QRectF(pixmap.rect()))
    p.end()


def write_apng(path: Path, frames: list[QImage], delay_ms: int, last_ms: int = 1500) -> None:
    """PNG animado (APNG) sem bibliotecas extras: abre animado no navegador."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    first = frames[0]
    w, h = first.width(), first.height()
    parts = [b"\x89PNG\r\n\x1a\n", chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))]
    parts.append(chunk(b"acTL", struct.pack(">II", len(frames), 0)))
    sequence = 0
    for index, frame in enumerate(frames):
        image = frame.convertToFormat(QImage.Format.Format_RGB888)
        stride = image.bytesPerLine()
        bits = bytes(image.constBits())
        raw = b"".join(b"\x00" + bits[y * stride : y * stride + w * 3] for y in range(h))
        data = zlib.compress(raw, 6)
        delay = last_ms if index == len(frames) - 1 else delay_ms
        parts.append(chunk(b"fcTL", struct.pack(">IIIIIHHBB", sequence, w, h, 0, 0, delay, 1000, 0, 0)))
        sequence += 1
        if index == 0:
            parts.append(chunk(b"IDAT", data))
        else:
            parts.append(chunk(b"fdAT", struct.pack(">I", sequence) + data))
            sequence += 1
    parts.append(chunk(b"IEND", b""))
    path.write_bytes(b"".join(parts))


def shell_v3_screens(ctx, island, media, out: Path) -> None:  # noqa: ANN001
    """Telas do Ventude Shell 3.0 ('Calma'), compostas numa área de trabalho de 1920×1080."""
    from ventude.services import indexer, shellicons
    from ventude.services import taskbar as taskbar_service
    from ventude.services.audio import AudioState, Device
    from ventude.services.taskbar import TaskbarHider, WindowInfo
    from ventude.services.weather import Place
    from ventude.ui.echo import EchoPill
    from ventude.ui.shell.controller import ShellController
    from ventude.ui.shell.curtain import PowerFx

    island._cancel_toasts()
    island.collapse()
    media.state = None
    media.state_changed.emit(None)
    ctx.update_settings(
        pinned_metrics_json="",
        animation="rapida",
        replace_taskbar=False,
        pill_date=True,
        pill_weather=True,
    )
    catalog = demo_catalog()
    by_name = {e.name: e for e in catalog}
    ctx.indexer.apps = catalog
    folders = Path(tempfile.mkdtemp(prefix="ventude-demo-"))
    demo_files = []
    repo_dir = folders / "Ventude_Notch"
    (repo_dir / ".git").mkdir(parents=True)
    (repo_dir / ".git" / "HEAD").write_text("ref: refs/heads/2.0.0\n", encoding="utf-8")
    demo_files.append(
        indexer.entry("Ventude_Notch", str(repo_dir), "folder", is_project=True, branch="2.0.0")
    )
    for name in ("Projetos", "Fotos da viagem"):
        (folders / name).mkdir()
        demo_files.append(indexer.entry(name, str(folders / name), "folder"))
    for name in ("Relatório Q4.docx", "Orçamento 2027.xlsx", "Apresentação.pptx", "contrato.pdf"):
        (folders / name).write_bytes(b"")
        demo_files.append(indexer.entry(name, str(folders / name), "file"))
    ctx.indexer.files = demo_files

    hider = TaskbarHider(
        windows=lambda: [1],
        show=lambda hwnd, cmd: None,
        screens=lambda: [((0, 0, W, H), (0, 0, W, H))],
        work_area=lambda rect: True,
        marker=folders / "barra.json",
    )
    windows: list[WindowInfo] = []
    taskbar_service.list_windows = lambda own_pid=None: list(windows)  # type: ignore[assignment]
    taskbar_service.covering_window = lambda *a, **k: False  # type: ignore[assignment]
    taskbar_service.foreground_rect = lambda *a, **k: None  # type: ignore[assignment]
    fx = PowerFx(ctx, runner=lambda key: (True, ""), island=island)
    shell = ShellController(ctx, island, hider, fx=fx)
    store = ctx.store
    shell.seed_start_pins()
    for name in ("Microsoft Edge", "Firefox", "WhatsApp", "LinkedIn", "Relógio", "OBS Studio"):
        entry = by_name.get(name)
        if entry is not None:
            store.add_pin("start", entry.kind, entry.name, entry.path)
    for name in ("Explorador de Arquivos", "Firefox", "Microsoft Store", "Discord", "Spotify", "Claude"):
        entry = by_name.get(name)
        if entry is not None:
            store.add_pin("taskbar", entry.kind, entry.name, entry.path)
    now = time.time()
    for label_text, minutes in (("Antigravity", 1), ("Claude", 2), ("Spotify", 90), ("Steam", 300)):
        entry = by_name.get(label_text)
        if entry is not None:
            store.record_launch(entry.kind, entry.name, entry.path, ts=now - minutes * 60)

    city = Place("São Paulo", "Brasil", -23.55, -46.633, "São Paulo")
    ctx.update_settings(weather_city_json=city.to_json())
    ctx.refresh_weather()
    settle(island, 500)

    names = [shellicons.parsing_name(e.kind, e.path) for e in catalog + demo_files]
    names += [shellicons.parsing_name(p.kind, p.target) for p in store.pins("taskbar") + store.pins("start")]
    for size in (16, 20, 24, 30, 32, 48):
        for name in names:
            ctx.icons.pixmap(name, size)
    end = time.monotonic() + 20
    while time.monotonic() < end and not all(ctx.icons.ready(n, s) for n in names for s in (24, 32) if n):
        QApplication.processEvents()
        time.sleep(0.02)

    ctx.update_settings(replace_taskbar=True, replace_start_menu=True, dock_mode="esquivar")
    settle(island, 400)
    dock = shell.docks[0]
    dock.resize(1280, dock.height())
    dock._recompute_layout(snap=True)

    def apps_lnk_target(path: str) -> str:
        from ventude.services.winutil import lnk_target

        return lnk_target(path) if path.lower().endswith(".lnk") else path

    def add_window(label_text: str, title: str, hwnd: int, foreground: bool = False) -> None:
        entry = by_name.get(label_text)
        if entry is not None:
            app_id = entry.path if entry.kind == "uwp" else ""
            exe = "" if entry.kind == "uwp" else (apps_lnk_target(entry.path) or entry.path)
            windows.append(WindowInfo(hwnd, title, hwnd, exe, foreground=foreground, app_id=app_id))

    add_window("Explorador de Arquivos", "Downloads - Explorador de Arquivos", 101)
    add_window("Explorador de Arquivos", "Projetos - Explorador de Arquivos", 102)
    add_window("Firefox", "Ventude_Notch · GitHub — Mozilla Firefox", 201, foreground=True)
    add_window("Discord", "#geral | Ventude - Discord", 301)
    add_window("Spotify", "Spotify Premium", 401)
    shell.apply_windows(windows)

    demo_audio = AudioState(
        outputs=(
            Device("1", "Alto-falantes (Realtek High Definition Audio)", "render", True),
            Device("2", "Fones de ouvido (WH-1000XM4)", "render"),
        ),
        inputs=(Device("3", "Microfone (USB)", "capture", True),),
        master=0.62,
        mic=0.8,
    )
    ctx.audio.state = demo_audio
    ctx.audio.changed.emit(demo_audio)
    shell.update_status()
    settle(island, 500)

    desktop = wallpaper()
    p = QPainter(desktop)
    p.drawImage(330, 100, mock_window("Ventude_Notch · GitHub — Mozilla Firefox", "#6EE7B7", 1260, 790))
    p.end()
    dock_x = (W - dock.width()) // 2
    dock_y = H - dock.height()
    put(desktop, island, (W - island.width()) // 2, island.y(), radius=19, glass=False, shadow=False)
    put(desktop, dock, dock_x, dock_y, radius=0, glass=False, shadow=False)
    desktop.save(str(out / "70-shell-area-de-trabalho.png"))
    desktop.copy(W // 2 - 440, H - 120, 880, 120).save(str(out / "71-doca-zoom.png"))

    # Doca com rótulo mini-ilha em hover
    if dock.current_layout.slots:
        dock._hovered_key = dock.current_layout.slots[1].key
        dock._show_hover_label = True
        dock.update()
        settle(island, 150)
        hover_img = QImage(wallpaper())
        put(hover_img, dock, dock_x, dock_y, radius=0, glass=False, shadow=False)
        hover_img.copy(W // 2 - 440, H - 140, 880, 140).save(str(out / "72-doca-hover.png"))
        dock._hovered_key = ""
        dock._show_hover_label = False

    with_dock = QImage(desktop)

    def scene(name: str, widget, x: int, y: int) -> None:  # noqa: ANN001
        canvas = QImage(with_dock)
        put(canvas, widget, x, y, radius=0, glass=False, shadow=False)
        canvas.save(str(out / name))

    # Controles na ilha
    island.open_controls(by_key=False)
    settle(island, 450)
    shot(island, out / "73-ilha-controles.png", 16)
    island.controls._toggle_devices()
    settle(island, 250)
    shot(island, out / "74-ilha-controles-saidas.png", 16)
    island.collapse()
    settle(island, 350)

    # Eco secundário
    echo = EchoPill(ctx, QApplication.primaryScreen())
    echo.place()
    echo.show()
    settle(island, 150)
    echo_canvas = QImage(wallpaper())
    put(echo_canvas, echo, (W - echo.width()) // 2, 8, radius=0, glass=False, shadow=False)
    echo_canvas.save(str(out / "75-eco-secundario.png"))
    echo.hide()
    echo.deleteLater()

    # Lançador (Home, Busca, Projeto Git, Todos os apps, Energia)
    shell.open_search()
    ln = shell.launcher
    ln.set_person("Você", "")
    settle(island, 450)
    lx = (W - ln.width()) // 2
    ly = (H - ln.height()) // 2
    scene("76-lancador-inicio.png", ln, lx, ly)
    ln.input.setText("con")
    settle(island, 350)
    scene("77-lancador-busca.png", ln, lx, (H - ln.height()) // 2)
    ln.input.setText("/ Ventude_Notch")
    settle(island, 350)
    scene("78-lancador-projeto-git.png", ln, lx, (H - ln.height()) // 2)
    ln.input.clear()
    ln.show_all_apps()
    settle(island, 350)
    scene("79-lancador-todos.png", ln, lx, (H - ln.height()) // 2)
    ln.show_power()
    settle(island, 350)
    scene("80-lancador-energia.png", ln, lx, (H - ln.height()) // 2)
    ln.hide()

    # Prévias
    first_with_wins = next((it for it in dock.items if len(it.windows) >= 2), None)
    if first_with_wins is not None:
        anchor = dock.slot_screen_rect(first_with_wins.key)
        shell._show_thumbs(first_with_wins, anchor, dock)
        thumbs = shell.thumbs
        thumbs.preview_images = {
            w.hwnd: mock_window(w.title, ("#6EE7B7", "#8EB8F5")[i % 2], 1280, 760)
            for i, w in enumerate(thumbs.windows)
        }
        settle(island, 350)
        tx = max(8, (W - thumbs.width()) // 2)
        scene("81-previas.png", thumbs, tx, H - 70 - thumbs.height())
        thumbs.hide_now()

    # Cortina Interruptor
    def curtain_frame(key: str, t_ms: float, greet: bool = False) -> QImage:
        phase_start = {"t": 0.0}
        clock = {"t": 0.0}
        fx._elapsed = lambda: clock["t"] - phase_start["t"]

        def enter(self, phase: str) -> None:  # noqa: ANN001
            self.phase = phase
            phase_start["t"] = clock["t"]

        fx._enter = enter.__get__(fx)
        if greet:
            fx.greet("Você", "27° e sol")
        else:
            fx.play(key)
        fx.timer.stop()
        for c in fx.curtains:
            c.hide()
        step = 0.0
        while step <= t_ms:
            clock["t"] = step
            fx._tick()
            step += 20
        primary = next(c for c in fx.curtains if c.primary)
        primary.resize(W, H)
        canvas = QImage(with_dock)
        pt = QPainter(canvas)
        pt.drawPixmap(0, 0, primary.grab())
        pt.end()
        fx.timer.stop()
        fx._close_curtains()
        fx.phase = ""
        return canvas

    curtain_frame("shutdown", 1500).save(str(out / "82-interruptor-ate-logo.png"))
    grid = QImage(W, H, QImage.Format.Format_RGB32)
    pt = QPainter(grid)
    pt.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    for idx, (k_act, t_ms, is_greet) in enumerate(
        (("restart", 1300, False), ("sleep", 1100, False), ("lock", 600, False), ("", 900, True))
    ):
        frame = curtain_frame(k_act, t_ms, is_greet).scaled(
            W // 2, H // 2, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        pt.drawImage((idx % 2) * (W // 2), (idx // 2) * (H // 2), frame)
    pt.end()
    grid.save(str(out / "83-interruptor-gestos.png"))

    shell.restore()
    for w in (*shell.docks, *shell.echos, shell.launcher, shell.thumbs):
        if w is not None:
            w.hide()


def datetime_now():  # noqa: ANN201
    from datetime import datetime

    return datetime.now()


if __name__ == "__main__":
    main()
