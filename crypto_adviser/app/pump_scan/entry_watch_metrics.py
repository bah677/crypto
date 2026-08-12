"""Снимок метрик каталога для entry-watch (без LLM)."""

from __future__ import annotations

import logging
from typing import Any

from app.bybit.rest import BybitRest
from app.pump_scan.params import PumpScanParams

log = logging.getLogger(__name__)


def compute_entry_watch_metrics(
    client: BybitRest,
    symbol: str,
    *,
    impulse_price: float,
    params: PumpScanParams | None = None,
) -> dict[str, Any]:
    """
    Возвращает dict метрик каталога.
    Отсутствующие значения = None (условие all_of не выполняется).
    """
    from app.pump_scan.daily_ema import compute_daily_emas
    from app.pump_scan.detect import _atr_1d
    from app.pump_scan.entry_watch_plan import classify_squeeze_phase
    from app.pump_scan.funding_oi_trajectory import evaluate_funding_oi_trajectory

    p = params or PumpScanParams()
    sym = symbol.upper()
    out: dict[str, Any] = {
        "funding_trajectory_state": None,
        "funding_now": None,
        "funding_min": None,
        "oi_trend": None,
        "oi_chg_window_pct": None,
        "squeeze_phase": None,
        "dist_atr_nearest": None,
        "dist_atr_ema200": None,
        "price_vs_impulse_high_pct": None,
        "price_vs_high_watermark_pct": None,
        "climax_signal": None,
        "price": None,
    }

    try:
        price = client.last_price(sym)
    except Exception:
        price = None
        log.debug("entry_watch price failed %s", sym, exc_info=True)
    out["price"] = price
    if price is not None and impulse_price > 0:
        out["price_vs_impulse_high_pct"] = (price - impulse_price) / impulse_price * 100.0

    funding_series: list[float] | None = None
    oi_series: list[float] | None = None
    interval_h: float | None = None
    try:
        interval_h = client.get_funding_interval_hours(sym)
        funding_series = client.get_funding_history_annualized(
            sym,
            interval_hours=interval_h,
            lookback_hours=p.funding_history_lookback_hours,
        )
    except Exception:
        log.debug("entry_watch funding failed %s", sym, exc_info=True)

    try:
        oi_iv = (p.oi_history_interval or "1h").strip()
        oi_limit = min(200, max(2, int(p.oi_history_lookback_hours)))
        oi_raw = client.get_open_interest_series(sym, interval=oi_iv, limit=oi_limit)
        oi_series = [v for _, v in sorted(oi_raw, key=lambda x: x[0])]
    except Exception:
        log.debug("entry_watch OI failed %s", sym, exc_info=True)

    ctx = evaluate_funding_oi_trajectory(
        funding_series=funding_series,
        oi_series=oi_series,
        funding_interval_hours=interval_h,
        params=p,
    )
    if ctx is not None:
        out["funding_trajectory_state"] = ctx.funding_trajectory_state
        out["funding_now"] = ctx.funding_now
        out["funding_min"] = ctx.funding_min
        out["oi_trend"] = ctx.oi_trend
        out["oi_chg_window_pct"] = ctx.oi_chg_over_window_pct

    try:
        emas = compute_daily_emas(client, sym)
        atr = _atr_1d(client, sym, period=p.atr_period_1d, as_of_ms=None)
        if emas is not None and price is not None and atr and atr > 0:
            candidates: list[float] = []
            for val in (emas.ema50, emas.ema100, emas.ema200):
                if val is not None:
                    candidates.append(abs(price - val) / atr)
            if candidates:
                out["dist_atr_nearest"] = min(candidates)
            if emas.ema200 is not None:
                out["dist_atr_ema200"] = (price - emas.ema200) / atr
    except Exception:
        log.debug("entry_watch dist_atr failed %s", sym, exc_info=True)

    out["squeeze_phase"] = classify_squeeze_phase(out)
    return out
