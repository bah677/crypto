"""Детекция пампа и дампа на нескольких таймфреймах."""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bybit.rest import BybitRest, _interval_to_ms
from app.market.lunarcrush import SocialSnapshot, fetch_symbol_social
from app.pump_scan.params import PumpScanParams
from app.pump_scan.trend_context import (
    TrendContext,
    evaluate_trend_context,
    format_trend_alert_line,
    should_drop_by_downtrend_filter,
)
from app.pump_scan.timeframes import (
    DEFAULT_FAST_INTERVALS,
    DEFAULT_SLOW_INTERVALS,
    TfProfile,
    dump_allowed_on_interval,
    interval_label,
    interval_minutes,
    parse_interval_list,
    profile_for,
)
from app.pump_scan.universe import PoolCoin

if TYPE_CHECKING:
    from app.pump_scan.market_context import AlertMarketContext

log = logging.getLogger(__name__)

Direction = Literal["pump", "dump"]


@dataclass(frozen=True)
class ScanHit:
    symbol: str
    name: str
    direction: Direction
    interval: str
    price_change_pct: float
    rvol: float
    close: float
    extreme_risk: bool
    source: str
    move_kind: str = "spike"
    window_bars: int = 1
    is_innovation: bool = False
    is_st: bool = False
    outside_top200: bool = False
    social: SocialSnapshot | None = None
    forming_candle: bool = False
    scan_as_of_msk: str | None = None
    market: AlertMarketContext | None = None
    score_mult: float = 1.0
    trend: "TrendContext | None" = None
    oi: "OiContext | None" = None
    climax: "ClimaxContext | None" = None
    funding_roc: "FundingRocContext | None" = None
    funding_oi: "FundingOiTrajectoryContext | None" = None
    isolation: "IsolationContext | None" = None
    distance: "DistanceToEmaContext | None" = None

    def flags(self) -> list[str]:
        out: list[str] = [interval_label(self.interval)]
        if self.outside_top200:
            out.append("вне топ-200")
        if self.is_st:
            out.append("ST")
        if self.is_innovation:
            out.append("Innovation")
        if self.extreme_risk:
            out.append("высокий риск")
        if self.source in ("trending", "gainer"):
            out.append(self.source)
        if self.move_kind == "smooth":
            out.append("плавный")
        return out


# Обратная совместимость
PumpHit = ScanHit


# TrendContext — app.pump_scan.trend_context


@dataclass(frozen=True)
class OiContext:
    regime: str  # squeeze | new_money | mixed | unknown
    oi_chg_pct: float | None = None


@dataclass(frozen=True)
class ClimaxContext:
    signal: bool
    strong: bool


@dataclass(frozen=True)
class FundingRocContext:
    is_spike: bool
    funding_chg_pp: float | None = None
    lookback_periods: int = 0


@dataclass(frozen=True)
class IsolationContext:
    btc_chg_pct: float | None = None
    is_isolated_pump: bool | None = None


@dataclass(frozen=True)
class DistanceToEmaContext:
    nearest_ema: str | None = None  # "50" | "100" | "200"
    dist_atr: float | None = None
    atr_1d: float | None = None
    in_entry_zone: bool = False


def _oi_context_for_hit(
    client: BybitRest,
    params: PumpScanParams,
    hit: ScanHit,
    *,
    as_of_ms: int | None,
) -> OiContext | None:
    if not params.oi_analysis_enabled:
        return None
    if hit.direction != "pump":
        return None

    # pick OI interval aligned to hit TF when possible; otherwise fall back to 15m
    oi_iv = hit.interval
    if interval_minutes(oi_iv) > 60:
        oi_iv = "15"

    need = max(2, int(params.oi_window_bars) + 1)
    try:
        series = client.get_open_interest_series(
            hit.symbol, interval=oi_iv, limit=min(200, need + 5), end_ms=as_of_ms
        )
    except Exception:
        return OiContext(regime="unknown", oi_chg_pct=None)

    if len(series) < need:
        return OiContext(regime="unknown", oi_chg_pct=None)

    window = series[-need:]
    oi0 = window[0][1]
    oi1 = window[-1][1]
    if oi0 <= 0:
        return OiContext(regime="unknown", oi_chg_pct=None)
    oi_chg_pct = (oi1 - oi0) / oi0 * 100.0

    price_chg_pct = float(hit.price_change_pct)
    regime = "mixed"
    if price_chg_pct > 0 and oi_chg_pct <= params.oi_squeeze_max_chg_pct:
        regime = "squeeze"
    elif price_chg_pct > 0 and oi_chg_pct >= params.oi_new_money_min_chg_pct:
        regime = "new_money"
    return OiContext(regime=regime, oi_chg_pct=oi_chg_pct)


