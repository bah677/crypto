#!/usr/bin/env python3
"""Ретро KAS с ослабленными порогами 1h — один prefetch, несколько сценариев."""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.pump_scan.timeframes as tf_mod
from app.bybit.rest import BybitRest
from app.pump_scan.params import PumpScanParams
from scripts.kas_retro_tvh import _fmt_dt, run_retro

MSK = ZoneInfo("Europe/Moscow")
SYMBOL = "KASUSDT"
START = datetime(2026, 6, 29, 17, 0, tzinfo=MSK)
END = datetime(2026, 6, 30, 13, 30, tzinfo=MSK)
INTERVALS = ("1", "5", "15", "30", "60", "240", "D")

SCENARIOS: list[tuple[str, dict[str, dict]]] = [
    ("A: 1h spike 3.5%", {"60": {"spike_pct": 3.5}}),
    ("B: 1h smooth 6% / 3 бара", {"60": {"smooth_pct": 6.0}}),
    ("C: 1h spike 5% + smooth 8%", {"60": {"spike_pct": 5.0, "smooth_pct": 8.0}}),
    ("D: 1h spike 3.5% + smooth 6%", {"60": {"spike_pct": 3.5, "smooth_pct": 6.0}}),
    (
        "E: 30m+1h (30: spike5/smooth10, 60: spike3.5/smooth6)",
        {
            "30": {"spike_pct": 5.0, "smooth_pct": 10.0},
            "60": {"spike_pct": 3.5, "smooth_pct": 6.0},
        },
    ),
]


def _prefetch(symbol: str, end_ms: int) -> dict[str, list]:
    client = BybitRest(category="linear")
    cache: dict[str, list] = {}
    for iv in INTERVALS:
        limit = 1000 if iv in ("1", "5", "15") else 200
        cache[iv] = client.get_kline_ohlcv(symbol, iv, limit=limit, end_ms=end_ms)
        time.sleep(0.35)
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


@contextmanager
def patched_profiles(overrides: dict[str, dict]):
    orig = dict(tf_mod._TF_DEFAULTS)
    new = dict(orig)
    for iv, kw in overrides.items():
        if iv in new:
            new[iv] = replace(new[iv], **kw)
    tf_mod._TF_DEFAULTS = new
    try:
        yield
    finally:
        tf_mod._TF_DEFAULTS = orig


def run_scenario(
    name: str,
    overrides: dict[str, dict],
    cache: dict[str, list],
    params: PumpScanParams,
) -> None:
    client = _patch_client(cache)
    with patched_profiles(overrides):
        # подменяем клиент внутри run_retro через monkeypatch модуля
        import scripts.kas_retro_tvh as retro_mod

        orig_cls = BybitRest
        retro_mod.BybitRest = lambda category="linear": client  # type: ignore[misc, assignment]
        try:
            events = run_retro(SYMBOL, START, END, params)
        finally:
            retro_mod.BybitRest = orig_cls

    impulses = [e for e in events if e.kind == "impulse"]
    tvhs = [e for e in events if e.kind == "tvh"]

    print("=" * 72)
    print(name)
    print("=" * 72)
    if impulses:
        for e in impulses:
            print(f"  ИМПУЛЬС {_fmt_dt(e.at)}  {e.detail}")
    else:
        print("  Импульсы: нет")
    if tvhs:
        for e in tvhs:
            print(f"  ТВХ     {_fmt_dt(e.at)}  {e.detail}")
    else:
        print("  ТВХ: нет")
    print()


def main() -> None:
    end_ms = int(END.timestamp() * 1000) + 3_600_000
    print(f"Prefetch {SYMBOL} klines…")
    cache = _prefetch(SYMBOL, end_ms)
    print(f"Загружено: {', '.join(f'{iv}={len(cache[iv])}' for iv in INTERVALS)}")
    print()

    params = PumpScanParams()
    print(f"KASUSDT {_fmt_dt(START)} — {_fmt_dt(END)}")
    print(f"ТВХ: score≥{params.tvh_min_score}, retrace={params.tvh_min_retrace_fade*100:.0f}%, TTL={params.tvh_watch_ttl_min}m")
    print()

    for name, ov in SCENARIOS:
        run_scenario(name, ov, cache, params)


if __name__ == "__main__":
    main()
