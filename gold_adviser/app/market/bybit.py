"""Bybit public market — XAUUSDT linear perpetual M1 (USDT-m).

Цены отличаются от спота XAU/USD, геометрия свечей обычно совпадает.
Публичный /v5/market/kline — без API-ключа.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from app.config import get_settings
from app.market.candles import Candle, assert_m1_spacing

log = logging.getLogger(__name__)

_BYBIT_KLINE = "https://api.bybit.com/v5/market/kline"


async def fetch_candles(limit: int = 30) -> list[Candle]:
    s = get_settings()
    symbol = (s.bybit_symbol or "XAUUSDT").strip().upper()
    category = (s.bybit_category or "linear").strip().lower()
    n = max(2, min(int(limit), 1000))

    params = {
        "category": category,
        "symbol": symbol,
        "interval": "1",
        "limit": n,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(_BYBIT_KLINE, params=params)
        r.raise_for_status()
        payload = r.json()

    if int(payload.get("retCode") or 0) != 0:
        raise RuntimeError(f"Bybit: {payload.get('retMsg') or payload}")

    rows = (payload.get("result") or {}).get("list") or []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Bybit: пустой kline")

    candles: list[Candle] = []
    for row in rows:
        if not row or len(row) < 5:
            continue
        try:
            start_ms = int(row[0])
            o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
            vol = float(row[5]) if len(row) > 5 and row[5] not in (None, "") else 0.0
        except (TypeError, ValueError):
            continue
        candles.append(
            Candle(
                open_time=datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc),
                open=o,
                high=h,
                low=l,
                close=c,
                volume=vol,
            )
        )

    candles.sort(key=lambda x: x.open_time)
    if len(candles) > n:
        candles = candles[-n:]
    if len(candles) < 2:
        raise RuntimeError(f"Bybit: мало свечей ({len(candles)})")
    assert_m1_spacing(candles, provider=f"Bybit:{symbol}")
    log.debug(
        "Bybit %s/%s candles=%s last=%s",
        category,
        symbol,
        len(candles),
        candles[-1].open_time_key,
    )
    return candles