def _climax_context_for_hit(
    client: BybitRest,
    params: PumpScanParams,
    hit: ScanHit,
    *,
    as_of_ms: int | None,
) -> ClimaxContext | None:
    if not params.volume_climax_enabled:
        return None
    if hit.direction != "pump":
        return None

    profile = profile_for(hit.interval)
    if profile is None:
        return None
    bars, _forming = _ohlcv_bars_including_forming(
        client,
        hit.symbol,
        hit.interval,
        limit=max(profile.kline_limit, 10),
        as_of_ms=as_of_ms,
    )
    if len(bars) < 2:
        return None

    last = bars[-1]
    prev = bars[-2]
    _ts, o1, h1, l1, c1, v1 = last
    _ts0, o0, h0, l0, c0, v0 = prev
    if o1 <= 0 or o0 <= 0:
        return None
    if v0 <= 0:
        v0 = 1e-9
    vol_ratio = v1 / v0
    pct_last = abs((c1 - o1) / o1 * 100.0)
    pct_prev = abs((c0 - o0) / o0 * 100.0)
    is_volume_climax = (
        vol_ratio >= params.climax_volume_ratio
        and pct_prev > 1e-9
        and pct_last <= pct_prev * params.climax_price_decay_ratio
    )
    rng = max(h1 - l1, 0.0)
    upper = _upper_wick(o1, h1, c1)
    upper_ratio = (upper / rng) if rng > 1e-12 else 0.0
    is_rejection_wick = upper_ratio >= params.climax_wick_ratio_threshold

    signal = bool(is_volume_climax or is_rejection_wick)
    strong = bool(is_volume_climax and is_rejection_wick)
    return ClimaxContext(signal=signal, strong=strong)


def _funding_roc_for_hit(
    client: BybitRest,
    params: PumpScanParams,
    hit: ScanHit,
    *,
    as_of_ms: int | None,
) -> FundingRocContext | None:
    if not params.funding_roc_enabled:
        return None
    if hit.direction != "pump":
        return None

    n = max(1, int(params.funding_lookback_periods))
    try:
        from app.bybit.priority import bybit_api_slot
        from app.market.funding_math import funding_rate_annual_percent
    except Exception:
        return None

    # We use funding history to compute change in annualized funding (p.p.)
    try:
        with bybit_api_slot():
            r = client._http.get_funding_rate_history(
                category="linear",
                symbol=hit.symbol,
                endTime=as_of_ms,
                limit=n + 1,
            )
    except Exception:
        return None

    lst = (r or {}).get("result", {}).get("list") or []
    if len(lst) < n + 1:
        return None

    def _ts(row: dict) -> int:
        for k in ("fundingRateTimestamp", "fundingRateTime", "timestamp", "time"):
            v = row.get(k)
            if v is None or v == "":
                continue
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
        return 0

    lst = sorted(lst, key=_ts)
    old = lst[0]
    cur = lst[-1]
    old_rate = old.get("fundingRate")
    cur_rate = cur.get("fundingRate")
    if old_rate in (None, "") or cur_rate in (None, ""):
        return None

    # Bybit linear funding is typically every 8 hours; use 8h unless API provides otherwise.
    interval_h = 8.0
    try:
        old_annual = funding_rate_annual_percent(old_rate, interval_h)
        cur_annual = funding_rate_annual_percent(cur_rate, interval_h)
    except Exception:
        return None

    chg = float(cur_annual) - float(old_annual)
    is_spike = chg >= params.funding_spike_threshold_pct
    return FundingRocContext(
        is_spike=is_spike,
        funding_chg_pp=chg,
        lookback_periods=n,
    )


def _oi_history_limit(lookback_hours: int, interval: str) -> int:
    iv = interval.strip().lower()
    if iv in ("1h", "60min", "60"):
        step_h = 1.0
    elif iv in ("4h", "240min", "240"):
        step_h = 4.0
    elif iv in ("15min", "15"):
        step_h = 0.25
    elif iv in ("30min", "30"):
        step_h = 0.5
    elif iv in ("5min", "5"):
        step_h = 5.0 / 60.0
    elif iv in ("1d", "d", "day"):
        step_h = 24.0
    else:
        step_h = 1.0
    return min(200, max(2, math.ceil(int(lookback_hours) / step_h)))


