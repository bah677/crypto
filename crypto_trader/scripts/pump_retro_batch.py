#!/usr/bin/env python3
"""Ретро pump/dump + ТВХ для нескольких пар (дефолтные пороги, взрывная модель)."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.bybit.rest import BybitRest
from app.pump_scan.params import PumpScanParams
from scripts.kas_retro_tvh import _fmt_dt, run_retro

MSK = ZoneInfo("Europe/Moscow")
INTERVALS = ("1", "5", "15", "30", "60", "240", "D")
WINDOW_H = 48

RUNS: list[tuple[str, datetime]] = [
    ("ORDIUSDT", datetime(2026, 6, 29, 14, 0, tzinfo=MSK)),
    ("REUSDT", datetime(2026, 6, 29, 4, 0, tzinfo=MSK)),
    ("HEIUSDT", datetime(2026, 6, 29, 16, 0, tzinfo=MSK)),
    ("SNDKUSDT", datetime(2026, 6, 28, 22, 0, tzinfo=MSK)),
]


def _prefetch(symbol: str, end_ms: int) -> dict[str, list]:
    client = BybitRest(category="linear")
    cache: dict[str, list] = {}
    for iv in INTERVALS:
        limit = 1000 if iv in ("1", "5", "15") else 200
        cache[iv] = client.get_kline_ohlcv(symbol, iv, limit=limit, end_ms=end_ms)
        time.sleep(0.3)
    return cache


def _patch_client(cache: dict[str, list]) -> BybitRest:
    client = BybitRest(category="linear")

    def cached_get_kline_ohlcv(
        symbol: str,
        interval: str,
        limit: int = 200,
        *,
        end_ms: int | None = None,
    ) -> list[tuple[int, float, float, float, float, float]]:
        bars = list(cache.get(interval, []))
        if end_ms is not None:
            bars = [b for b in bars if b[0] <= end_ms]
        if len(bars) > limit:
            bars = bars[-limit:]
        return bars

    client.get_kline_ohlcv = cached_get_kline_ohlcv  # type: ignore[method-assign]
    return client


def main() -> None:
    params = PumpScanParams()
    print("Взрывная модель (дефолтные пороги)")
    print(
        f"ТВХ: score≥{params.tvh_min_score}, retrace={params.tvh_min_retrace_fade*100:.0f}%, "
        f"TTL={params.tvh_watch_ttl_min}m · окно +{WINDOW_H}ч от старта\n"
    )

    import scripts.kas_retro_tvh as retro_mod

    orig_cls = BybitRest

    for symbol, start in RUNS:
        end = start + timedelta(hours=WINDOW_H)
        end_ms = int(end.timestamp() * 1000) + 3_600_000
        cache = _prefetch(symbol, end_ms)
        client = _patch_client(cache)
        retro_mod.BybitRest = lambda category="linear": client  # type: ignore[misc, assignment]
        try:
            events = run_retro(symbol, start, end, params)
        finally:
            retro_mod.BybitRest = orig_cls

        impulses = [e for e in events if e.kind == "impulse"]
        tvhs = [e for e in events if e.kind == "tvh"]

        print("=" * 72)
        print(f"{symbol}  {_fmt_dt(start)} — {_fmt_dt(end)}")
        print("=" * 72)
        if impulses:
            for e in impulses:
                print(f"  ИМПУЛЬС {_fmt_dt(e.at)}  {e.detail}")
        else:
            print("  Импульсы: нет")
        if tvhs:
            for e in tvhs:
                print(f"  ТВХ     {_fmt_dt(e.at)}  [{e.direction}] {e.detail}")
        else:
            print("  ТВХ: нет")
        print()


if __name__ == "__main__":
    main()
