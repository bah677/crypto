"""Цена закрытия следующей свечи, при которой EMA fast = EMA slow."""

from __future__ import annotations

from app.indicators.ema import ema_series


def next_close_ema_cross_price(
    closes: list[float],
    ema_fast: int,
    ema_slow: int,
) -> float | None:
    """
    При закрытии следующего бара по цене P:
    EMA_fast(P) = alpha_f*P + (1-alpha_f)*F
    EMA_slow(P) = alpha_s*P + (1-alpha_s)*S
    """
    if ema_fast == ema_slow:
        return None
    fast = ema_series(closes, ema_fast)
    slow = ema_series(closes, ema_slow)
    f_prev, s_prev = fast[-1], slow[-1]
    if f_prev is None or s_prev is None:
        return None
    kf = 2.0 / (ema_fast + 1)
    ks = 2.0 / (ema_slow + 1)
    denom = kf - ks
    if abs(denom) < 1e-15:
        return None
    return ((1.0 - ks) * s_prev - (1.0 - kf) * f_prev) / denom
