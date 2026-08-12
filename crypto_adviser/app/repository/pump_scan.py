"""Pump&Dump config repository."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PumpScanConfigRow
from app.pump_scan.params import PumpScanParams
from app.pump_scan.universe import PoolCoin

_PUMP_CONFIG_ID = 1


async def get_pump_config(session: AsyncSession) -> PumpScanConfigRow:
    row = await session.get(PumpScanConfigRow, _PUMP_CONFIG_ID)
    if row is None:
        row = PumpScanConfigRow(id=_PUMP_CONFIG_ID, enabled=False)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


async def set_pump_enabled(session: AsyncSession, enabled: bool) -> PumpScanConfigRow:
    row = await get_pump_config(session)
    row.enabled = enabled
    await session.commit()
    await session.refresh(row)
    return row


async def update_pump_params(
    session: AsyncSession, params: PumpScanParams
) -> PumpScanConfigRow:
    row = await get_pump_config(session)
    row.set_params(params)
    await session.commit()
    await session.refresh(row)
    return row


async def update_pump_pool(
    session: AsyncSession, coins: list[PoolCoin]
) -> PumpScanConfigRow:
    row = await get_pump_config(session)
    row.set_pool(coins)
    row.pool_updated_at = datetime.now(tz=timezone.utc)
    await session.commit()
    await session.refresh(row)
    return row


async def fetch_pump_config(session: AsyncSession) -> PumpScanConfigRow | None:
    return await session.get(PumpScanConfigRow, _PUMP_CONFIG_ID)
