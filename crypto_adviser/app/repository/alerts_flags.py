from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BotAlertsFlags

_SINGLETON_ID = 1


async def get_alerts_flags(session: AsyncSession) -> BotAlertsFlags:
    r = await session.execute(
        select(BotAlertsFlags).where(BotAlertsFlags.id == _SINGLETON_ID)
    )
    row = r.scalar_one_or_none()
    if row is not None:
        return row
    row = BotAlertsFlags(
        id=_SINGLETON_ID,
        ema_sl_reports=True,
        price_spike_reports=True,
        funding_reports=True,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def set_ema_sl_reports(session: AsyncSession, enabled: bool) -> BotAlertsFlags:
    row = await get_alerts_flags(session)
    row.ema_sl_reports = enabled
    await session.commit()
    await session.refresh(row)
    return row


async def set_price_spike_reports(
    session: AsyncSession, enabled: bool
) -> BotAlertsFlags:
    row = await get_alerts_flags(session)
    row.price_spike_reports = enabled
    await session.commit()
    await session.refresh(row)
    return row


async def set_funding_reports(session: AsyncSession, enabled: bool) -> BotAlertsFlags:
    row = await get_alerts_flags(session)
    row.funding_reports = enabled
    await session.commit()
    await session.refresh(row)
    return row
