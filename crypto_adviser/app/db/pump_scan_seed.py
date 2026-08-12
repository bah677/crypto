"""Seed singleton Pump&Dump config."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PumpScanConfigRow


async def seed_pump_scan_config_if_empty(session: AsyncSession) -> None:
    row = await session.get(PumpScanConfigRow, 1)
    if row is not None:
        return
    session.add(PumpScanConfigRow(id=1, enabled=False))
    await session.commit()
