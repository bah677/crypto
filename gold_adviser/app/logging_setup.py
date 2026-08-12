"""Файловые логи в logs/."""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import PROJECT_ROOT

LOGS_DIR = PROJECT_ROOT / "logs"
BOT_LOG = LOGS_DIR / "bot.log"
ERR_LOG = LOGS_DIR / "err.log"

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def setup_file_logging(level_name: str = "INFO", *, also_console: bool = True) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.handlers.clear()
    level = getattr(logging, (level_name or "INFO").upper(), logging.INFO)
    root.setLevel(level)

    fmt = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FMT)

    bot_h = logging.FileHandler(BOT_LOG, encoding="utf-8")
    bot_h.setLevel(logging.INFO)
    bot_h.setFormatter(fmt)
    root.addHandler(bot_h)

    err_h = logging.FileHandler(ERR_LOG, encoding="utf-8")
    err_h.setLevel(logging.ERROR)
    err_h.setFormatter(fmt)
    root.addHandler(err_h)

    if also_console:
        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(fmt)
        root.addHandler(ch)