def _funding_oi_trajectory_for_hit(
    client: BybitRest,
    params: PumpScanParams,
    hit: ScanHit,
    *,
    as_of_ms: int | None,
) -> "FundingOiTrajectoryContext | None":
    if not params.funding_trajectory_enabled:
        return None
    if hit.direction != "pump":
        return None

    from app.pump_scan.funding_oi_trajectory import (
        FundingOiTrajectoryContext,
        evaluate_funding_oi_trajectory,
    )

    funding_series: list[float] | None = None
    oi_series: list[float] | None = None
    interval_h: float | None = None

    try:
        interval_h = client.get_funding_interval_hours(hit.symbol)
        funding_series = client.get_funding_history_annualized(
            hit.symbol,
            interval_hours=interval_h,
            lookback_hours=params.funding_history_lookback_hours,
            end_ms=as_of_ms,
        )
    except Exception:
        log.debug("Funding history failed %s", hit.symbol, exc_info=True)

    try:
        oi_iv = (params.oi_history_interval or "1h").strip()
        oi_limit = _oi_history_limit(params.oi_history_lookback_hours, oi_iv)
        oi_raw = client.get_open_interest_series(
            hit.symbol,
            interval=oi_iv,
            limit=oi_limit,
            end_ms=as_of_ms,
        )
        oi_series = [v for _, v in sorted(oi_raw, key=lambda x: x[0])]
    except Exception:
        log.debug("OI history failed %s", hit.symbol, exc_info=True)

    return evaluate_funding_oi_trajectory(
        funding_series=funding_series,
        oi_series=oi_series,
        funding_interval_hours=interval_h,
        params=params,
    )


def _market_isolation_for_hit(
    client: BybitRest,
    params: PumpScanParams,
    hit: ScanHit,
    *,
    as_of_ms: int | None,
) -> IsolationContext | None:
    if not params.market_isolation_enabled:
        return None
    if hit.direction != "pump":
        return None

    window = hit.window_bars if hit.move_kind == "smooth" else 1
    try:
        bars, _forming = _ohlcv_bars_including_forming(
            client,
            "BTCUSDT",
            hit.interval,
            limit=max(10, window + 5),
            as_of_ms=as_of_ms,
        )
    except Exception:
        return None
    if len(bars) < window:
        return None
    chunk = bars[-window:]
    o0 = chunk[0][1]
    c1 = chunk[-1][4]
    if o0 <= 0:
        return None
    btc_chg = (c1 - o0) / o0 * 100.0
    is_isolated = abs(btc_chg) <= params.isolation_btc_chg_threshold
    return IsolationContext(btc_chg_pct=btc_chg, is_isolated_pump=is_isolated)


def _atr_1d(
    client: BybitRest,
    symbol: str,
    *,
    period: int,
    as_of_ms: int | None,
) -> float | None:
    p = max(2, int(period))
    bars, forming = _ohlcv_bars_including_forming(
        client,
        symbol,
        "D",
        limit=p + 5,
        as_of_ms=as_of_ms,
    )
    if not bars:
        return None
    closed = bars[:-1] if forming else bars
    if len(closed) < p + 1:
        return None
    # compute true range using prev close
    trs: list[float] = []
    for i in range(1, len(closed)):
        _ts, _o, h, l, c, _v = closed[i]
        prev_close = closed[i - 1][4]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(float(tr))
    if len(trs) < p:
        return None
    tail = trs[-p:]
    atr = sum(tail) / len(tail) if tail else None
    if atr is None or atr <= 0:
        return None
    return atr


def _distance_to_ema_for_hit(
    client: BybitRest,
    params: PumpScanParams,
    hit: ScanHit,
    *,
    as_of_ms: int | None,
) -> DistanceToEmaContext | None:
    if not params.distance_to_ema_enabled:
        return None
    try:
        from app.pump_scan.daily_ema import compute_daily_emas
    except Exception:
        return None

    emas = compute_daily_emas(client, hit.symbol, as_of_ms=as_of_ms)
    if emas is None:
        return None
    atr = _atr_1d(client, hit.symbol, period=params.atr_period_1d, as_of_ms=as_of_ms)
    if atr is None:
        return None

    px = float(hit.close)
    candidates: list[tuple[str, float]] = []
    if emas.ema50 is not None:
        candidates.append(("50", (px - emas.ema50) / atr))
    if emas.ema100 is not None:
        candidates.append(("100", (px - emas.ema100) / atr))
    if emas.ema200 is not None:
        candidates.append(("200", (px - emas.ema200) / atr))
    if not candidates:
        return None
    nearest, dist = min(candidates, key=lambda x: abs(x[1]))
    in_zone = abs(dist) <= float(params.distance_near_threshold_atr)
    return DistanceToEmaContext(
        nearest_ema=nearest,
        dist_atr=float(dist),
        atr_1d=float(atr),
        in_entry_zone=in_zone,
    )


def _body(open_px: float, close_px: float) -> float:
    return abs(close_px - open_px)


def _upper_wick(open_px: float, high: float, close_px: float) -> float:
    return high - max(open_px, close_px)


def _lower_wick(open_px: float, low: float, close_px: float) -> float:
    return min(open_px, close_px) - low


def _bar_rvol(volumes: list[float], idx: int, lookback: int) -> float | None:
    if idx < lookback:
        return None
    window = volumes[idx - lookback : idx]
    if not window:
        return None
    avg = sum(window) / len(window)
    if avg <= 1e-12:
        return None
    return volumes[idx] / avg


