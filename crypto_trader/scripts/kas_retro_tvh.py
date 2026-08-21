#!/usr/bin/env python3
"""Ретроспектива pump/dump + ТВХ для одной монеты."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.bybit.rest import BybitRest
from app.pump_scan.detect import (
    ScanHit,
    detect_symbol_hits,
    fast_intervals,
    slow_intervals,
)
from app.pump_scan.params import PumpScanParams
from app.pump_scan.tvh import SCENARIO_LABELS, junior_interval
from app.pump_scan.universe import PoolCoin
from app.services.pump_tvh_monitor import (
    compute_impulse_bounds,
    expand_impulse_bounds_for_watch,
    scenario_flags,
    tvh_params_from_scan,
)
from app.pump_scan.tvh import ImpulseContext, evaluate_tvh

MSK = ZoneInfo("Europe/Moscow")

SCAN_MINUTES = {1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, 56}
SLOW_SCAN_MINUTE = 8


@dataclass
class SimWatch:
    direction: str
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
    alerted_long: bool = False
    impulse_at: datetime = field(default_factory=lambda: datetime.now(MSK))


@dataclass
class SimEvent:
    at: datetime
    kind: str  # impulse | tvh
    direction: str
    detail: str


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M MSK")


def _cooldown_ok(last_ms: int | None, now_ms: int, cooldown_min: int) -> bool:
    if last_ms is None:
        return True
    return (now_ms - last_ms) >= cooldown_min * 60 * 1000


def _scan_intervals(params: PumpScanParams, minute: int, hour: int) -> list[str]:
    ivs = list(fast_intervals(params))
    if minute == SLOW_SCAN_MINUTE:
        for iv in slow_intervals(params):
            if iv not in ivs:
                ivs.append(iv)
    return ivs


def _scenario_label(scenario: str) -> str:
    raw = SCENARIO_LABELS.get(scenario, scenario)
    return raw.replace("<b>", "").replace("</b>", "")


def run_retro(
    symbol: str,
    start: datetime,
    end: datetime,
    params: PumpScanParams | None = None,
) -> list[SimEvent]:
    params = params or PumpScanParams()
    tvh_p = tvh_params_from_scan(params)
    coin = PoolCoin(symbol=symbol.upper(), name=symbol, source="pool")
    client = BybitRest(category="linear")

    events: list[SimEvent] = []
    watches: dict[str, SimWatch] = {}  # key: direction
    last_impulse_ms: dict[str, int] = {}

    t = start.replace(second=0, microsecond=0)
    end = end.replace(second=0, microsecond=0)

    while t <= end:
        now_ms = _ms(t)

        # --- pump scan (каждые 5 мин по расписанию) ---
        if t.minute in SCAN_MINUTES:
            label = _fmt_dt(t)
            intervals = _scan_intervals(params, t.minute, t.hour)
            hits = detect_symbol_hits(
                client,
                coin,
                params,
                intervals,
                as_of_ms=now_ms,
                as_of_label=label,
            )
            for hit in hits:
                key = hit.direction
                if not _cooldown_ok(last_impulse_ms.get(key), now_ms, params.alert_cooldown_min):
                    continue
                try:
                    low, high, open_ms = compute_impulse_bounds(
                        client, hit, as_of_ms=now_ms
                    )
                except Exception:
                    continue
                if high <= low:
                    continue
                entry_iv = junior_interval(hit.interval)
                ttl_ms = params.tvh_watch_ttl_min * 60 * 1000
                prev = watches.get(key)
                watches[key] = SimWatch(
                    direction=hit.direction,
                    source_interval=hit.interval,
                    entry_interval=entry_iv,
                    impulse_low=low,
                    impulse_high=high,
                    impulse_bar_open_ms=open_ms,
                    impulse_pct=hit.price_change_pct,
                    impulse_rvol=hit.rvol,
                    move_kind=hit.move_kind,
                    expires_ms=now_ms + ttl_ms,
                    impulse_at=t,
                )
                last_impulse_ms[key] = now_ms
                icon = "🔥" if hit.direction == "pump" else "🔻"
                events.append(
                    SimEvent(
                        at=t,
                        kind="impulse",
                        direction=hit.direction,
                        detail=(
                            f"{icon} импульс {hit.interval} · ход {hit.price_change_pct:+.1f}% "
                            f"· RVOL ×{hit.rvol:.1f} · {hit.move_kind} → вотчлист "
                            f"(→{entry_iv}, L={low:.5f} H={high:.5f})"
                            + (" [обновлён]" if prev else "")
                        ),
                    )
                )

        # --- TVH monitor (:18 каждой минуты → оценка на закрытии текущей минуты) ---
        if True:  # каждую минуту, как тик монитора
            expired = [k for k, w in watches.items() if now_ms > w.expires_ms]
            for k in expired:
                del watches[k]

            for key, w in list(watches.items()):
                expanded_low, expanded_high, expanded = expand_impulse_bounds_for_watch(
                    client,
                    type("Row", (), {
                        "symbol": symbol.upper(),
                        "source_interval": w.source_interval,
                        "entry_interval": w.entry_interval,
                        "impulse_bar_open_ms": w.impulse_bar_open_ms,
                        "impulse_low": w.impulse_low,
                        "impulse_high": w.impulse_high,
                    })(),
                    as_of_ms=now_ms,
                )
                if expanded:
                    w.impulse_low = expanded_low
                    w.impulse_high = expanded_high
                    w.expires_ms = now_ms + params.tvh_watch_ttl_min * 60 * 1000

                ctx = ImpulseContext(
                    symbol=symbol.upper(),
                    direction=w.direction,  # type: ignore[arg-type]
                    source_interval=w.source_interval,
                    entry_interval=w.entry_interval,
                    impulse_low=w.impulse_low,
                    impulse_high=w.impulse_high,
                    impulse_pct=w.impulse_pct,
                    impulse_rvol=w.impulse_rvol,
                    move_kind=w.move_kind,
                    impulse_bar_open_ms=w.impulse_bar_open_ms,
                )
                from app.services.pump_tvh_monitor import _bars_with_volume

                bars = _bars_with_volume(
                    client, symbol.upper(), w.entry_interval, limit=120, end_ms=now_ms
                )
                if not bars:
                    continue
                for cand in evaluate_tvh(bars, ctx, tvh_p):
                    is_short, is_long = scenario_flags(cand.scenario)
                    if is_short and w.alerted_short:
                        continue
                    if is_long and w.alerted_long:
                        continue
                    events.append(
                        SimEvent(
                            at=t,
                            kind="tvh",
                            direction=w.direction,
                            detail=(
                                f"📣 ТВХ {_scenario_label(cand.scenario)} · score {cand.score} "
                                f"· TF {w.entry_interval} · зона {cand.entry_low:.5f}–{cand.entry_high:.5f} "
                                f"· стоп ~{cand.invalidation:.5f} · "
                                f"{'; '.join(cand.reasons[:3])}"
                            ),
                        )
                    )
                    if is_short:
                        w.alerted_short = True
                    if is_long:
                        w.alerted_long = True
                    if params.tvh_one_shot_watch:
                        del watches[key]
                        break

        t += timedelta(minutes=1)

    return events


def main() -> None:
    start = datetime(2026, 6, 29, 17, 0, tzinfo=MSK)
    end = datetime(2026, 6, 30, 13, 30, tzinfo=MSK)
    params = PumpScanParams()
    events = run_retro("KASUSDT", start, end, params)

    print(f"KASUSDT ретро {_fmt_dt(start)} — {_fmt_dt(end)}")
    print(f"Параметры ТВХ: score≥{params.tvh_min_score} retrace_fade={params.tvh_min_retrace_fade*100:.0f}% TTL={params.tvh_watch_ttl_min}m")
    print()

    impulses = [e for e in events if e.kind == "impulse"]
    tvhs = [e for e in events if e.kind == "tvh"]

    print(f"=== Импульсы в вотчлист ({len(impulses)}) ===")
    for e in impulses:
        print(f"{_fmt_dt(e.at)}  {e.detail}")
    if not impulses:
        print("(нет)")

    print()
    print(f"=== Алерты ТВХ в топик ({len(tvhs)}) ===")
    for e in tvhs:
        print(f"{_fmt_dt(e.at)}  [{e.direction}] {e.detail}")
    if not tvhs:
        print("(нет — импульс не найден или ТВХ не подтвердилась)")


if __name__ == "__main__":
    main()
