from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def ema_series(closes: list[float], period: int) -> list[float | None]:
    """EMA по закрытиям; первые period-1 значений — None (не используйте для сигнала)."""
    n = len(closes)
    if n == 0 or period <= 0:
        return []
    out: list[float | None] = [None] * n
    if n < period:
        return out
    seed = sum(closes[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i in range(period, n):
        prev = closes[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def crossed_bullish(fast: list[float | None], slow: list[float | None]) -> bool:
    if len(fast) < 2 or len(slow) < 2:
        return False
    f0, f1 = fast[-2], fast[-1]
    s0, s1 = slow[-2], slow[-1]
    if f0 is None or f1 is None or s0 is None or s1 is None:
        return False
    return f0 <= s0 and f1 > s1


def crossed_bearish(fast: list[float | None], slow: list[float | None]) -> bool:
    if len(fast) < 2 or len(slow) < 2:
        return False
    f0, f1 = fast[-2], fast[-1]
    s0, s1 = slow[-2], slow[-1]
    if f0 is None or f1 is None or s0 is None or s1 is None:
        return False
    return f0 >= s0 and f1 < s1