def _green_red_vol_ratio(bars: list[tuple], n: int = 3) -> float:
    tail = bars[-n:]
    green = 0.0
    red = 0.0
    for _, o, _, _, c, vol in tail:
        if c >= o:
            green += vol
        else:
            red += vol
    if red <= 1e-12:
        return green if green > 0 else 0.0
    return green / red


def _sell_pressure_ratio(bars: list[tuple], n: int = 3) -> float:
    """Объём красных / зелёных свечей (для дампа)."""
    tail = bars[-n:]
    green = 0.0
    red = 0.0
    for _, o, _, _, c, vol in tail:
        if c < o:
            red += vol
        else:
            green += vol
    if green <= 1e-12:
        return 10.0 if red > 0 else 0.0
    return red / green


def _red_green_vol_ratio(bars: list[tuple], n: int = 3) -> float:
    return _sell_pressure_ratio(bars, n)


def _prior_pump_filter_ok(
    bars: list[tuple], filter_bars: int, min_pct: float
) -> bool:
    """Для pump: не сигналить, если за N баров уже был сильный минус (≤ min_pct)."""
    if not bars:
        return True
    n = max(1, filter_bars)
    if len(bars) <= n:
        return True
    start_close = bars[-n - 1][4]
    end_close = bars[-1][4]
    if start_close <= 0:
        return True
    change = (end_close - start_close) / start_close * 100.0
    return change > min_pct


def _ohlcv_bars_including_forming(
    client: BybitRest,
    symbol: str,
    interval: str,
    limit: int,
    *,
    as_of_ms: int | None = None,
) -> tuple[list[tuple[int, float, float, float, float, float]], bool]:
    """Закрытые свечи + формирующаяся на момент as_of (или сейчас) в конце."""
    now_ms = as_of_ms if as_of_ms is not None else int(time.time() * 1000)
    raw = client.get_kline_ohlcv(
        symbol, interval, limit=limit, end_ms=as_of_ms
    )
    if not raw:
        return [], False
    raw.sort(key=lambda x: x[0])
    step = _interval_to_ms(interval)
    closed = [bar for bar in raw if bar[0] + step <= now_ms]
    in_progress = [bar for bar in raw if bar[0] <= now_ms < bar[0] + step]
    bars = list(closed)
    has_forming = False
    if in_progress:
        bars.append(in_progress[-1])
        has_forming = True
    return bars, has_forming


def _hit_from_coin(
    coin: PoolCoin,
    *,
    direction: Direction,
    interval: str,
    price_chg: float,
    rvol: float,
    close: float,
    move_kind: str,
    window_bars: int,
    social: SocialSnapshot | None = None,
    forming_candle: bool = False,
    scan_as_of_msk: str | None = None,
) -> ScanHit:
    return ScanHit(
        symbol=coin.symbol,
        name=coin.name,
        direction=direction,
        interval=interval,
        price_change_pct=price_chg,
        rvol=rvol,
        close=close,
        extreme_risk=coin.extreme_risk,
        source=coin.source,
        move_kind=move_kind,
        window_bars=window_bars,
        is_innovation=coin.is_innovation,
        is_st=coin.is_st,
        outside_top200=coin.outside_top200,
        social=social,
        forming_candle=forming_candle,
        scan_as_of_msk=scan_as_of_msk,
        score_mult=1.0,
        trend=None,
        oi=None,
        climax=None,
        funding_roc=None,
        funding_oi=None,
        isolation=None,
        distance=None,
    )


def _detect_spike(
    bars: list[tuple],
    coin: PoolCoin,
    params: PumpScanParams,
    profile: TfProfile,
    *,
    direction: Direction,
    forming_at_end: bool = False,
) -> ScanHit | None:
    lookback = profile.lookback
    need = lookback + params.rvol_sustain_bars + 2
    if len(bars) < need:
        return None

    volumes = [b[5] for b in bars]
    sustain = params.rvol_sustain_bars
    start_idx = len(bars) - sustain
    rvols: list[float] = []
    _, o, h, l, c, _ = bars[-1]
    if o <= 0:
        return None
    price_chg = (c - o) / o * 100.0

    rv_thresh = profile.rvol_threshold
    if direction == "dump" and abs(price_chg) >= profile.spike_pct * 1.5:
        rv_thresh = min(rv_thresh, 0.7)

    for i in range(start_idx, len(bars)):
        rv = _bar_rvol(volumes, i, lookback)
        if rv is None or rv < rv_thresh:
            return None
        rvols.append(rv)

    if direction == "pump":
        if price_chg < profile.spike_pct:
            return None
        body = _body(o, c)
        upper = _upper_wick(o, h, c)
        if body > 1e-12 and upper / body > params.max_upper_wick_body_ratio:
            return None
        if _green_red_vol_ratio(bars) < profile.min_green_red_ratio:
            return None
        if not _prior_pump_filter_ok(
            bars, params.dump_filter_bars, params.dump_filter_pct
        ):
            return None
    else:
        if price_chg > -profile.spike_pct:
            return None
        if c >= o:
            return None
        body = _body(o, c)
        lower = _lower_wick(o, l, c)
        if body > 1e-12 and lower / body > params.max_lower_wick_body_ratio:
            return None
        if abs(price_chg) < profile.spike_pct * 1.5:
            if _sell_pressure_ratio(bars, n=3) < profile.min_green_red_ratio:
                return None

    return _hit_from_coin(
        coin,
        direction=direction,
        interval=profile.interval,
        price_chg=price_chg,
        rvol=max(rvols),
        close=c,
        move_kind="spike",
        window_bars=1,
        forming_candle=forming_at_end,
    )


