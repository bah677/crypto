from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)


def interval_step_ms(interval: str) -> int:
    if interval in ("D", "W", "M"):
        return {"D": 86400_000, "W": 604800_000, "M": 2592000_000}[interval]
    return int(interval) * 60_000


def kline_interval_to_mt5_timeframe(mt5: Any, interval: str) -> int:
    """Соответствие строки интервала (как у Bybit в задании) константе TIMEFRAME_* MT5."""
    m = {
        "1": mt5.TIMEFRAME_M1,
        "2": mt5.TIMEFRAME_M2,
        "3": mt5.TIMEFRAME_M3,
        "4": mt5.TIMEFRAME_M4,
        "5": mt5.TIMEFRAME_M5,
        "6": mt5.TIMEFRAME_M6,
        "10": mt5.TIMEFRAME_M10,
        "12": mt5.TIMEFRAME_M12,
        "15": mt5.TIMEFRAME_M15,
        "20": mt5.TIMEFRAME_M20,
        "30": mt5.TIMEFRAME_M30,
        "60": mt5.TIMEFRAME_H1,
        "120": mt5.TIMEFRAME_H2,
        "180": mt5.TIMEFRAME_H3,
        "240": mt5.TIMEFRAME_H4,
        "360": mt5.TIMEFRAME_H6,
        "480": mt5.TIMEFRAME_H8,
        "720": mt5.TIMEFRAME_H12,
        "D": mt5.TIMEFRAME_D1,
        "W": mt5.TIMEFRAME_W1,
        "M": mt5.TIMEFRAME_MN1,
    }
    if interval not in m:
        raise ValueError(
            f"Интервал {interval!r} не поддержан для MT5. "
            f"Допустимы: {', '.join(sorted(m, key=lambda x: (len(x) > 1, x)))}."
        )
    return m[interval]


def closed_bars_with_ts(
    mt5: Any,
    symbol: str,
    interval: str,
    *,
    limit: int = 500,
) -> list[tuple[int, float]]:
    """Закрытые бары: (openTime_ms, close). Последняя — только что закрывшаяся."""
    tf = kline_interval_to_mt5_timeframe(mt5, interval)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, limit)
    if rates is None or len(rates) == 0:
        return []
    step_ms = interval_step_ms(interval)
    now_ms = int(time.time() * 1000)
    out: list[tuple[int, float]] = []
    for r in rates:
        t_open_ms = int(r["time"]) * 1000
        if t_open_ms + step_ms <= now_ms:
            out.append((t_open_ms, float(r["close"])))
    out.sort(key=lambda x: x[0])
    return out
