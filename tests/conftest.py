import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    data = tmp_path / "data"
    home = tmp_path / "home"
    data.mkdir()
    home.mkdir()
    monkeypatch.setenv("VENTUDE_HOME", str(data))
    monkeypatch.setenv("VENTUDE_USER_HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("GROK_HOME", raising=False)
    monkeypatch.delenv("GROK_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    # Cursor e Copilot procuram o login no AppData: nunca no da máquina que roda os testes.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata-vazio"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata-vazio"))
    # Nunca procura o Claude Code no WSL da máquina que roda os testes.
    monkeypatch.setattr("ventude.limits.claude._wsl_homes", lambda: [])
    return tmp_path


@pytest.fixture
def home(tmp_home):
    return tmp_home / "home"


@pytest.fixture
def store(tmp_home):
    from ventude.core.store import Store

    s = Store(tmp_home / "data" / "ventude.db")
    yield s
    s.close()


@pytest.fixture
def app_ctx(qapp, store, home):
    """Contexto com IAs de mentira. Os serviços que mexem no sistema não são iniciados (sem start())."""
    import time

    from ventude.core.tasks import Tasks
    from ventude.limits.model import Report, Window
    from ventude.services.media import MediaService
    from ventude.ui.context import AppContext

    now = time.time()
    fetchers = {
        "claude": lambda c: Report("claude", "ok", [Window("session", "Sessão · 5h", 55, now + 3600)], "Pro"),
        "antigravity": lambda c: Report("antigravity", "setup", message="Abra o Antigravity"),
        "grok": lambda c: Report("grok", "error", message="Cookie recusado"),
    }
    ctx = AppContext(store, Tasks(), media=MediaService(), fetchers=fetchers, home=home)
    store.save_settings(ctx.settings.copy(animation="rapida"))
    ctx.settings = store.load_settings()
    yield ctx
    ctx.tasks.shutdown()


@pytest.fixture
def island(qapp, app_ctx):
    from ventude.ui.island import Island

    widget = Island(app_ctx)
    widget.show()
    yield widget
    widget.hide()
    widget.deleteLater()


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from ventude.ui import theme

    theme.apply(app)
    return app
