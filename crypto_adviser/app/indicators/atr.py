"""Robust ATR: полный ход свечи (high-low), выбросы >3× среднего исключаются."""

from __future__ import annotations

ATR_WINDOW = 30
OUTLIER_MULT = 3.0


def candle_ranges(
    bars: list[tuple[int, float, float, float, float]],
) -> list[float]:
    return [max(0.0, h - l) for _, _, h, l, _ in bars]


def robust_atr(
    bars: list[tuple[int, float, float, float, float]],
    *,
    window: int = ATR_WINDOW,
    outlier_mult: float = OUTLIER_MULT,
) -> float | None:
    """
    ATR по последним `window` закрытым свечам.
    Свечи с диапазоном > outlier_mult × среднего по окну не участвуют в итоге.
    """
    if len(bars) < window:
        return None
    chunk = bars[-window:]
    ranges = candle_ranges(chunk)
    if not ranges:
        return None
    mean_all = sum(ranges) / len(ranges)
    if mean_all <= 0:
        return mean_all
    filtered = [r for r in ranges if r <= outlier_mult * mean_all]
    if not filtered:
        return mean_all
    return sum(filtered) / len(filtered)
