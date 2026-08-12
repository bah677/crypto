"""Уровни TP и стоп по спецификации."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LevelPair:
    first: float
    second: float


@dataclass(frozen=True)
class StopCalc:
    price: float
    distance: float
    pct: float


def nearest_levels_above(price: float, levels: list[float]) -> LevelPair | None:
    above = sorted(x for x in levels if x > price)
    if len(above) < 2:
        return None
    return LevelPair(first=above[0], second=above[1])


def nearest_levels_below(price: float, levels: list[float]) -> LevelPair | None:
    below = sorted((x for x in levels if x < price), reverse=True)
    if len(below) < 2:
        return None
    return LevelPair(first=below[0], second=below[1])


def min_stop_pct_for_symbol(symbol: str) -> float:
    sym = symbol.upper()
    if "XAU" in sym or sym.startswith("GOLD"):
        return 0.0004  # 0.04%
    return 0.0008  # 0.08% BTC/ETH/default


def calc_stop_loss(
    side: str,
    entry: float,
    atr_m1: float,
    *,
    min_pct: float | None = None,
    atr_mult: float = 1.5,
) -> StopCalc:
    pct_min = min_pct if min_pct is not None else min_stop_pct_for_symbol("")
    atr_dist = atr_mult * atr_m1
    pct_dist = entry * pct_min
    dist = max(atr_dist, pct_dist)
    dist = max(dist, entry * 0.0002)  # 0.02% floor
    dist = min(dist, entry * 0.02)  # 2% cap
    if side == "Buy":
        sl = entry - dist
    else:
        sl = entry + dist
    return StopCalc(price=sl, distance=dist, pct=dist / entry * 100.0)
