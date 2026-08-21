"""Ранг монеты на Binance USDT-perp по объёму за 24ч."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

_BINANCE_FUTURES_24H = "https://fapi.binance.com/fapi/v1/ticker/24hr"
_CACHE_TTL_S = 300.0
_rank_map: dict[str, int] | None = None
_rank_cache_mono: float = 0.0


def _fetch_rank_map() -> dict[str, int]:
    global _rank_map, _rank_cache_mono
    now = time.monotonic()
    if _rank_map is not None and now - _rank_cache_mono < _CACHE_TTL_S:
        return _rank_map

    req = urllib.request.Request(
        _BINANCE_FUTURES_24H,
        headers={"User-Agent": "traiding_bot_ema/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        log.warning("Binance 24h tickers: не удалось загрузить", exc_info=True)
        return _rank_map or {}

    rows: list[tuple[str, float]] = []
    for row in data:
        sym = str(row.get("symbol") or "").upper()
        if not sym.endswith("USDT"):
            continue
        try:
            vol = float(row.get("quoteVolume") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        if vol > 0:
            rows.append((sym, vol))

    rows.sort(key=lambda x: x[1], reverse=True)
    _rank_map = {sym: rank for rank, (sym, _) in enumerate(rows, start=1)}
    _rank_cache_mono = now
    log.debug("Binance volume ranks: %s USDT пар", len(_rank_map))
    return _rank_map


def binance_usdt_volume_rank(symbol: str) -> int | None:
    """Позиция в топе Binance USDT-perp по quoteVolume 24h (1 = макс.)."""
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym = f"{sym}USDT"
    return _fetch_rank_map().get(sym)
