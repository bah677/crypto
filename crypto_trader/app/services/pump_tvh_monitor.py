"""Мониторинг вотчлиста ТВХ после pump/dump импульсов."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.bybit.priority import background_request_scope, end_background_tick, try_begin_background_tick
from app.bybit.rest import BybitRest, _interval_to_ms
from app.config import get_settings
from app.db.session import session_scope
from app.pump_scan.detect import ScanHit
from app.pump_scan.params import PumpScanParams
from app.pump_scan.tvh import (
    ImpulseContext,
    TvhParams,
    evaluate_tvh,
    filter_pump_short_fade,
    junior_interval,
)
from app.repository.pump_scan import get_pump_config
from app.repository.pump_tvh_watch import (
    disable_pump_tvh_watch,
    fetch_active_pump_tvh_watches,
    mark_tvh_alerted,
    purge_expired_pump_tvh_watches,
    update_pump_tvh_watch_bounds,
    upsert_pump_tvh_watch,
)

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


def tvh_params_from_scan(scan: PumpScanParams) -> TvhParams:
    return TvhParams(
        min_score=scan.tvh_min_score,
        ema_fast=scan.tvh_ema_fast,
        ema_slow=scan.tvh_ema_slow,
        min_retrace_fade=scan.tvh_min_retrace_fade,
        pullback_min=scan.tvh_pullback_min,
        pullback_max=scan.tvh_pullback_max,
        swing_lookback=scan.tvh_swing_lookback,
    )


def scan_hit_to_dict(hit: ScanHit) -> dict:
    d = asdict(hit)
    d.pop("social", None)
    d.pop("market", None)
    return d


def scan_hit_from_dict(raw: dict, *, symbol: str) -> ScanHit:
    return ScanHit(
        symbol=raw.get("symbol", symbol),
        name=raw.get("name", symbol),
        direction=raw.get("direction", "pump"),
        interval=raw.get("interval", "5"),
        price_change_pct=float(raw.get("price_change_pct", 0)),
        rvol=float(raw.get("rvol", 0)),
        close=float(raw.get("close", 0)),
        extreme_risk=bool(raw.get("extreme_risk", False)),
        source=raw.get("source", "pool"),
        move_kind=raw.get("move_kind", "spike"),
        window_bars=int(raw.get("window_bars", 1)),
        is_innovation=bool(raw.get("is_innovation", False)),
        is_st=bool(raw.get("is_st", False)),
        outside_top200=bool(raw.get("outside_top200", False)),
        social=None,
        forming_candle=False,
        scan_as_of_msk=raw.get("scan_as_of_msk"),
        market=None,
    )


def _price_eps(price: float) -> float:
    return max(abs(price) * 1e-6, 1e-8)


def _extremes_since(
    client: BybitRest,
    symbol: str,
    interval: str,
    start_ms: int,
    *,
    as_of_ms: int | None = None,
) -> tuple[float, float] | None:
    bars = client.get_kline_ohlcv(symbol, interval, limit=200, end_ms=as_of_ms)
    window = [b for b in bars if b[0] >= start_ms]
    if not window:
        return None
    return min(b[3] for b in window), max(b[2] for b in window)


def expand_impulse_bounds_for_watch(
    client: BybitRest,
    row,
    *,
    as_of_ms: int | None = None,
) -> tuple[float, float, bool]:
    """
    Расширить L/H импульса по новым экстремумам с момента импульсной свечи.
    Смотрим source TF и entry TF, чтобы не пропустить хай/лоу между старшими свечами.
    """
    start_ms = int(row.impulse_bar_open_ms or 0)
    if start_ms <= 0:
        return row.impulse_low, row.impulse_high, False

    lows: list[float] = []
    highs: list[float] = []
    for interval in {row.source_interval, row.entry_interval}:
        ext = _extremes_since(
            client, row.symbol, interval, start_ms, as_of_ms=as_of_ms
        )
        if ext is not None:
            lows.append(ext[0])
            highs.append(ext[1])

    if not lows:
        return row.impulse_low, row.impulse_high, False

    new_low = min(row.impulse_low, min(lows))
    new_high = max(row.impulse_high, max(highs))
    eps = _price_eps((new_low + new_high) / 2)
    changed = new_low < row.impulse_low - eps or new_high > row.impulse_high + eps
    return new_low, new_high, changed


def compute_impulse_bounds(
    client: BybitRest,
    hit: ScanHit,
    *,
    as_of_ms: int | None = None,
) -> tuple[float, float, int]:
    from app.pump_scan.detect import _ohlcv_bars_including_forming
    from app.pump_scan.timeframes import profile_for

    profile = profile_for(hit.interval)
    limit = profile.kline_limit if profile else 80
    bars, forming = _ohlcv_bars_including_forming(
        client, hit.symbol, hit.interval, limit, as_of_ms=as_of_ms
    )
    if not bars:
        raise ValueError(f"no bars for {hit.symbol}")

    if hit.move_kind == "smooth" and hit.window_bars > 1:
        w = hit.window_bars
        if forming and len(bars) > w:
            chunk = bars[-w - 1 : -1] or bars[-w:]
        else:
            chunk = bars[-w:]
        lows = [b[3] for b in chunk]
        highs = [b[2] for b in chunk]
        return min(lows), max(highs), chunk[-1][0]

    bar = bars[-2] if forming and len(bars) >= 2 else bars[-1]
    return bar[3], bar[2], bar[0]


def _impulse_context_from_watch(row) -> ImpulseContext:
    raw = row.hit_dict()
    return ImpulseContext(
        symbol=row.symbol,
        direction=row.impulse_direction,  # type: ignore[arg-type]
        source_interval=row.source_interval,
        entry_interval=row.entry_interval,
        impulse_low=row.impulse_low,
        impulse_high=row.impulse_high,
        impulse_pct=float(raw.get("price_change_pct", 0)),
        impulse_rvol=float(raw.get("rvol", 0)),
        move_kind=raw.get("move_kind", "spike"),
        impulse_bar_open_ms=int(row.impulse_bar_open_ms or 0),
    )


def _bars_with_volume(
    client: BybitRest,
    symbol: str,
    interval: str,
    *,
    limit: int = 120,
    end_ms: int | None = None,
) -> list[Bar]:
    raw = client.get_kline_ohlcv(symbol, interval, limit=limit, end_ms=end_ms)
    if not raw:
        return []
    step = _interval_to_ms(interval)
    now_ms = end_ms if end_ms is not None else int(__import__("time").time() * 1000)
    closed = [bar for bar in raw if bar[0] + step <= now_ms]
    return closed


def _evaluate_watch_sync(row, params: TvhParams, *, as_of_ms: int | None = None) -> list:
    client = BybitRest(category="linear")
    ctx = _impulse_context_from_watch(row)
    bars = _bars_with_volume(
        client, row.symbol, row.entry_interval, limit=120, end_ms=as_of_ms
    )
    if not bars:
        return []
    return evaluate_tvh(
        bars,
        ctx,
        params,
    )


def _apply_expanded_bounds(row, low: float, high: float) -> None:
    row.impulse_low = low
    row.impulse_high = high


def compute_watchlist_scores_sync(
    watches: list,
    tvh_p: TvhParams,
) -> dict[int, tuple[int | None, int | None]]:
    from app.pump_scan.tvh import preview_watch_scores

    out: dict[int, tuple[int | None, int | None]] = {}
    if not watches:
        return out
    with background_request_scope():
        client = BybitRest(category="linear")
        for row in watches:
            try:
                low, high, _ = expand_impulse_bounds_for_watch(client, row)
                _apply_expanded_bounds(row, low, high)
                ctx = _impulse_context_from_watch(row)
                bars = _bars_with_volume(client, row.symbol, row.entry_interval, limit=120)
                out[row.id] = preview_watch_scores(bars, ctx, tvh_p)
            except Exception:
                log.exception("TVH scores: %s", row.symbol)
                out[row.id] = (None, None)
    return out


def scenario_flags(scenario: str) -> tuple[bool, bool]:
    if scenario in ("short_fade", "short_continue"):
        return True, False
    return False, True


async def enqueue_tvh_watch(
    hits: list[ScanHit],
    params: PumpScanParams,
    *,
    as_of_ms: int | None = None,
) -> int:
    if not hits:
        return 0
    ttl = max(15, params.tvh_watch_ttl_min)
    expires_at = datetime.now(MSK) + timedelta(minutes=ttl)
    added = 0

    async with session_scope() as session:
        for hit in hits:
            if hit.direction != "pump":
                continue
            try:
                bounds = await asyncio.to_thread(
                    compute_impulse_bounds, BybitRest(category="linear"), hit, as_of_ms=as_of_ms
                )
            except Exception:
                log.exception("TVH watch: не вычислили границы импульса %s", hit.symbol)
                continue
            low, high, open_ms = bounds
            if high <= low:
                continue
            entry_iv = junior_interval(hit.interval)
            await upsert_pump_tvh_watch(
                session,
                symbol=hit.symbol,
                impulse_direction=hit.direction,
                source_interval=hit.interval,
                entry_interval=entry_iv,
                hit_data=scan_hit_to_dict(hit),
                impulse_low=low,
                impulse_high=high,
                impulse_bar_open_ms=open_ms,
                expires_at=expires_at,
            )
            added += 1
            log.info(
                "TVH watch: %s %s %s → entry TF %s",
                hit.direction,
                hit.symbol,
                hit.interval,
                entry_iv,
            )
    return added


async def evaluate_tvh_for_hits(
    hits: list[ScanHit],
    params: PumpScanParams,
    *,
    as_of_ms: int | None = None,
) -> list[tuple[ScanHit, list]]:
    """Мгновенная оценка ТВХ (ручной исторический скан)."""
    from app.pump_scan.tvh import TvhCandidate

    out: list[tuple[ScanHit, list[TvhCandidate]]] = []
    tvh_p = tvh_params_from_scan(params)
    client = BybitRest(category="linear")

    for hit in hits:
        if hit.direction != "pump":
            continue
        try:
            low, high, open_ms = await asyncio.to_thread(
                compute_impulse_bounds, client, hit, as_of_ms=as_of_ms
            )
        except Exception:
            continue
        entry_iv = junior_interval(hit.interval)
        ctx = ImpulseContext(
            symbol=hit.symbol,
            direction=hit.direction,  # type: ignore[arg-type]
            source_interval=hit.interval,
            entry_interval=entry_iv,
            impulse_low=low,
            impulse_high=high,
            impulse_pct=hit.price_change_pct,
            impulse_rvol=hit.rvol,
            move_kind=hit.move_kind,
            impulse_bar_open_ms=open_ms,
        )
        bars = await asyncio.to_thread(
            _bars_with_volume,
            client,
            hit.symbol,
            entry_iv,
            limit=120,
            end_ms=as_of_ms,
        )
        if not bars:
            continue
        candidates = filter_pump_short_fade(evaluate_tvh(bars, ctx, tvh_p))
        if candidates:
            out.append((hit, candidates))
    return out


async def run_pump_tvh_tick() -> int:
    if not get_settings().pump_scan_enabled:
        return 0

    async with session_scope() as session:
        row = await get_pump_config(session)
        if not row.enabled:
            return 0
        scan_params = row.params()
        tvh_p = tvh_params_from_scan(scan_params)
        watches = await fetch_active_pump_tvh_watches(session)

    if not watches:
        await _purge_expired()
        return 0

    if not await asyncio.to_thread(try_begin_background_tick, "pump_tvh"):
        return 0

    ttl_min = max(15, scan_params.tvh_watch_ttl_min)
    sent = 0
    try:
        with background_request_scope():
            client = BybitRest(category="linear")
            for watch in watches:
                if watch.impulse_direction != "pump":
                    continue
                try:
                    low, high, expanded = await asyncio.to_thread(
                        expand_impulse_bounds_for_watch, client, watch
                    )
                    if expanded:
                        new_expires = datetime.now(MSK) + timedelta(minutes=ttl_min)
                        async with session_scope() as session:
                            await update_pump_tvh_watch_bounds(
                                session,
                                watch.id,
                                impulse_low=low,
                                impulse_high=high,
                                expires_at=new_expires,
                            )
                        _apply_expanded_bounds(watch, low, high)
                        watch.expires_at = new_expires
                        log.info(
                            "TVH watch expand: %s %s L=%.6f H=%.6f TTL→%s",
                            watch.impulse_direction,
                            watch.symbol,
                            low,
                            high,
                            new_expires.strftime("%H:%M"),
                        )
                    candidates = await asyncio.to_thread(_evaluate_watch_sync, watch, tvh_p)
                    candidates = filter_pump_short_fade(candidates)
                except Exception:
                    log.exception("TVH tick: сбой %s", watch.symbol)
                    continue
                if not candidates:
                    continue

                hit = scan_hit_from_dict(watch.hit_dict(), symbol=watch.symbol)
                to_send = []
                for cand in candidates:
                    is_short, is_long = scenario_flags(cand.scenario)
                    if is_short and watch.alerted_short:
                        continue
                    if is_long and watch.alerted_long:
                        continue
                    to_send.append(cand)

                if not to_send:
                    continue

                from app.services.pump_scan import send_tvh_alerts

                await send_tvh_alerts(hit, to_send)
                async with session_scope() as session:
                    for cand in to_send:
                        is_short, is_long = scenario_flags(cand.scenario)
                        await mark_tvh_alerted(
                            session,
                            watch.id,
                            short=is_short,
                            long=is_long,
                        )
                    if scan_params.tvh_one_shot_watch:
                        await disable_pump_tvh_watch(session, watch.id)
                sent += len(to_send)
    finally:
        await asyncio.to_thread(end_background_tick)

    await _purge_expired()
    return sent


async def _purge_expired() -> None:
    async with session_scope() as session:
        n = await purge_expired_pump_tvh_watches(session)
    if n:
        log.debug("TVH watch: удалено просроченных %s", n)
