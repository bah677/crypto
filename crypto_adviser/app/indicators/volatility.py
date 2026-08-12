from __future__ import annotations

from typing import Sequence


def candle_range_pct(high: float, low: float, close: float) -> float:
    """Полный ход свечи (тень–тень), % от close."""
    if close <= 0:
        return 0.0
    return max(0.0, high - low) / close * 100.0


def _shadow_range(high: float, low: float, close: float) -> float:
    """Размах свечи с тенями, доля от close (0..1, для сравнения между свечами)."""
    if close <= 0:
        return 0.0
    return max(0.0, high - low) / close


def last_two_candles_high_volatility(
    ohlc: Sequence[tuple[float, float, float, float]],
    *,
    factor: float,
) -> bool:
    """
    ohlc: (open, high, low, close) по порядку времени, все свечи в расчёте EMA до сигнала включительно.
    Среднее — по всем кроме двух последних; предупреждение, если обе последние выше factor × среднее.
    """
    n = len(ohlc)
    if n < 3:
        return False

    ranges = [_shadow_range(h, l, c) for _, h, l, c in ohlc]
    baseline = ranges[:-2]
    avg = sum(baseline) / len(baseline)
    if avg <= 0:
        return False

    threshold = avg * factor
    return ranges[-2] > threshold and ranges[-1] > threshold
