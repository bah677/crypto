from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SlAnomCloseMasterRow
from app.services.sl_anom_close_params import SlAnomCloseParams


async def get_sl_anom_close_master(session: AsyncSession) -> SlAnomCloseMasterRow:
    row = await session.execute(select(SlAnomCloseMasterRow).where(SlAnomCloseMasterRow.id == 1))
    r = row.scalar_one_or_none()
    if r is None:
        r = SlAnomCloseMasterRow(id=1, enabled=True, config_json="{}")
        session.add(r)
        await session.commit()
        await session.refresh(r)
    return r


def _parse_params(config_json: str | None) -> SlAnomCloseParams:
    raw: Any = json.loads(config_json or "{}")
    if not isinstance(raw, dict):
        return SlAnomCloseParams()
    return SlAnomCloseParams.from_dict(raw)


async def get_sl_anom_close_params(session: AsyncSession) -> SlAnomCloseParams:
    master = await get_sl_anom_close_master(session)
    return _parse_params(master.config_json)


async def set_sl_anom_close_params(
    session: AsyncSession,
    *,
    enabled: bool | None = None,
    params: SlAnomCloseParams | None = None,
) -> SlAnomCloseMasterRow:
    master = await get_sl_anom_close_master(session)
    if enabled is not None:
        master.enabled = enabled
    if params is not None:
        master.config_json = json.dumps(params.to_dict(), ensure_ascii=False)
    await session.commit()
    await session.refresh(master)
    return master

