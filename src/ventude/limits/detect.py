"""Descobre quais IAs opcionais já estão instaladas e logadas neste PC."""

from __future__ import annotations

import logging
from pathlib import Path

from ventude.limits import codex, copilot, cursor

log = logging.getLogger(__name__)

OPTIONAL = ("codex", "cursor", "copilot")


def installed(home: Path) -> list[str]:
    """IAs opcionais com login local encontrado (lê arquivos; rode fora da interface)."""
    checks = {
        "codex": lambda: codex.read_login(home) is not None,
        "cursor": lambda: cursor.read_login(home) is not None,
        "copilot": lambda: copilot.read_token(home) is not None,
    }
    found = []
    for provider, check in checks.items():
        try:
            if check():
                found.append(provider)
        except Exception:  # noqa: BLE001
            log.debug("Detecção de %s falhou", provider, exc_info=True)
    return found