def _detect_smooth(
    bars: list[tuple],
    coin: PoolCoin,
    params: PumpScanParams,
    profile: TfProfile,
    *,
    direction: Direction,
    forming_at_end: bool = False,
) -> ScanHit | None:
    window = profile.smooth_bars
    lookback = profile.lookback
    need = lookback + window + 2
    if len(bars) < need:
        return None

    chunk = bars[-window:]
    o0 = chunk[0][1]
    c1 = chunk[-1][4]
    if o0 <= 0:
        return None
    price_chg = (c1 - o0) / o0 * 100.0

    if direction == "pump":
        if not params.smooth_pump_enabled or price_chg < profile.smooth_pct:
            return None
        vol_ratio_min = profile.smooth_min_green_ratio
        vol_ok = _green_red_vol_ratio(chunk, n=min(window, 6)) >= vol_ratio_min
    else:
        if not params.smooth_dump_enabled or price_chg > -profile.smooth_pct:
            return None
        vol_ratio_min = profile.smooth_min_green_ratio
        vol_ok = _sell_pressure_ratio(chunk, n=min(window, 6)) >= vol_ratio_min

    if not vol_ok:
        return None

    vols = [b[5] for b in bars]
    avg_chunk = sum(b[5] for b in chunk) / window
    prior = vols[-window - lookback : -window]
    if not prior:
        return None
    avg_prior = sum(prior) / len(prior)
    if avg_prior <= 1e-12:
        return None
    rvol = avg_chunk / avg_prior
    if rvol < profile.smooth_rvol:
        return None

    if direction == "pump" and not _prior_pump_filter_ok(
        bars, params.dump_filter_bars, params.dump_filter_pct
    ):
        return None

    return _hit_from_coin(
        coin,
        direction=direction,
        interval=profile.interval,
        price_chg=price_chg,
        rvol=rvol,
        close=c1,
        move_kind="smooth",
        window_bars=window,
        forming_candle=forming_at_end,
    )


def _best_spike_or_smooth(spike: ScanHit | None, smooth: ScanHit | None) -> ScanHit | None:
    candidates = [h for h in (spike, smooth) if h is not None]
    if not candidates:
        return None
    return max(candidates, key=_hit_quality_score)


def _hit_quality_score(hit: ScanHit) -> float:
    """
    Лучший TF: сила сигнала относительно порога TF × RVOL × вес старшего TF.
    """
    profile = profile_for(hit.interval)
    if profile is None:
        return abs(hit.price_change_pct) * (max(hit.rvol, 0.1) ** 0.5)
    thresh = profile.smooth_pct if hit.move_kind == "smooth" else profile.spike_pct
    excess = abs(hit.price_change_pct) / max(thresh, 1.0)
    rvol_factor = min(max(hit.rvol, 0.1), 10.0) ** 0.5
    tf_factor = math.log10(max(interval_minutes(hit.interval), 5))
    base = excess * rvol_factor * tf_factor
    return base * max(hit.score_mult, 0.0)


def _best_hits_per_direction(hits: list[ScanHit]) -> list[ScanHit]:
    """Один алерт на монету и направление — TF с лучшим score."""
    if not hits:
        return []
    by_dir: dict[Direction, list[ScanHit]] = {}
    for h in hits:
        by_dir.setdefault(h.direction, []).append(h)
    return [max(group, key=_hit_quality_score) for group in by_dir.values()]


def _attach_social(hit: ScanHit, social: SocialSnapshot | None, min_ratio: float) -> ScanHit:
    if social is None:
        return hit
    if social.spike_ratio is not None and social.spike_ratio < min_ratio:
        social = SocialSnapshot(
            topic=social.topic,
            galaxy_score=social.galaxy_score,
            sentiment=social.sentiment,
            interactions=social.interactions,
            social_dominance=social.social_dominance,
            contributors=social.contributors,
            spike_ratio=None,
        )
    return replace(hit, social=social)


