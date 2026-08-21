"""Pump alert outcomes repository."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PumpAlertOutcomeRow


async def create_pump_outcome(
    session: AsyncSession,
    *,
    symbol: str,
    direction: str,
    interval: str,
    move_kind: str,
    window_bars: int,
    entry_price: float,
    score: float,
    features: dict,
    ema50_1d: float | None,
    ema100_1d: float | None,
    ema200_1d: float | None,
    horizon_hours: int,
) -> PumpAlertOutcomeRow:
    row = PumpAlertOutcomeRow(
        symbol=symbol.upper(),
        direction=direction,
        interval=interval,
        move_kind=move_kind,
        window_bars=int(window_bars),
        entry_price=float(entry_price),
        score=float(score),
        ema50_1d=ema50_1d,
        ema100_1d=ema100_1d,
        ema200_1d=ema200_1d,
        horizon_hours=int(horizon_hours),
        evaluated=False,
    )
    row.set_features(features)
    session.add(row)
    await session.flush()
    return row


async def fetch_due_unevaluated_outcomes(
    session: AsyncSession,
    *,
    limit: int = 50,
) -> list[PumpAlertOutcomeRow]:
    """
    Outcomes become "due" after their horizon window passed (alerted_at + horizon_hours).
    """
    now = datetime.now(tz=timezone.utc)
    # We cannot express alerted_at + horizon_hours in portable SQL easily without func,
    # so we fetch a small batch of unevaluated and check in Python.
    r = await session.execute(
        select(PumpAlertOutcomeRow)
        .where(PumpAlertOutcomeRow.evaluated.is_(False))
        .order_by(PumpAlertOutcomeRow.id)
        .limit(int(max(1, min(limit, 200))))
    )
    rows = list(r.scalars().all())
    due: list[PumpAlertOutcomeRow] = []
    for row in rows:
        horizon = timedelta(hours=int(row.horizon_hours or 0))
        if row.alerted_at and row.alerted_at.astimezone(timezone.utc) + horizon <= now:
            due.append(row)
    return due


async def mark_outcome_evaluated(
    session: AsyncSession,
    outcome_id: int,
    *,
    mfe_pct: float | None,
    mae_pct: float | None,
    reached_ema50: bool,
    reached_ema100: bool,
    reached_ema200: bool,
) -> None:
    await session.execute(
        update(PumpAlertOutcomeRow)
        .where(PumpAlertOutcomeRow.id == outcome_id)
        .values(
            evaluated=True,
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
            reached_ema50=bool(reached_ema50),
            reached_ema100=bool(reached_ema100),
            reached_ema200=bool(reached_ema200),
        )
    )

