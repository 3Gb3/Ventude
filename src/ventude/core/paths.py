from __future__ import annotations

import os
from pathlib import Path


def app_data_dir() -> Path:
    """Pasta de dados (%APPDATA%\\Ventude). VENTUDE_HOME sobrescreve (testes)."""
    override = os.environ.get("VENTUDE_HOME")
    if override:
        base = Path(override)
    else:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) / "Ventude" if appdata else Path.home() / ".ventude"
    base.mkdir(parents=True, exist_ok=True)
    return base


def user_home() -> Path:
    """Pasta do usuário onde ficam .claude, .grok etc. VENTUDE_USER_HOME sobrescreve."""
    override = os.environ.get("VENTUDE_USER_HOME")
    return Path(override) if override else Path.home()
