"""Bollinger Bands (SMA middle)."""

from __future__ import annotations

import math


def bollinger_bands(
    closes: list[float],
    period: int = 20,
    std_mult: float = 2.0,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    n = len(closes)
    upper: list[float | None] = [None] * n
    middle: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    if n < period:
        return upper, middle, lower
    for i in range(period - 1, n):
        chunk = closes[i - period + 1 : i + 1]
        mid = sum(chunk) / period
        var = sum((x - mid) ** 2 for x in chunk) / period
        sd = math.sqrt(var)
        middle[i] = mid
        upper[i] = mid + std_mult * sd
        lower[i] = mid - std_mult * sd
    return upper, middle, lower


def bandwidth_at(upper: float, lower: float, middle: float) -> float:
    if middle <= 0:
        return 0.0
    return (upper - lower) / middle