def _detect_on_interval(
    client: BybitRest,
    coin: PoolCoin,
    params: PumpScanParams,
    interval: str,
    *,
    directions: tuple[Direction, ...],
    as_of_ms: int | None = None,
    as_of_label: str | None = None,
) -> list[ScanHit]:
    profile = profile_for(interval)
    if profile is None:
        return []

    bars, forming_at_end = _ohlcv_bars_including_forming(
        client,
        coin.symbol,
        interval,
        limit=profile.kline_limit,
        as_of_ms=as_of_ms,
    )
    if not bars:
        return []

    hits: list[ScanHit] = []
    for direction in directions:
        spike = _detect_spike(
            bars, coin, params, profile, direction=direction, forming_at_end=forming_at_end
        )
        smooth = _detect_smooth(
            bars, coin, params, profile, direction=direction, forming_at_end=forming_at_end
        )
        hit = _best_spike_or_smooth(spike, smooth)
        if hit is not None:
            if as_of_label:
                hit = replace(hit, scan_as_of_msk=as_of_label)
            hits.append(hit)
    return hits


def detect_symbol_hits(
    client: BybitRest,
    coin: PoolCoin,
    params: PumpScanParams,
    intervals: list[str],
    *,
    as_of_ms: int | None = None,
    as_of_label: str | None = None,
) -> list[ScanHit]:
    """До одного алерта на монету × направление (лучший TF по score)."""
    raw: list[ScanHit] = []
    for interval in intervals:
        directions: list[Direction] = ["pump"]
        if params.dump_detection_enabled and dump_allowed_on_interval(interval):
            directions.append("dump")
        raw.extend(
            _detect_on_interval(
                client,
                coin,
                params,
                interval,
                directions=tuple(directions),
                as_of_ms=as_of_ms,
                as_of_label=as_of_label,
            )
        )

    raw = _enrich_hits_context(client, params, raw, as_of_ms=as_of_ms)
    hits = _best_hits_per_direction(raw)
    if not hits or not params.lunarcrush_in_alerts or as_of_ms is not None:
        return hits

    try:
        social = fetch_symbol_social(coin.symbol)
        min_ratio = params.lunarcrush_spike_ratio
        return [_attach_social(h, social, min_ratio) for h in hits]
    except Exception:
        log.debug("LunarCrush skip %s", coin.symbol, exc_info=True)
    return hits


def detect_symbol(
    client: BybitRest,
    coin: PoolCoin,
    params: PumpScanParams,
    intervals: list[str],
) -> ScanHit | None:
    hits = detect_symbol_hits(client, coin, params, intervals)
    if not hits:
        return None
    return max(hits, key=_hit_quality_score)


def detect_pump(
    client: BybitRest,
    coin: PoolCoin,
    params: PumpScanParams,
) -> ScanHit | None:
    """Совместимость: быстрые TF + медленные если заданы в fast list."""
    fast = parse_interval_list(params.scan_intervals_fast, fallback=DEFAULT_FAST_INTERVALS)
    return detect_symbol(client, coin, params, fast)


def _trend_context_for_hit(
    client: BybitRest, params: PumpScanParams, hit: ScanHit, *, as_of_ms: int | None
) -> TrendContext | None:
    if not params.trend_context_enabled:
        return None
    try:
        from app.pump_scan.daily_ema import compute_daily_emas
    except Exception:
        return None

    need = max(
        params.min_bars_for_ema200 + 5,
        params.trend_context_lookback_days + 5,
        60,
    )
    bars, forming = _ohlcv_bars_including_forming(
        client,
        hit.symbol,
        "D",
        limit=min(250, need),
        as_of_ms=as_of_ms,
    )
    if not bars:
        return None

    prev = bars[-2] if forming and len(bars) >= 2 else bars[-1]
    _ts, _o, _h, _l, close_prev, _vol = prev
    if close_prev <= 0:
        return None

    closed = bars[:-1] if forming else bars
    if not closed:
        return None
    history_days = len(closed)
    daily_highs = [b[2] for b in closed]

    emas = compute_daily_emas(client, hit.symbol, as_of_ms=as_of_ms)
    ema50 = emas.ema50 if emas else None
    ema100 = emas.ema100 if emas else None
    ema200 = emas.ema200 if emas else None

    return evaluate_trend_context(
        history_days=history_days,
        close_prev=close_prev,
        ema50=ema50,
        ema100=ema100,
        ema200=ema200,
        daily_highs=daily_highs,
        params=params,
    )


