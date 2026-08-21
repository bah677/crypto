"""Логика скальп-советника: M5 setup + M1 вход."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.atr_pullback.logic import last_closed_bar_index_at
from app.indicators.bollinger import bandwidth_at, bollinger_bands
from app.indicators.ema import ema_series
from app.indicators.wilder import adx_wilder, atr_wilder
from app.scalp_advisor.levels import (
    LevelPair,
    calc_stop_loss,
    min_stop_pct_for_symbol,
    nearest_levels_above,
    nearest_levels_below,
)
from app.scalp_advisor.strategy_params import ScalpStrategyParams, default_scalp_strategy
from app.scalp_advisor.patterns import (
    bearish_engulfing,
    bearish_pin_bar,
    bullish_engulfing,
    bullish_pin_bar,
    pattern_label,
)

EMA_FAST = 20
EMA_SLOW = 50
ATR_PERIOD = 14
ADX_PERIOD = 14
M5_PULLBACK_ATR = 1.5
M1_TOUCH_ATR = 1.0
ROOM_TO_TP_ATR = 1.5
M5_CROSS_LOOKBACK = 24
NOISE_ADX_MIN = 20.0
NOISE_BODY_BARS = 6
NOISE_BODY_WINDOW = 10
NOISE_BODY_RATIO = 0.30
BB_PERIOD = 20
BB_STD = 2.0
BB_MIN_BANDWIDTH = 0.0015  # 0.15% — не сжатие


def _bollinger_ok(m1_bars: list, m1_idx: int, side: str, cfg: ScalpStrategyParams) -> bool:
    return _bollinger_detail(m1_bars, m1_idx, side, cfg)["ok"]


def _bollinger_detail(
    m1_bars: list, m1_idx: int, side: str, cfg: ScalpStrategyParams
) -> dict:
    if not cfg.bb_enabled:
        return {"ok": True, "skipped": True, "disabled": True}
    closes = [b[4] for b in m1_bars[: m1_idx + 1]]
    out: dict = {"ok": False, "bandwidth": None, "close": None, "middle": None}
    if len(closes) < cfg.bb_period + 2:
        out["fail"] = "insufficient_bars"
        return out
    upper, middle, lower = bollinger_bands(closes, cfg.bb_period, cfg.bb_std)
    u, m, lo = upper[m1_idx], middle[m1_idx], lower[m1_idx]
    if u is None or m is None or lo is None:
        out["fail"] = "bands_na"
        return out
    bw = bandwidth_at(u, lo, m)
    close = closes[m1_idx]
    out.update(
        {
            "bandwidth": round(bw, 6),
            "min_bandwidth": cfg.bb_min_bandwidth,
            "close": round(close, 8),
            "middle": round(m, 8),
            "upper": round(u, 8),
            "lower": round(lo, 8),
        }
    )
    if bw < cfg.bb_min_bandwidth:
        out["fail"] = "squeeze"
        return out
    if side == "Buy":
        out["ok"] = close <= m
        if not out["ok"]:
            out["fail"] = "close_above_middle"
    else:
        out["ok"] = close >= m
        if not out["ok"]:
            out["fail"] = "close_below_middle"
    return out


@dataclass(frozen=True)
class ScalpSignal:
    side: str  # Buy | Sell
    entry: float
    sl: float
    sl_pct: float
    tp1: float
    tp2: float
    pattern: str
    adx_m1: float
    m5_pullback_atr: float
    room_r1_atr: float
    m5_cross_age_bars: int


def _cross_within_lookback(
    closes: list[float],
    ema_fast: int,
    ema_slow: int,
    side: str,
    lookback: int,
) -> int | None:
    """Бars ago of last cross (0=latest bar), or None."""
    fast = ema_series(closes, ema_fast)
    slow = ema_series(closes, ema_slow)
    start = max(1, len(closes) - lookback)
    last: int | None = None
    for i in range(start, len(closes)):
        f0, f1 = fast[i - 1], fast[i]
        s0, s1 = slow[i - 1], slow[i]
        if f0 is None or f1 is None or s0 is None or s1 is None:
            continue
        if side == "Buy" and f0 <= s0 and f1 > s1:
            last = len(closes) - 1 - i
        elif side == "Sell" and f0 >= s0 and f1 < s1:
            last = len(closes) - 1 - i
    return last


def _m5_setup_detail(
    m5_bars: list, m5_idx: int, side: str, cfg: ScalpStrategyParams
) -> dict[str, Any]:
    closes = [b[4] for b in m5_bars[: m5_idx + 1]]
    out: dict[str, Any] = {"ok": False, "side": side}
    if len(closes) < cfg.ema_slow + 5:
        out["fail"] = "insufficient_bars"
        return out

    cross_age = _cross_within_lookback(
        closes, cfg.ema_fast, cfg.ema_slow, side, cfg.m5_cross_lookback
    )
    out["cross_age_bars"] = cross_age
    if cfg.m5_cross_enabled:
        if cross_age is None:
            out["fail"] = "no_cross"
            return out
    else:
        out["cross_skipped"] = True

    fast = ema_series(closes, cfg.ema_fast)
    atr = atr_wilder(m5_bars[: m5_idx + 1], cfg.atr_period)
    f, a = fast[m5_idx], atr[m5_idx]
    close = closes[m5_idx]
    slow = ema_series(closes, cfg.ema_slow)[m5_idx]
    out["close"] = round(close, 8)
    out["ema_fast"] = round(f, 8) if f is not None else None
    out["ema_slow"] = round(slow, 8) if slow is not None else None
    if f is None or a is None or a <= 0 or slow is None:
        out["fail"] = "indicators_na"
        return out
    out["atr"] = round(a, 8)

    if cfg.m5_trend_enabled:
        if side == "Buy" and f <= slow:
            out["fail"] = "fast_below_slow"
            return out
        if side == "Sell" and f >= slow:
            out["fail"] = "fast_above_slow"
            return out
    else:
        out["trend_skipped"] = True

    dist_atr = abs(close - f) / a
    out["pullback_atr"] = round(dist_atr, 4)
    out["pullback_max"] = cfg.m5_pullback_max_atr
    if cfg.m5_pullback_enabled:
        if dist_atr > cfg.m5_pullback_max_atr:
            out["fail"] = "pullback_too_far"
            return out
    else:
        out["pullback_skipped"] = True

    out["ok"] = True
    return out


def _m1_noise_detail(
    m1_bars: list, m1_idx: int, side: str, cfg: ScalpStrategyParams
) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "side": side}
    if not (cfg.m1_adx_enabled or cfg.m1_bodies_enabled or cfg.m1_impulse_enabled):
        out["ok"] = True
        out["skipped"] = True
        out["disabled"] = True
        return out

    if m1_idx < cfg.m1_body_window:
        out["fail"] = "insufficient_bars"
        return out

    window = m1_bars[m1_idx - cfg.m1_body_window + 1 : m1_idx + 1]
    _, _, adx = adx_wilder(m1_bars[: m1_idx + 1], cfg.adx_period)
    adx_v = adx[m1_idx]
    out["adx"] = round(adx_v, 2) if adx_v is not None else None
    out["adx_min"] = cfg.m1_adx_min

    if cfg.m1_adx_enabled:
        if adx_v is None or adx_v <= cfg.m1_adx_min:
            out["fail"] = "adx_low"
            return out
    else:
        out["adx_skipped"] = True

    body_count = 0
    for _, o, h, l, c in window:
        rng = h - l
        if rng <= 0:
            continue
        if abs(c - o) / rng > cfg.m1_body_ratio:
            body_count += 1
    out["body_bars"] = body_count
    out["body_bars_min"] = cfg.m1_body_bars_min
    if cfg.m1_bodies_enabled:
        if body_count < cfg.m1_body_bars_min:
            out["fail"] = "body_bars"
            return out
    else:
        out["bodies_skipped"] = True

    closes = [b[4] for b in window]
    last3 = closes[-3:]
    out["last3_closes"] = [round(x, 8) for x in last3]
    if cfg.m1_impulse_enabled:
        if side == "Buy":
            if not (last3[0] < last3[1] < last3[2]):
                out["fail"] = "impulse_not_up"
                return out
        else:
            if not (last3[0] > last3[1] > last3[2]):
                out["fail"] = "impulse_not_down"
                return out
    else:
        out["impulse_skipped"] = True

    out["ok"] = True
    return out


def _m1_entry_detail(
    m1_bars: list, m1_idx: int, side: str, cfg: ScalpStrategyParams
) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "side": side, "pattern": ""}
    if not cfg.m1_entry_enabled:
        out["ok"] = True
        out["skipped"] = True
        out["disabled"] = True
        out["pattern"] = "—"
        closes = [b[4] for b in m1_bars[: m1_idx + 1]]
        atr = atr_wilder(m1_bars[: m1_idx + 1], cfg.atr_period)
        a = atr[m1_idx]
        out["atr"] = round(a, 8) if a is not None else None
        return out

    if m1_idx < 3:
        out["fail"] = "insufficient_bars"
        return out
    closes = [b[4] for b in m1_bars[: m1_idx + 1]]
    fast = ema_series(closes, cfg.ema_fast)
    atr = atr_wilder(m1_bars[: m1_idx + 1], cfg.atr_period)
    f, a = fast[m1_idx], atr[m1_idx]
    if f is None or a is None or a <= 0:
        out["fail"] = "indicators_na"
        return out
    out["ema20"] = round(f, 8)
    out["atr"] = round(a, 8)

    o, h, l, c = m1_bars[m1_idx][1], m1_bars[m1_idx][2], m1_bars[m1_idx][3], m1_bars[m1_idx][4]
    prev = m1_bars[m1_idx - 1]
    out["close"] = round(c, 8)
    eps = cfg.m1_close_ema_eps_atr * a

    if side == "Buy":
        if c > f + eps:
            out["fail"] = "close_above_ema"
            return out
        lows = [b[3] for b in m1_bars[m1_idx - 2 : m1_idx + 1]]
        min_low = min(lows)
        touch = abs(min_low - f) / a
        out["ema_touch_atr"] = round(touch, 4)
        out["touch_max"] = cfg.m1_touch_max_atr
        if touch > cfg.m1_touch_max_atr:
            out["fail"] = "no_ema_touch"
            return out
        pin = bullish_pin_bar(o, h, l, c)
        eng = bullish_engulfing(
            (prev[1], prev[2], prev[3], prev[4]),
            (o, h, l, c),
        )
        out["pin"] = pin
        out["engulf"] = eng
        if not pin and not eng:
            out["fail"] = "no_pattern"
            return out
        out["pattern"] = pattern_label("Buy", pin, eng)
    else:
        if c < f - eps:
            out["fail"] = "close_below_ema"
            return out
        highs = [b[2] for b in m1_bars[m1_idx - 2 : m1_idx + 1]]
        max_high = max(highs)
        touch = abs(max_high - f) / a
        out["ema_touch_atr"] = round(touch, 4)
        out["touch_max"] = cfg.m1_touch_max_atr
        if touch > cfg.m1_touch_max_atr:
            out["fail"] = "no_ema_touch"
            return out
        pin = bearish_pin_bar(o, h, l, c)
        eng = bearish_engulfing(
            (prev[1], prev[2], prev[3], prev[4]),
            (o, h, l, c),
        )
        out["pin"] = pin
        out["engulf"] = eng
        if not pin and not eng:
            out["fail"] = "no_pattern"
            return out
        out["pattern"] = pattern_label("Sell", pin, eng)
    out["ok"] = True
    return out


def _room_detail(
    side: str,
    entry: float,
    atr_m1: float,
    levels: list[float],
    cfg: ScalpStrategyParams,
) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "entry": round(entry, 8), "atr_m1": round(atr_m1, 8)}
    if not cfg.room_tp_enabled:
        out["ok"] = True
        out["skipped"] = True
        out["disabled"] = True
        return out
    if side == "Buy":
        pair = nearest_levels_above(entry, levels)
        if pair is None:
            out["fail"] = "no_levels_above"
            return out
        room = pair.first - entry
    else:
        pair = nearest_levels_below(entry, levels)
        if pair is None:
            out["fail"] = "no_levels_below"
            return out
        room = entry - pair.first
    room_atr = room / atr_m1 if atr_m1 > 0 else 0.0
    out["tp1"] = round(pair.first, 8)
    out["tp2"] = round(pair.second, 8)
    out["room_atr"] = round(room_atr, 4)
    out["room_min"] = cfg.room_tp_min_atr
    if room_atr < cfg.room_tp_min_atr:
        out["fail"] = "room_too_small"
        return out
    out["ok"] = True
    return out


def detect_scalp_signal(
    *,
    m5_bars: list,
    m1_bars: list,
    levels: list[float],
    symbol: str,
    m1_close_ms: int,
    cfg: ScalpStrategyParams | None = None,
    m5_interval: str = "5",
    m1_interval: str = "1",
    debug_out: dict[str, Any] | None = None,
) -> ScalpSignal | None:
    p = cfg or default_scalp_strategy()
    if len(m1_bars) < 50 or len(m5_bars) < 60:
        if debug_out is not None:
            debug_out["fail"] = "insufficient_bars"
            debug_out["bars"] = {"m1": len(m1_bars), "m5": len(m5_bars)}
        return None
    m1_idx = len(m1_bars) - 1
    m5_idx = last_closed_bar_index_at(m5_bars, m1_close_ms, m5_interval)
    if m5_idx is None:
        if debug_out is not None:
            debug_out["fail"] = "m5_align"
        return None
    if debug_out is not None:
        debug_out["m1_idx"] = m1_idx
        debug_out["m5_idx"] = m5_idx
        debug_out["sides"] = {}
        debug_out["strategy_rev"] = p.revision

    for side in ("Buy", "Sell"):
        side_dbg: dict[str, Any] = {}
        if debug_out is not None:
            debug_out["sides"][side] = side_dbg

        m5_d = _m5_setup_detail(m5_bars, m5_idx, side, p)
        side_dbg["m5"] = m5_d
        if not m5_d["ok"]:
            side_dbg["fail"] = f"m5:{m5_d.get('fail')}"
            continue
        pb_atr = m5_d.get("pullback_atr") or 0.0
        cross_age = m5_d.get("cross_age_bars") or 0

        noise_d = _m1_noise_detail(m1_bars, m1_idx, side, p)
        side_dbg["noise"] = noise_d
        if not noise_d["ok"]:
            side_dbg["fail"] = f"noise:{noise_d.get('fail')}"
            continue
        adx_v = noise_d.get("adx") or 0.0

        bb_d = _bollinger_detail(m1_bars, m1_idx, side, p)
        side_dbg["bb"] = bb_d
        if not bb_d["ok"]:
            side_dbg["fail"] = f"bb:{bb_d.get('fail')}"
            continue

        entry_d = _m1_entry_detail(m1_bars, m1_idx, side, p)
        side_dbg["entry"] = entry_d
        if not entry_d["ok"]:
            side_dbg["fail"] = f"entry:{entry_d.get('fail')}"
            continue
        pattern = entry_d.get("pattern") or ""
        atr_m1 = entry_d.get("atr")
        if atr_m1 is None or atr_m1 <= 0:
            side_dbg["fail"] = "entry:no_atr"
            continue

        entry = m1_bars[m1_idx][4]
        room_d = _room_detail(side, entry, float(atr_m1), levels, p)
        side_dbg["room"] = room_d
        if not room_d["ok"]:
            side_dbg["fail"] = f"room:{room_d.get('fail')}"
            continue
        pair_first = room_d.get("tp1", entry)
        pair_second = room_d.get("tp2", entry)
        room_atr = room_d.get("room_atr") or 0.0

        stop = calc_stop_loss(
            side,
            entry,
            float(atr_m1),
            min_pct=min_stop_pct_for_symbol(symbol),
            atr_mult=p.sl_atr_mult,
        )
        side_dbg["ok"] = True
        if debug_out is not None:
            debug_out["winner"] = side
        return ScalpSignal(
            side=side,
            entry=entry,
            sl=stop.price,
            sl_pct=stop.pct,
            tp1=float(pair_first),
            tp2=float(pair_second),
            pattern=pattern,
            adx_m1=float(adx_v),
            m5_pullback_atr=float(pb_atr),
            room_r1_atr=float(room_atr),
            m5_cross_age_bars=int(cross_age),
        )
    if debug_out is not None:
        debug_out["fail"] = "no_side"
    return None
