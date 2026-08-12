from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PumpTvhWatchRow

MSK = ZoneInfo("Europe/Moscow")


def _now() -> datetime:
    return datetime.now(MSK)


async def fetch_active_pump_tvh_watches(session: AsyncSession) -> list[PumpTvhWatchRow]:
    now = _now()
    r = await session.execute(
        select(PumpTvhWatchRow)
        .where(PumpTvhWatchRow.enabled.is_(True))
        .where(PumpTvhWatchRow.expires_at > now)
        .order_by(PumpTvhWatchRow.created_at)
    )
    return list(r.scalars().all())


async def get_active_watch(
    session: AsyncSession, symbol: str, impulse_direction: str
) -> PumpTvhWatchRow | None:
    sym = symbol.upper()
    direction = impulse_direction.lower()
    now = _now()
    r = await session.execute(
        select(PumpTvhWatchRow)
        .where(PumpTvhWatchRow.symbol == sym)
        .where(PumpTvhWatchRow.impulse_direction == direction)
        .where(PumpTvhWatchRow.enabled.is_(True))
        .where(PumpTvhWatchRow.expires_at > now)
    )
    return r.scalar_one_or_none()


async def upsert_pump_tvh_watch(
    session: AsyncSession,
    *,
    symbol: str,
    impulse_direction: str,
    source_interval: str,
    entry_interval: str,
    hit_data: dict,
    impulse_low: float,
    impulse_high: float,
    impulse_bar_open_ms: int,
    expires_at: datetime,
) -> PumpTvhWatchRow:
    row = await get_active_watch(session, symbol, impulse_direction)
    if row is None:
        row = PumpTvhWatchRow(
            symbol=symbol.upper(),
            impulse_direction=impulse_direction.lower(),
            source_interval=source_interval,
            entry_interval=entry_interval,
            impulse_low=impulse_low,
            impulse_high=impulse_high,
            impulse_bar_open_ms=impulse_bar_open_ms,
            expires_at=expires_at,
        )
        session.add(row)
    else:
        row.source_interval = source_interval
        row.entry_interval = entry_interval
        row.impulse_low = impulse_low
        row.impulse_high = impulse_high
        row.impulse_bar_open_ms = impulse_bar_open_ms
        row.expires_at = expires_at
        row.alerted_short = False
        row.alerted_long = False
    row.set_hit_dict(hit_data)
    row.enabled = True
    await session.commit()
    await session.refresh(row)
    return row


async def update_pump_tvh_watch_bounds(
    session: AsyncSession,
    row_id: int,
    *,
    impulse_low: float,
    impulse_high: float,
    expires_at: datetime | None = None,
) -> None:
    """Расширение рамки импульса без сброса флагов алертов."""
    r = await session.execute(select(PumpTvhWatchRow).where(PumpTvhWatchRow.id == row_id))
    row = r.scalar_one_or_none()
    if row is None:
        return
    row.impulse_low = impulse_low
    row.impulse_high = impulse_high
    if expires_at is not None:
        row.expires_at = expires_at
    await session.commit()


async def mark_tvh_alerted(
    session: AsyncSession,
    row_id: int,
    *,
    short: bool = False,
    long: bool = False,
) -> None:
    r = await session.execute(select(PumpTvhWatchRow).where(PumpTvhWatchRow.id == row_id))
    row = r.scalar_one_or_none()
    if row is None:
        return
    if short:
        row.alerted_short = True
    if long:
        row.alerted_long = True
    await session.commit()


async def disable_pump_tvh_watch(session: AsyncSession, row_id: int) -> None:
    r = await session.execute(select(PumpTvhWatchRow).where(PumpTvhWatchRow.id == row_id))
    row = r.scalar_one_or_none()
    if row is None:
        return
    row.enabled = False
    await session.commit()


async def purge_expired_pump_tvh_watches(session: AsyncSession) -> int:
    now = _now()
    r = await session.execute(
        delete(PumpTvhWatchRow).where(PumpTvhWatchRow.expires_at <= now)
    )
    await session.commit()
    return int(r.rowcount or 0)