def _enrich_hits_context(
    client: BybitRest,
    params: PumpScanParams,
    hits: list[ScanHit],
    *,
    as_of_ms: int | None,
) -> list[ScanHit]:
    if not hits:
        return []

    out: list[ScanHit] = []
    for h in hits:
        trend = _trend_context_for_hit(client, params, h, as_of_ms=as_of_ms)
        if trend is not None:
            h = replace(h, trend=trend)
            mode = (params.downtrend_mode or "filter").strip().lower()
            if should_drop_by_downtrend_filter(trend, mode):
                log.debug(
                    "Downtrend filter drop %s %s status=%s",
                    h.symbol,
                    h.interval,
                    trend.data_status,
                )
                continue
            if mode == "boost" and trend.is_downtrend_context:
                h = replace(
                    h,
                    score_mult=h.score_mult * max(0.1, params.downtrend_score_multiplier),
                )
        # 3) Open interest analysis (instant snapshot; score via trajectory when enabled)
        oi = _oi_context_for_hit(client, params, h, as_of_ms=as_of_ms)
        if oi is not None:
            h = replace(h, oi=oi)
            if not params.funding_trajectory_enabled:
                if oi.regime == "squeeze":
                    h = replace(h, score_mult=h.score_mult * max(0.1, params.oi_squeeze_score_bonus))
                elif oi.regime == "new_money":
                    if params.oi_new_money_hard_block:
                        log.debug(
                            "OI hard-block drop %s %s oi=%.1f%%",
                            h.symbol,
                            h.interval,
                            oi.oi_chg_pct or 0.0,
                        )
                        continue
                    h = replace(
                        h,
                        score_mult=h.score_mult * max(0.0, min(params.oi_new_money_score_penalty, 1.0)),
                    )
            elif oi.regime == "new_money" and params.oi_new_money_hard_block:
                log.debug(
                    "OI hard-block drop %s %s oi=%.1f%%",
                    h.symbol,
                    h.interval,
                    oi.oi_chg_pct or 0.0,
                )
                continue
        # 4) Volume climax / divergence
        climax = _climax_context_for_hit(client, params, h, as_of_ms=as_of_ms)
        if climax is not None:
            h = replace(h, climax=climax)
            if climax.strong:
                h = replace(h, score_mult=h.score_mult * max(0.1, params.climax_score_bonus))
            elif climax.signal:
                h = replace(h, score_mult=h.score_mult * max(0.1, params.climax_score_bonus_weak))
        # 5) Funding rate of change (ROC) — alert only, no score
        fr = _funding_roc_for_hit(client, params, h, as_of_ms=as_of_ms)
        if fr is not None:
            h = replace(h, funding_roc=fr)
        # 5b) Funding + OI Trajectory (composite score)
        fot = _funding_oi_trajectory_for_hit(client, params, h, as_of_ms=as_of_ms)
        if fot is not None:
            h = replace(h, funding_oi=fot)
            if fot.score_multiplier != 1.0:
                h = replace(
                    h,
                    score_mult=h.score_mult * max(0.0, fot.score_multiplier),
                )
        # 6) Market isolation vs BTC
        iso = _market_isolation_for_hit(client, params, h, as_of_ms=as_of_ms)
        if iso is not None:
            h = replace(h, isolation=iso)
            if iso.is_isolated_pump:
                h = replace(
                    h,
                    score_mult=h.score_mult * max(0.1, params.isolation_score_bonus),
                )
        # 7) Distance-to-EMA / ATR metric (context only)
        dist = _distance_to_ema_for_hit(client, params, h, as_of_ms=as_of_ms)
        if dist is not None:
            h = replace(h, distance=dist)
        out.append(h)
    return out


def fast_intervals(params: PumpScanParams) -> list[str]:
    return parse_interval_list(params.scan_intervals_fast, fallback=DEFAULT_FAST_INTERVALS)


def slow_intervals(params: PumpScanParams) -> list[str]:
    return parse_interval_list(params.scan_intervals_slow, fallback=DEFAULT_SLOW_INTERVALS)


