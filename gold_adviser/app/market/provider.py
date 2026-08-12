"""Provider chain: RealMarketAPI → Twelve Data."""

from __future__ import annotations

import logging

from app.market import realmarket, twelvedata
from app.market.candles import Candle

log = logging.getLogger(__name__)


async def fetch_xau_candles(limit: int = 30) -> tuple[list[Candle], str]:
    errors: list[str] = []
    try:
        return await realmarket.fetch_candles(limit=limit), "realmarket"
    except Exception as e:
        errors.append(f"realmarket: {e}")
        log.warning("RealMarketAPI failed: %s", e)

    try:
        return await twelvedata.fetch_candles(limit=limit), "twelvedata"
    except Exception as e:
        errors.append(f"twelvedata: {e}")
        log.warning("Twelve Data failed: %s", e)

    raise RuntimeError("Все провайдеры недоступны: " + "; ".join(errors))
