"""Evaluate pump alert outcomes after a horizon window."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.bybit.rest import BybitRest
from app.config import get_settings
from app.db.session import session_scope
from app.repository.pump_outcomes import (
    fetch_due_unevaluated_outcomes,
    mark_outcome_evaluated,
)

log = logging.getLogger(__name__)


def _evaluate_outcome_sync(row) -> tuple[float | None, float | None, bool, bool, bool]:
    """
    Compute:
    - MFE% for fade short: how much price went DOWN from entry (best low)
    - MAE% against short: how much price went UP from entry (worst high)
    - reached EMA levels (low <= EMAx)
    """
    client = BybitRest(category="linear")
    entry = float(row.entry_price or 0)
    if entry <= 0:
        return None, None, False, False, False

    start_ms = int(row.alerted_at.astimezone(timezone.utc).timestamp() * 1000)
    end_ms = start_ms + int(row.horizon_hours or 0) * 3600 * 1000
    # Use 5m bars for outcome window; cap to 1200 bars to avoid huge requests.
    limit = min(1200, max(50, int(row.horizon_hours or 48) * 12 + 10))
    bars = client.get_kline_ohlcv(row.symbol, "5", limit=limit, end_ms=end_ms)
    if not bars:
        return None, None, False, False, False
    bars.sort(key=lambda x: x[0])
    bars = [b for b in bars if start_ms <= b[0] <= end_ms]
    if not bars:
        return None, None, False, False, False

    highs = [float(b[2]) for b in bars]
    lows = [float(b[3]) for b in bars]
    hi = max(highs) if highs else entry
    lo = min(lows) if lows else entry

    mfe = (entry - lo) / entry * 100.0 if entry > 0 else None
    mae = (hi - entry) / entry * 100.0 if entry > 0 else None

    e50 = row.ema50_1d
    e100 = row.ema100_1d
    e200 = row.ema200_1d
    reached_50 = bool(e50 is not None and lo <= float(e50))
    reached_100 = bool(e100 is not None and lo <= float(e100))
    reached_200 = bool(e200 is not None and lo <= float(e200))
    return mfe, mae, reached_50, reached_100, reached_200


async def run_pump_outcome_eval_tick() -> None:
    s = get_settings()
    if not s.pump_scan_enabled:
        return
    # controlled by PumpScanParams.outcome_logging_enabled (checked at insert time)

    async with session_scope() as session:
        rows = await fetch_due_unevaluated_outcomes(session, limit=50)
    if not rows:
        return

    for row in rows:
        try:
            mfe, mae, r50, r100, r200 = await asyncio.to_thread(_evaluate_outcome_sync, row)
        except Exception:
            log.exception("outcome eval id=%s %s", row.id, row.symbol)
            continue
        async with session_scope() as session:
            await mark_outcome_evaluated(
                session,
                int(row.id),
                mfe_pct=mfe,
                mae_pct=mae,
                reached_ema50=r50,
                reached_ema100=r100,
                reached_ema200=r200,
            )
            await session.commit()
        log.info(
            "Outcome %s %s: mfe=%.1f mae=%.1f ema50=%s",
            row.id,
            row.symbol,
            mfe if mfe is not None else -1,
            mae if mae is not None else -1,
            r50,
        )