def symbol_usdt_pair(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith("USDT"):
        return s
    if s.endswith("PERP"):
        return s
    return f"{s}USDT"


def symbol_base(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith("USDT"):
        s = s[:-4]
    elif s.endswith("PERP"):
        s = s[:-4]
    m = re.match(r"^(\d+)(.+)$", s)
    if m:
        return m.group(2)
    return s


def alert_tf_label(interval: str) -> str:
    lbl = interval_label(interval)
    if lbl.endswith("m"):
        return f"{lbl[:-1]}M"
    if lbl.endswith("h"):
        return f"{lbl[:-1]}H"
    if lbl.endswith("w"):
        return f"{lbl[:-1]}W"
    return lbl


def _move_window_label(hit: ScanHit) -> str:
    if hit.move_kind == "smooth":
        return str(hit.window_bars)
    lbl = interval_label(hit.interval)
    if lbl.endswith("m") or lbl.endswith("h"):
        return lbl[:-1]
    if lbl == "1D":
        return "1"
    if lbl == "1W":
        return "1"
    return "1"


def _risk_tag_lines(hit: ScanHit) -> list[str]:
    lines: list[str] = []
    if hit.is_innovation:
        lines.append("⚠️ – Innovation")
    if hit.is_st:
        lines.append("⚠️ – ST")
    if hit.outside_top200:
        lines.append("⚠️ – вне топ-200")
    return lines


def format_scan_alert(hit: ScanHit, params: PumpScanParams | None = None) -> str:
    from app.pump_scan.market_context import format_market_context_lines

    scan_params = params or PumpScanParams()
    icon = "🔻" if hit.direction == "dump" else "🔥"
    pair = symbol_usdt_pair(hit.symbol)
    warn = "⚠️" if hit.is_innovation or hit.is_st or hit.outside_top200 else ""
    tf = alert_tf_label(hit.interval)
    pct = hit.price_change_pct
    sign = "+" if pct > 0 else ""
    window = _move_window_label(hit)

    lines = [
        f"{icon} <code>{pair}</code>{warn} · {tf}",
        f"Ход: <b>{sign}{pct:.1f}%</b> за {window} · Объём: <b>×{hit.rvol:.1f}</b>",
    ]
    if hit.trend:
        trend_line = format_trend_alert_line(hit.trend, scan_params)
        if trend_line:
            lines.append(trend_line)
    lines.extend(format_market_context_lines(hit.market))
    lines.extend(_risk_tag_lines(hit))
    if hit.oi and hit.oi.regime != "unknown":
        if hit.oi.regime == "squeeze":
            pct = hit.oi.oi_chg_pct
            lines.append(f"🟢 OI: сквиз шортов (OI {pct:.1f}%)" if pct is not None else "🟢 OI: сквиз шортов")
        elif hit.oi.regime == "new_money":
            pct = hit.oi.oi_chg_pct
            lines.append(
                f"🔴 OI: заходят новые лонги (OI +{pct:.1f}%) — риск продолжения"
                if pct is not None
                else "🔴 OI: заходят новые лонги — риск продолжения"
            )
        elif hit.oi.regime == "mixed":
            pct = hit.oi.oi_chg_pct
            lines.append(f"⚪ OI: смешанно (OI {pct:+.1f}%)" if pct is not None else "⚪ OI: смешанно")
    if hit.funding_oi and hit.funding_oi.alert_line:
        lines.append(hit.funding_oi.alert_line)
    if hit.climax and hit.climax.signal:
        lines.append(
            "⚡ Признаки истощения покупателей (объём+тень)"
            if hit.climax.strong
            else "⚡ Возможное истощение импульса"
        )
    if hit.funding_roc and hit.funding_roc.is_spike:
        chg = hit.funding_roc.funding_chg_pp
        n = hit.funding_roc.lookback_periods
        if chg is not None and n > 0:
            lines.append(f"📈 Funding резко растёт: +{chg:.0f}п.п. за {n} период(а)")
    if hit.isolation and hit.isolation.btc_chg_pct is not None:
        btc = hit.isolation.btc_chg_pct
        if hit.isolation.is_isolated_pump:
            lines.append(f"🎯 Изолированный памп (BTC {btc:+.1f}% за окно)")
        else:
            lines.append(f"⚠️ Памп на фоне движения рынка (BTC {btc:+.1f}%)")
    if hit.distance and hit.distance.nearest_ema and hit.distance.dist_atr is not None:
        near = " (в зоне входа)" if hit.distance.in_entry_zone else ""
        lines.append(f"📍 До EMA{hit.distance.nearest_ema}: {hit.distance.dist_atr:+.1f} ATR{near}")
    if hit.social:
        social_line = hit.social.alert_line()
        if social_line:
            lines.append(social_line)
    if hit.scan_as_of_msk:
        lines.append(f"<i>📅 Исторический срез: {hit.scan_as_of_msk}</i>")
    if hit.forming_candle:
        lines.append("<i>⏳ Свеча ещё не закрыта — значения могут измениться</i>")
    return "\n".join(lines)


format_pump_alert = format_scan_alert


def pump_alert_keyboard(symbol: str, *, offer_entry_watch: bool = True) -> InlineKeyboardMarkup:
    sym = symbol.upper()
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="📉 Открыть позицию",
                callback_data=f"pump:pos:open:{sym}",
            ),
            InlineKeyboardButton(
                text="⚡ По маркету",
                callback_data=f"pump:pos:mkt:{sym}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🔔 EMA будильник",
                callback_data=f"pump:alarm:start:{sym}",
            ),
        ],
    ]
    if offer_entry_watch:
        rows.append(
            [
                InlineKeyboardButton(
                    text="👀 Следить до входа",
                    callback_data=f"pump:watch:add:{sym}",
                ),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
