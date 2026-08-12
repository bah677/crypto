"""Свечные паттерны M1."""

from __future__ import annotations


def _range(h: float, l: float) -> float:
    return max(h - l, 1e-12)


def bullish_pin_bar(o: float, h: float, l: float, c: float) -> bool:
    rng = _range(h, l)
    body = abs(c - o)
    lower_shadow = min(o, c) - l
    return c > o and lower_shadow >= (2.0 / 3.0) * rng and body < rng * 0.4


def bearish_pin_bar(o: float, h: float, l: float, c: float) -> bool:
    rng = _range(h, l)
    body = abs(c - o)
    upper_shadow = h - max(o, c)
    return c < o and upper_shadow >= (2.0 / 3.0) * rng and body < rng * 0.4


def bullish_engulfing(
    prev: tuple[float, float, float, float],
    cur: tuple[float, float, float, float],
) -> bool:
    po, _, _, pc = prev
    co, _, _, cc = cur
    if cc <= co:
        return False
    prev_lo, prev_hi = min(po, pc), max(po, pc)
    cur_lo, cur_hi = min(co, cc), max(co, cc)
    return cur_lo <= prev_lo and cur_hi >= prev_hi


def bearish_engulfing(
    prev: tuple[float, float, float, float],
    cur: tuple[float, float, float, float],
) -> bool:
    po, _, _, pc = prev
    co, _, _, cc = cur
    if cc >= co:
        return False
    prev_lo, prev_hi = min(po, pc), max(po, pc)
    cur_lo, cur_hi = min(co, cc), max(co, cc)
    return cur_lo <= prev_lo and cur_hi >= prev_hi


def pattern_label(side: str, pin: bool, engulf: bool) -> str:
    if side == "Buy":
        if pin:
            return "бычий пин-бар"
        if engulf:
            return "бычье поглощение"
    else:
        if pin:
            return "медвежий пин-бар"
        if engulf:
            return "медвежье поглощение"
    return "—"
