"""Provider chain: Bybit XAUUSDT perp → Twelve Data → RealMarketAPI.

Bybit публичный kline почти без лага после close M1.
Twelve Data / RealMarket — запасные (TD часто ~60с, RM FREE часто отдаёт M5).
"""

from __future__ import annotations

import logging
import time

from app.market import bybit, realmarket, twelvedata
from app.market.candles import Candle

log = logging.getLogger(__name__)

_RM_COOLDOWN_SEC = 1800.0
_rm_skip_until: float = 0.0


def _rm_in_cooldown() -> bool:
    return time.monotonic() < _rm_skip_until


def _trip_rm_cooldown(reason: str) -> None:
    global _rm_skip_until
    _rm_skip_until = time.monotonic() + _RM_COOLDOWN_SEC
    log.warning("RealMarketAPI cooldown %ss: %s", int(_RM_COOLDOWN_SEC), reason)


async def fetch_xau_candles(limit: int = 30) -> tuple[list[Candle], str]:
    errors: list[str] = []

    try:
        return await bybit.fetch_candles(limit=limit), "bybit"
    except Exception as e:
        errors.append(f"bybit: {e}")
        log.warning("Bybit failed: %s", e)

    try:
        return await twelvedata.fetch_candles(limit=limit), "twelvedata"
    except Exception as e:
        errors.append(f"twelvedata: {e}")
        log.warning("Twelve Data failed: %s", e)

    if _rm_in_cooldown():
        raise RuntimeError(
            "Все провайдеры недоступны: "
            + "; ".join(errors)
            + "; realmarket: cooldown"
        )

    try:
        return await realmarket.fetch_candles(limit=limit), "realmarket"
    except Exception as e:
        errors.append(f"realmarket: {e}")
        msg = str(e)
        if "шаг" in msg or "M1" in msg or "мало свечей" in msg:
            _trip_rm_cooldown(msg)
        else:
            log.warning("RealMarketAPI failed: %s", e)

    raise RuntimeError("Все провайдеры недоступны: " + "; ".join(errors))
