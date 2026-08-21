"""Twelve Data — fallback XAU/USD 1min candles."""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.market.candles import Candle, assert_m1_spacing, parse_iso_dt

log = logging.getLogger(__name__)


async def fetch_candles(limit: int = 30) -> list[Candle]:
    s = get_settings()
    if not (s.twelvedata_api_key or "").strip():
        raise RuntimeError("TWELVEDATA_API_KEY не задан")

    symbol = s.symbol.upper()
    if "/" not in symbol:
        # XAUUSD → XAU/USD
        if symbol.endswith("USD") and len(symbol) > 3:
            symbol = f"{symbol[:-3]}/{symbol[-3:]}"
        else:
            symbol = "XAU/USD"

    params = {
        "symbol": symbol,
        "interval": "1min",
        "outputsize": max(2, int(limit)),
        "apikey": s.twelvedata_api_key,
        "timezone": "UTC",
        "order": "ASC",
    }
    url = f"{s.twelvedata_base_url.rstrip('/')}/time_series"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        payload = r.json()

    if isinstance(payload, dict) and payload.get("status") == "error":
        raise RuntimeError(f"Twelve Data: {payload.get('message') or payload}")

    values = payload.get("values") if isinstance(payload, dict) else None
    if not isinstance(values, list) or not values:
        raise RuntimeError("Twelve Data: пустой ответ")

    candles: list[Candle] = []
    for obj in values:
        if not isinstance(obj, dict):
            continue
        try:
            candles.append(
                Candle(
                    open_time=parse_iso_dt(obj["datetime"]),
                    open=float(obj["open"]),
                    high=float(obj["high"]),
                    low=float(obj["low"]),
                    close=float(obj["close"]),
                    volume=float(obj.get("volume") or 0),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    candles.sort(key=lambda x: x.open_time)
    if len(candles) > limit:
        candles = candles[-limit:]
    if len(candles) < 2:
        raise RuntimeError(f"Twelve Data: мало свечей ({len(candles)})")
    assert_m1_spacing(candles, provider="TwelveData")
    log.debug("TwelveData candles=%s last=%s", len(candles), candles[-1].open_time_key)
    return candles
