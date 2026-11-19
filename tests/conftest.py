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


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from ventude.ui import theme

    theme.apply(app)
    return app
