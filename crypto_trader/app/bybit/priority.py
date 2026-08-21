"""Приоритет и сериализация запросов Bybit (лимит 10006)."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Literal

log = logging.getLogger(__name__)

Priority = Literal["signal", "background"]

_lock = threading.Lock()
_last_request_at = 0.0
_advisor_depth = 0
_background_active = 0

_GAP_SIGNAL_S = 0.28
_GAP_BACKGROUND_S = 0.4
_BACKGROUND_WAIT_ADVISOR_S = 45.0


def _wait_advisor_idle(timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with _lock:
            if _advisor_depth == 0:
                return True
        time.sleep(0.08)
    with _lock:
        return _advisor_depth == 0


@contextmanager
def advisor_tick_scope():
    """Советчик держит приоритет — фоновые тики не стартуют."""
    global _advisor_depth
    with _lock:
        _advisor_depth += 1
    try:
        yield
    finally:
        with _lock:
            _advisor_depth -= 1


def try_begin_background_tick(name: str) -> bool:
    """False — пропустить фоновый тик (price spike / ema sl), пока идёт советчик."""
    global _background_active
    with _lock:
        if _advisor_depth > 0 or _background_active > 0:
            return False
        _background_active += 1
    return True


def end_background_tick() -> None:
    global _background_active
    with _lock:
        if _background_active > 0:
            _background_active -= 1


_tls = threading.local()


@contextmanager
def background_request_scope():
    _tls.priority = "background"
    try:
        yield
    finally:
        _tls.priority = "signal"


@contextmanager
def bybit_api_slot(*, priority: Priority | None = None):
    """
    Пауза между запросами + один поток в HTTP (внутри with).
    Вызывать: with bybit_api_slot(): ... http ...
    """
    global _last_request_at
    prio: Priority = priority or getattr(_tls, "priority", "signal")

    if prio == "background":
        if not _wait_advisor_idle(_BACKGROUND_WAIT_ADVISOR_S):
            log.debug("Bybit background: советчик занят — ждём слот")
        gap = _GAP_BACKGROUND_S
    else:
        gap = _GAP_SIGNAL_S

    with _lock:
        now = time.monotonic()
        wait = gap - (now - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()
        try:
            yield
        finally:
            pass


def throttle_before_bybit_request() -> None:
    """Устаревший вызов — предпочтительно bybit_api_slot()."""
    with bybit_api_slot():
        return
