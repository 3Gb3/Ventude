from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from ventude.core.paths import app_data_dir


def setup_logging(level: int = logging.INFO) -> None:
    handler = RotatingFileHandler(
        app_data_dir() / "ventude.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
