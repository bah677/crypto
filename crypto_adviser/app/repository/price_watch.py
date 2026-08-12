from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PriceWatchRow


async def fetch_enabled_price_watch(session: AsyncSession) -> list[PriceWatchRow]:
    r = await session.execute(
        select(PriceWatchRow)
        .where(PriceWatchRow.enabled.is_(True))
        .order_by(PriceWatchRow.symbol)
    )
    return list(r.scalars().all())


async def fetch_all_price_watch(session: AsyncSession) -> list[PriceWatchRow]:
    r = await session.execute(select(PriceWatchRow).order_by(PriceWatchRow.symbol))
    return list(r.scalars().all())


async def get_price_watch(session: AsyncSession, symbol: str) -> PriceWatchRow | None:
    sym = symbol.upper().strip()
    r = await session.execute(
        select(PriceWatchRow).where(PriceWatchRow.symbol == sym)
    )
    return r.scalar_one_or_none()


async def add_price_watch(
    session: AsyncSession,
    *,
    symbol: str,
    alias: str = "",
) -> PriceWatchRow:
    sym = symbol.upper().strip()
    row = await get_price_watch(session, sym)
    if row is None:
        row = PriceWatchRow(symbol=sym, alias=alias.strip(), enabled=True)
        session.add(row)
    else:
        row.enabled = True
        if alias.strip():
            row.alias = alias.strip()
    await session.commit()
    await session.refresh(row)
    return row


async def set_price_watch_enabled(
    session: AsyncSession, symbol: str, enabled: bool
) -> PriceWatchRow | None:
    row = await get_price_watch(session, symbol)
    if row is None:
        return None
    row.enabled = enabled
    await session.commit()
    await session.refresh(row)
    return row


async def delete_price_watch(session: AsyncSession, symbol: str) -> bool:
    row = await get_price_watch(session, symbol)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True
