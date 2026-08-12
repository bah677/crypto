from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PumpEmaAlarmRow, SubscriberRow, UserActivityDayRow


async def get_subscriber(session: AsyncSession, telegram_user_id: int) -> SubscriberRow | None:
    return await session.get(SubscriberRow, telegram_user_id)


async def upsert_subscriber_touch(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    username: str = "",
    first_name: str = "",
) -> SubscriberRow:
    now = datetime.now(timezone.utc)
    row = await session.get(SubscriberRow, telegram_user_id)
    if row is None:
        row = SubscriberRow(
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            username=username or "",
            first_name=first_name or "",
            subscribed=False,
            banned=False,
            last_seen_at=now,
        )
        session.add(row)
    else:
        row.telegram_chat_id = telegram_chat_id
        if username:
            row.username = username
        if first_name:
            row.first_name = first_name
        row.last_seen_at = now
    await _touch_activity_day(session, telegram_user_id, now)
    await session.flush()
    return row


async def _touch_activity_day(session: AsyncSession, user_id: int, now: datetime) -> None:
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    r = await session.execute(
        select(UserActivityDayRow.id).where(
            UserActivityDayRow.telegram_user_id == user_id,
            UserActivityDayRow.activity_date == day_start,
        )
    )
    if r.scalar_one_or_none() is None:
        session.add(
            UserActivityDayRow(
                telegram_user_id=user_id,
                activity_date=day_start,
            )
        )


async def set_subscribed(
    session: AsyncSession, telegram_user_id: int, *, subscribed: bool
) -> SubscriberRow | None:
    row = await session.get(SubscriberRow, telegram_user_id)
    if row is None or row.banned:
        return None
    now = datetime.now(timezone.utc)
    row.subscribed = subscribed
    if subscribed:
        row.subscribed_at = now
        row.unsubscribed_at = None
    else:
        row.unsubscribed_at = now
    await session.flush()
    return row


async def ban_subscriber(
    session: AsyncSession,
    telegram_user_id: int,
    *,
    banned_by: int,
    reason: str = "",
) -> bool:
    row = await session.get(SubscriberRow, telegram_user_id)
    if row is None:
        return False
    now = datetime.now(timezone.utc)
    row.banned = True
    row.banned_at = now
    row.banned_by = banned_by
    row.ban_reason = (reason or "")[:256]
    row.subscribed = False
    row.unsubscribed_at = now
    await session.flush()
    return True


async def unban_subscriber(session: AsyncSession, telegram_user_id: int) -> bool:
    row = await session.get(SubscriberRow, telegram_user_id)
    if row is None:
        return False
    row.banned = False
    row.banned_at = None
    row.banned_by = None
    row.ban_reason = ""
    await session.flush()
    return True


async def is_subscriber_banned(session: AsyncSession, telegram_user_id: int) -> bool:
    row = await session.get(SubscriberRow, telegram_user_id)
    return bool(row and row.banned)


async def fetch_active_subscribers(session: AsyncSession) -> list[SubscriberRow]:
    r = await session.execute(
        select(SubscriberRow)
        .where(SubscriberRow.subscribed.is_(True), SubscriberRow.banned.is_(False))
        .order_by(SubscriberRow.telegram_user_id)
    )
    return list(r.scalars().all())


async def list_all_subscribers(session: AsyncSession) -> list[SubscriberRow]:
    """Все пользователи: подписанные первыми, затем по последней активности."""
    r = await session.execute(
        select(SubscriberRow).order_by(
            SubscriberRow.banned.asc(),
            SubscriberRow.subscribed.desc(),
            SubscriberRow.last_seen_at.desc().nullslast(),
            SubscriberRow.created_at.desc(),
        )
    )
    return list(r.scalars().all())


async def count_subscribers(session: AsyncSession) -> dict[str, int]:
    total = await session.scalar(select(func.count()).select_from(SubscriberRow)) or 0
    active = await session.scalar(
        select(func.count())
        .select_from(SubscriberRow)
        .where(SubscriberRow.subscribed.is_(True), SubscriberRow.banned.is_(False))
    ) or 0
    banned = await session.scalar(
        select(func.count()).select_from(SubscriberRow).where(SubscriberRow.banned.is_(True))
    ) or 0
    alarms = await session.scalar(
        select(func.count())
        .select_from(PumpEmaAlarmRow)
        .where(PumpEmaAlarmRow.active.is_(True))
    ) or 0
    return {
        "total_users": int(total),
        "active_subscribers": int(active),
        "banned_users": int(banned),
        "active_alarms": int(alarms),
    }


async def count_dau_mau(session: AsyncSession) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    month_start = today_start - timedelta(days=29)
    dau = await session.scalar(
        select(func.count(func.distinct(UserActivityDayRow.telegram_user_id))).where(
            UserActivityDayRow.activity_date == today_start
        )
    ) or 0
    mau = await session.scalar(
        select(func.count(func.distinct(UserActivityDayRow.telegram_user_id))).where(
            UserActivityDayRow.activity_date >= month_start
        )
    ) or 0
    return int(dau), int(mau)
