"""Кэш свечей Bybit — меньше дублей при опросе заданий."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from app.bybit.rest import _filter_closed_bars, _interval_to_ms

if TYPE_CHECKING:
    from app.bybit.rest import BybitRest

_lock = threading.Lock()
_cache: dict[tuple[str, str, str, int], tuple[float, list]] = {}


def _ttl_seconds(interval: str) -> float:
    step_s = _interval_to_ms(interval) / 1000.0
    return max(3.0, min(12.0, step_s * 0.2))


def get_closed_ohlc_bars(
    client: "BybitRest",
    symbol: str,
    interval: str,
    *,
    limit: int = 200,
) -> list[tuple[int, float, float, float, float]]:
    key = (client.category, symbol.upper(), interval, limit)
    ttl = _ttl_seconds(interval)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < ttl:
            return list(hit[1])

    raw = client.get_kline_ohlc(symbol, interval, limit=limit)
    bars = _filter_closed_bars(raw, interval)
    with _lock:
        _cache[key] = (time.monotonic(), bars)
    return bars
