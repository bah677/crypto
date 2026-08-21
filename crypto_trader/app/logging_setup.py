"""Файловые логи в logs/bot.log (INFO+) и logs/err.log (ERROR+), ротация при старте."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from app.config import PROJECT_ROOT

LOGS_DIR = PROJECT_ROOT / "logs"
ARCHIVE_DIR = LOGS_DIR / "arc"
BOT_LOG = LOGS_DIR / "bot.log"
ERR_LOG = LOGS_DIR / "err.log"
BOT_LOG_MAX_LINES = 1000

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def _archive_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d-%H-%M-%S")


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def _archive_file(src: Path, prefix: str) -> Path | None:
    if not src.is_file() or src.stat().st_size == 0:
        return None
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dst = ARCHIVE_DIR / f"{prefix}_{_archive_stamp()}.log"
    shutil.move(str(src), str(dst))
    return dst


def rotate_logs_on_startup() -> None:
    """При каждом запуске: err.log → arc; bot.log → arc если строк > 1000."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _archive_file(ERR_LOG, "err")
    if _line_count(BOT_LOG) > BOT_LOG_MAX_LINES:
        _archive_file(BOT_LOG, "bot")


def setup_file_logging(level_name: str = "INFO", *, also_console: bool = True) -> None:
    """
    Настраивает корневой logger: logs/bot.log, logs/err.log.
    Вызывать после rotate_logs_on_startup().
    """
    rotate_logs_on_startup()

    level = getattr(logging, level_name.upper(), logging.INFO)
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FMT)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    bot_handler = logging.FileHandler(BOT_LOG, encoding="utf-8", mode="a")
    bot_handler.setLevel(level)
    bot_handler.setFormatter(formatter)
    root.addHandler(bot_handler)

    err_handler = logging.FileHandler(ERR_LOG, encoding="utf-8", mode="a")
    err_handler.setLevel(logging.ERROR)
    err_handler.setFormatter(formatter)
    root.addHandler(err_handler)

    if also_console:
        console = logging.StreamHandler()
        console.setLevel(level)
        console.setFormatter(formatter)
        root.addHandler(console)

    # «skipped: max instances» — штатно при долгом advisor tick; не пишем в bot.log
    logging.getLogger("apscheduler").setLevel(logging.ERROR)
    # pybit при 10006 пишет ERROR на каждый retry — дублирует err.log
    logging.getLogger("pybit").setLevel(logging.CRITICAL)
    logging.getLogger("pybit._http_manager").setLevel(logging.CRITICAL)

    logging.getLogger(__name__).debug(
        "Логи: %s (≥%s), %s (ERROR+), архив: %s",
        BOT_LOG,
        level_name.upper(),
        ERR_LOG,
        ARCHIVE_DIR,
    )
