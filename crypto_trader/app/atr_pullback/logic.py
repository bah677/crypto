"""Условия ATR Pullback: зона интереса, шаги 1/2, расчёт SL."""

from __future__ import annotations

from app.bybit.rest import _interval_to_ms
from app.indicators.atr import robust_atr
from app.indicators.ema import crossed_bearish, crossed_bullish, ema_series
from app.indicators.ema_cross_price import next_close_ema_cross_price

PULLBACK_ATR_MULT = 1.5
SL_ATR_MULT = 1.0


def in_interest_zone(
    side: str,
    close: float,
    fast: list[float | None],
    slow: list[float | None],
    idx: int,
) -> bool:
    """Long: close > fast и close > slow; Short: наоборот."""
    f, s = fast[idx], slow[idx]
    if f is None or s is None:
        return False
    if side == "Buy":
        return close > f and close > s
    return close < f and close < s


def last_closed_bar_index_at(
    bars: list[tuple[int, float, float, float, float]],
    at_close_ms: int,
    interval: str,
) -> int | None:
    step = _interval_to_ms(interval)
    best: int | None = None
    for i, (open_ms, *_rest) in enumerate(bars):
        if open_ms + step <= at_close_ms:
            best = i
    return best


def detect_btf_cross(
    closes: list[float],
    ema_fast: int,
    ema_slow: int,
) -> str | None:
    fast = ema_series(closes, ema_fast)
    slow = ema_series(closes, ema_slow)
    if crossed_bullish(fast, slow):
        return "Buy"
    if crossed_bearish(fast, slow):
        return "Sell"
    return None


def cross_price_at_bar(closes: list[float], bar_index: int, ema_fast: int, ema_slow: int) -> float | None:
    if bar_index < 1:
        return None
    return next_close_ema_cross_price(closes[:bar_index], ema_fast, ema_slow)


def initial_stop_loss(side: str, cross_price: float, atr_mtf: float) -> float:
    if side == "Buy":
        return cross_price - SL_ATR_MULT * atr_mtf
    return cross_price + SL_ATR_MULT * atr_mtf


def trail_stop_loss(
    side: str,
    slow_ema: float,
    atr_mtf: float,
) -> float:
    if side == "Buy":
        return slow_ema - SL_ATR_MULT * atr_mtf
    return slow_ema + SL_ATR_MULT * atr_mtf


def pullback_ready(
    side: str,
    close: float,
    fast_val: float,
    atr_mtf: float,
) -> bool:
    dist = abs(close - fast_val)
    return dist <= PULLBACK_ATR_MULT * atr_mtf


def ema_at_index(
    closes: list[float],
    idx: int,
    ema_fast: int,
    ema_slow: int,
) -> tuple[float | None, float | None]:
    fast = ema_series(closes, ema_fast)
    slow = ema_series(closes, ema_slow)
    if idx < 0 or idx >= len(fast):
        return None, None
    return fast[idx], slow[idx]
