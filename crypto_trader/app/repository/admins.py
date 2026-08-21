from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminRow


async def is_telegram_admin(session: AsyncSession, telegram_user_id: int) -> bool:
    r = await session.execute(
        select(AdminRow.telegram_user_id).where(
            AdminRow.telegram_user_id == telegram_user_id
        )
    )
    return r.scalar_one_or_none() is not None


async def list_admins(session: AsyncSession) -> list[AdminRow]:
    r = await session.execute(
        select(AdminRow).order_by(AdminRow.created_at.asc())
    )
    return list(r.scalars().all())


async def add_admin(
    session: AsyncSession,
    telegram_user_id: int,
    *,
    note: str = "",
) -> AdminRow:
    row = await session.get(AdminRow, telegram_user_id)
    if row is None:
        row = AdminRow(telegram_user_id=telegram_user_id, note=note or "")
        session.add(row)
    elif note:
        row.note = note
    await session.commit()
    await session.refresh(row)
    return row


async def remove_admin(session: AsyncSession, telegram_user_id: int) -> bool:
    row = await session.get(AdminRow, telegram_user_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True
