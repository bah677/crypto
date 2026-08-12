"""Сопровождение виртуальной сделки: SL, TP, PnL."""

from __future__ import annotations

from dataclasses import dataclass

from app.indicators.ema import ema_series
from app.indicators.wilder import atr_wilder
from app.scalp_advisor.logic import EMA_FAST, ATR_PERIOD

BE_FEE_BUFFER_PCT = 0.0001  # 0.01% буфер к безубытку


@dataclass
class TradeSnapshot:
    side: str
    entry: float
    initial_sl: float
    sl: float
    tp1: float
    tp2: float
    tp1_hit: bool
    tp2_hit: bool


@dataclass(frozen=True)
class CloseEvent:
    exit_price: float
    reason: str
    pnl_r: float


def pnl_r(side: str, entry: float, exit_price: float, initial_sl: float) -> float:
    """Результат в R: 1R = |entry − initial_sl|."""
    risk = abs(entry - initial_sl)
    if risk <= 0:
        return 0.0
    if side == "Buy":
        return (exit_price - entry) / risk
    return (entry - exit_price) / risk


def check_close(
    snap: TradeSnapshot,
    mark: float,
) -> CloseEvent | None:
    side = snap.side
    if side == "Buy":
        if mark <= snap.sl:
            r = pnl_r(side, snap.entry, snap.sl, snap.initial_sl)
            return CloseEvent(snap.sl, "SL", r)
        if mark >= snap.tp2:
            r = pnl_r(side, snap.entry, snap.tp2, snap.initial_sl)
            return CloseEvent(snap.tp2, "TP2", r)
    else:
        if mark >= snap.sl:
            r = pnl_r(side, snap.entry, snap.sl, snap.initial_sl)
            return CloseEvent(snap.sl, "SL", r)
        if mark <= snap.tp2:
            r = pnl_r(side, snap.entry, snap.tp2, snap.initial_sl)
            return CloseEvent(snap.tp2, "TP2", r)
    return None


def update_tp_flags(snap: TradeSnapshot, mark: float) -> tuple[bool, bool]:
    tp1 = snap.tp1_hit
    tp2 = snap.tp2_hit
    if snap.side == "Buy":
        if not tp1 and mark >= snap.tp1:
            tp1 = True
        if not tp2 and mark >= snap.tp2:
            tp2 = True
    else:
        if not tp1 and mark <= snap.tp1:
            tp1 = True
        if not tp2 and mark <= snap.tp2:
            tp2 = True
    return tp1, tp2


def compute_trail_sl(
    snap: TradeSnapshot,
    m5_bars: list,
) -> float:
    """Новый SL: BE после TP1, трейл M5 после TP2; только ужесточение."""
    side = snap.side
    sl = snap.sl

    if snap.tp1_hit and not snap.tp2_hit:
        buf = snap.entry * BE_FEE_BUFFER_PCT
        be = snap.entry + buf if side == "Buy" else snap.entry - buf
        if side == "Buy":
            sl = max(sl, be)
        else:
            sl = min(sl, be)

    if snap.tp2_hit and len(m5_bars) >= 40:
        idx = len(m5_bars) - 1
        closes = [b[4] for b in m5_bars]
        ema = ema_series(closes, EMA_FAST)
        atr = atr_wilder(m5_bars, ATR_PERIOD)
        e, a = ema[idx], atr[idx]
        if e is not None and a is not None and a > 0:
            trail = e - a if side == "Buy" else e + a
            if side == "Buy":
                sl = max(sl, trail)
            else:
                sl = min(sl, trail)

    return sl


def sl_tightened(side: str, old_sl: float, new_sl: float, eps: float = 1e-8) -> bool:
    if side == "Buy":
        return new_sl > old_sl + eps
    return new_sl < old_sl - eps
