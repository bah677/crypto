#!/usr/bin/env python3
"""Ретро новой стратегии: pump + EMA 1D → топик, ТВХ только шорт-фейд."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.bybit.rest import BybitRest
from app.pump_scan.daily_ema import compute_daily_emas
from app.pump_scan.detect import detect_symbol_hits, fast_intervals, slow_intervals
from app.pump_scan.params import PumpScanParams
from app.pump_scan.tvh import (
    SCENARIO_LABELS,
    ImpulseContext,
    evaluate_tvh,
    filter_pump_short_fade,
    junior_interval,
)
from app.pump_scan.universe import PoolCoin
from app.services.pump_tvh_monitor import (
    _bars_with_volume,
    compute_impulse_bounds,
    expand_impulse_bounds_for_watch,
    tvh_params_from_scan,
)

MSK = ZoneInfo("Europe/Moscow")
SCAN_MINUTES = {1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, 56}
SLOW_SCAN_MINUTE = 8
INTERVALS = ("1", "5", "15", "30", "60", "240", "D")

RUNS = [
    ("BUSDT", datetime(2026, 7, 11, 12, 0, tzinfo=MSK)),
    ("ELSAUSDT", datetime(2026, 7, 11, 10, 0, tzinfo=MSK)),
]


@dataclass
class SimWatch:
    source_interval: str
    entry_interval: str
    impulse_low: float
    impulse_high: float
    impulse_bar_open_ms: int
    impulse_pct: float
    impulse_rvol: float
    move_kind: str
    expires_ms: int
    alerted_short: bool = False


@dataclass
class SimEvent:
    at: datetime
    kind: str  # impulse | tvh
    detail: str


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%d.%m %H:%M")


def _prefetch(symbol: str, end_ms: int) -> dict[str, list]:
    client = BybitRest(category="linear")
    cache: dict[str, list] = {}
    for iv in INTERVALS:
        limit = 1000 if iv in ("1", "5", "15") else 250
        cache[iv] = client.get_kline_ohlcv(symbol, iv, limit=limit, end_ms=end_ms)
        time.sleep(0.25)
    return cache


def _patch_client(cache: dict[str, list]) -> BybitRest:
    client = BybitRest(category="linear")

    def cached(symbol: str, interval: str, limit: int = 200, *, end_ms: int | None = None):
        bars = list(cache.get(interval, []))
        if end_ms is not None:
            bars = [b for b in bars if b[0] <= end_ms]
        if len(bars) > limit:
            bars = bars[-limit:]
        return bars

    client.get_kline_ohlcv = cached  # type: ignore[method-assign]
    return client


def _scan_intervals(params: PumpScanParams, minute: int) -> list[str]:
    ivs = list(fast_intervals(params))
    if minute == SLOW_SCAN_MINUTE:
        for iv in slow_intervals(params):
            if iv not in ivs:
                ivs.append(iv)
    return ivs


def _scenario_label(scenario: str) -> str:
    return SCENARIO_LABELS.get(scenario, scenario).replace("<b>", "").replace("</b>", "")


def run_retro(
    symbol: str,
    start: datetime,
    end: datetime,
    client: BybitRest,
    params: PumpScanParams,
) -> list[SimEvent]:
    tvh_p = tvh_params_from_scan(params)
    coin = PoolCoin(symbol=symbol, name=symbol, source="pool")
    events: list[SimEvent] = []
    watch: SimWatch | None = None
    last_pump_ms: int | None = None

    t = start.replace(second=0, microsecond=0)
    end = end.replace(second=0, microsecond=0)

    while t <= end:
        now_ms = _ms(t)

        if t.minute in SCAN_MINUTES:
            hits = detect_symbol_hits(
                client,
                coin,
                params,
                _scan_intervals(params, t.minute),
                as_of_ms=now_ms,
            )
            pump_hits = [h for h in hits if h.direction == "pump"]
            for hit in pump_hits:
                if last_pump_ms and (now_ms - last_pump_ms) < params.alert_cooldown_min * 60_000:
                    continue
                try:
                    low, high, open_ms = compute_impulse_bounds(client, hit, as_of_ms=now_ms)
                except Exception:
                    continue
                if high <= low:
                    continue
                emas = compute_daily_emas(client, symbol, as_of_ms=now_ms)
                ema_txt = "EMA1D: —"
                fire = "🔥"
                if emas:
                    from app.pump_scan.pump_strength import classify_pump_strength, pump_fire_prefix

                    fire = pump_fire_prefix(classify_pump_strength(hit, hit.close, emas))
                    ema_txt = " · ".join(emas.format_lines(price=hit.close))

                prev = watch is not None
                watch = SimWatch(
                    source_interval=hit.interval,
                    entry_interval=junior_interval(hit.interval),
                    impulse_low=low,
                    impulse_high=high,
                    impulse_bar_open_ms=open_ms,
                    impulse_pct=hit.price_change_pct,
                    impulse_rvol=hit.rvol,
                    move_kind=hit.move_kind,
                    expires_ms=now_ms + params.tvh_watch_ttl_min * 60_000,
                )
                last_pump_ms = now_ms
                events.append(
                    SimEvent(
                        at=t,
                        kind="impulse",
                        detail=(
                            f"{fire} PUMP алерт → топик 870 · TF {hit.interval} "
                            f"{hit.price_change_pct:+.1f}% RVOL×{hit.rvol:.1f} {hit.move_kind}\n"
                            f"      {ema_txt}"
                            + (" [вотч обновлён]" if prev else "")
                        ),
                    )
                )

        if watch is not None:
            if now_ms > watch.expires_ms:
                watch = None
            else:
                row = type(
                    "R",
                    (),
                    {
                        "symbol": symbol,
                        "source_interval": watch.source_interval,
                        "entry_interval": watch.entry_interval,
                        "impulse_bar_open_ms": watch.impulse_bar_open_ms,
                        "impulse_low": watch.impulse_low,
                        "impulse_high": watch.impulse_high,
                    },
                )()
                low, high, expanded = expand_impulse_bounds_for_watch(
                    client, row, as_of_ms=now_ms
                )
                if expanded:
                    watch.impulse_low = low
                    watch.impulse_high = high
                    watch.expires_ms = now_ms + params.tvh_watch_ttl_min * 60_000

                ctx = ImpulseContext(
                    symbol=symbol,
                    direction="pump",
                    source_interval=watch.source_interval,
                    entry_interval=watch.entry_interval,
                    impulse_low=watch.impulse_low,
                    impulse_high=watch.impulse_high,
                    impulse_pct=watch.impulse_pct,
                    impulse_rvol=watch.impulse_rvol,
                    move_kind=watch.move_kind,
                    impulse_bar_open_ms=watch.impulse_bar_open_ms,
                )
                bars = _bars_with_volume(
                    client, symbol, watch.entry_interval, limit=120, end_ms=now_ms
                )
                for cand in filter_pump_short_fade(evaluate_tvh(bars, ctx, tvh_p)):
                    if watch.alerted_short:
                        continue
                    events.append(
                        SimEvent(
                            at=t,
                            kind="tvh",
                            detail=(
                                f"📣 ТВХ шорт → топик 870 · {_scenario_label(cand.scenario)} "
                                f"score {cand.score} · TF {watch.entry_interval} · "
                                f"зона {cand.entry_low:.5g}–{cand.entry_high:.5g} · "
                                f"стоп ~{cand.invalidation:.5g} · "
                                f"{'; '.join(cand.reasons[:3])}"
                            ),
                        )
                    )
                    watch.alerted_short = True

        t += timedelta(minutes=1)

    return events


def main() -> None:
    end = datetime.now(MSK).replace(second=0, microsecond=0)
    params = PumpScanParams()

    print(f"Стратегия: pump+EMA1D + шорт-фейд · TTL {params.tvh_watch_ttl_min}m · score≥{params.tvh_min_score}")
    print(f"Конец периода: {_fmt(end)} MSK\n")

    for symbol, start in RUNS:
        end_ms = _ms(end) + 3_600_000
        print(f"Prefetch {symbol}…")
        cache = _prefetch(symbol, end_ms)
        client = _patch_client(cache)
        events = run_retro(symbol, start, end, client, params)

        print("=" * 72)
        print(f"{symbol}  {_fmt(start)} — {_fmt(end)} MSK")
        print("=" * 72)
        if not events:
            print("  (нет алертов)\n")
            continue
        for e in events:
            tag = "ИМПУЛЬС" if e.kind == "impulse" else "ТВХ    "
            print(f"  {_fmt(e.at)}  {tag}  {e.detail}")
        print()


if __name__ == "__main__":
    main()
