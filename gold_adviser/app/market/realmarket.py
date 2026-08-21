"""RealMarketAPI — primary XAUUSD M1 candles provider."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.market.candles import Candle, assert_m1_spacing, parse_iso_dt

log = logging.getLogger(__name__)


def _as_list(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("Data", "data", "candles", "Candles", "items", "result"):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
        # single candle object
        if any(k in payload for k in ("OpenPrice", "open", "Open")):
            return [payload]
    return []


def _num(obj: dict, *keys: str) -> float | None:
    for k in keys:
        if k in obj and obj[k] is not None:
            try:
                return float(obj[k])
            except (TypeError, ValueError):
                continue
    return None


def _candle_from_obj(obj: dict) -> Candle | None:
    try:
        o = _num(obj, "openPrice", "OpenPrice", "open", "Open")
        h = _num(obj, "highPrice", "HighPrice", "high", "High")
        l = _num(obj, "lowPrice", "LowPrice", "low", "Low")
        c = _num(obj, "closePrice", "ClosePrice", "close", "Close")
        vol = _num(obj, "volume", "Volume") or 0.0
        ot = (
            obj.get("openTime")
            or obj.get("OpenTime")
            or obj.get("datetime")
            or obj.get("time")
        )
        if o is None or h is None or l is None or c is None or ot is None:
            return None
        return Candle(
            open_time=parse_iso_dt(ot),
            open=o,
            high=h,
            low=l,
            close=c,
            volume=vol,
        )
    except (TypeError, ValueError, KeyError):
        return None


async def fetch_candles(limit: int = 30) -> list[Candle]:
    s = get_settings()
    if not (s.realmarket_api_key or "").strip():
        raise RuntimeError("REALMARKET_API_KEY не задан")

    base = s.realmarket_base_url.rstrip("/")
    params = {
        "apiKey": s.realmarket_api_key,
        "symbolCode": s.symbol.upper().replace("/", ""),
        "timeFrame": "M1",
        "limit": max(2, int(limit)),
        "count": max(2, int(limit)),
        "pageSize": max(2, int(limit)),
    }
    url = f"{base}/api/v1/candle"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        payload = r.json()

    raw = _as_list(payload)
    candles: list[Candle] = []
    for obj in raw:
        c = _candle_from_obj(obj)
        if c is not None:
            candles.append(c)

    # API returns most-recent first → chronological asc
    candles.sort(key=lambda x: x.open_time)
    if len(candles) > limit:
        candles = candles[-limit:]
    if len(candles) < 2:
        raise RuntimeError(f"RealMarketAPI: мало свечей ({len(candles)})")
    # FREE-план RM часто отдаёт 5m-бары даже при timeFrame=M1
    assert_m1_spacing(candles, provider="RealMarketAPI")
    log.debug("RealMarketAPI candles=%s last=%s", len(candles), candles[-1].open_time_key)
    return candles
