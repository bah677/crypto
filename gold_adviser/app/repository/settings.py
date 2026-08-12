from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GoldAlertRow, GoldSettingsRow


@dataclass(frozen=True)
class GoldRuntimeSettings:
    enabled: bool
    body_mult: float
    lookback: int
    settings_cache_ttl_sec: int
    updated_by: int | None = None


async def get_settings_row(session: AsyncSession) -> GoldSettingsRow | None:
    return await session.get(GoldSettingsRow, 1)


async def load_runtime_settings(session: AsyncSession) -> GoldRuntimeSettings:
    row = await get_settings_row(session)
    if row is None:
        return GoldRuntimeSettings(
            enabled=True,
            body_mult=2.0,
            lookback=30,
            settings_cache_ttl_sec=30,
        )
    return GoldRuntimeSettings(
        enabled=bool(row.enabled),
        body_mult=float(row.body_mult),
        lookback=int(row.lookback),
        settings_cache_ttl_sec=int(row.settings_cache_ttl_sec),
        updated_by=row.updated_by,
    )


async def update_runtime_settings(
    session: AsyncSession,
    *,
    updated_by: int | None = None,
    enabled: bool | None = None,
    body_mult: float | None = None,
    lookback: int | None = None,
    settings_cache_ttl_sec: int | None = None,
) -> GoldRuntimeSettings:
    row = await get_settings_row(session)
    if row is None:
        row = GoldSettingsRow(id=1)
        session.add(row)
        await session.flush()
    if enabled is not None:
        row.enabled = bool(enabled)
    if body_mult is not None:
        row.body_mult = float(body_mult)
    if lookback is not None:
        row.lookback = int(lookback)
    if settings_cache_ttl_sec is not None:
        row.settings_cache_ttl_sec = int(settings_cache_ttl_sec)
    if updated_by is not None:
        row.updated_by = int(updated_by)
    await session.commit()
    await session.refresh(row)
    return GoldRuntimeSettings(
        enabled=bool(row.enabled),
        body_mult=float(row.body_mult),
        lookback=int(row.lookback),
        settings_cache_ttl_sec=int(row.settings_cache_ttl_sec),
        updated_by=row.updated_by,
    )


async def alert_already_sent(session: AsyncSession, candle_open_time: str) -> bool:
    from sqlalchemy import select

    r = await session.execute(
        select(GoldAlertRow.id).where(GoldAlertRow.candle_open_time == candle_open_time)
    )
    return r.scalar_one_or_none() is not None


async def save_alert(
    session: AsyncSession,
    *,
    candle_open_time: str,
    provider: str,
    body: float,
    avg_body: float,
    ratio: float,
    message: str,
) -> None:
    session.add(
        GoldAlertRow(
            candle_open_time=candle_open_time,
            provider=provider,
            body=body,
            avg_body=avg_body,
            ratio=ratio,
            message=message[:4000],
        )
    )
    await session.commit()
