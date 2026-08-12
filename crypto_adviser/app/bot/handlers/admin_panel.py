"""Админ: бан пользователей и аналитика DAU/MAU."""

from __future__ import annotations

import html

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.admin_guard import is_admin_user
from app.config import get_settings
from app.db.models import SubscriberRow
from app.db.session import session_scope
from app.repository.subscribers import (
    ban_subscriber,
    count_dau_mau,
    count_subscribers,
    get_subscriber,
    list_all_subscribers,
    unban_subscriber,
)

router = Router()

_USERS_CHUNK_MAX = 3500


def _subscriber_status_label(row: SubscriberRow) -> str:
    if row.banned:
        return "⛔ бан"
    if row.subscribed:
        return "✅ подписан"
    return "🔕 отписан"


def _format_subscriber_line(row: SubscriberRow) -> str:
    name = html.escape((row.first_name or "").strip() or "—")
    username = f" @{html.escape(row.username)}" if (row.username or "").strip() else ""
    return f"{_subscriber_status_label(row)} <code>{row.telegram_user_id}</code> — {name}{username}"


def _build_users_report(rows: list[SubscriberRow]) -> list[str]:
    subscribed = sum(1 for r in rows if r.subscribed and not r.banned)
    unsubscribed = sum(1 for r in rows if not r.subscribed and not r.banned)
    banned = sum(1 for r in rows if r.banned)
    header = (
        "<b>👥 Пользователи</b>\n\n"
        f"✅ подписано: <b>{subscribed}</b> · "
        f"🔕 отписано: <b>{unsubscribed}</b> · "
        f"⛔ бан: <b>{banned}</b> · "
        f"всего: <b>{len(rows)}</b>\n"
    )
    if not rows:
        return [header + "\nПока никого нет — появятся после /start."]

    parts: list[str] = []
    current = header
    for row in rows:
        line = _format_subscriber_line(row) + "\n"
        if len(current) + len(line) > _USERS_CHUNK_MAX and current != header:
            parts.append(current.rstrip())
            current = line
        else:
            current += line
    parts.append(current.rstrip())
    return parts


async def _require_admin(message: Message) -> bool:
    uid = message.from_user.id if message.from_user else 0
    if not await is_admin_user(uid):
        await message.answer("⛔ Команда только для администраторов.")
        return False
    return True


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not await _require_admin(message):
        return
    async with session_scope() as session:
        counts = await count_subscribers(session)
        dau, mau = await count_dau_mau(session)
    await message.answer(
        "<b>📊 Аналитика EMA-подписка</b>\n\n"
        f"Активных подписчиков: <b>{counts['active_subscribers']}</b>\n"
        f"Всего пользователей: <b>{counts['total_users']}</b>\n"
        f"Забанено: <b>{counts['banned_users']}</b>\n"
        f"Активных EMA-будильников: <b>{counts['active_alarms']}</b>\n\n"
        f"DAU (сегодня UTC): <b>{dau}</b>\n"
        f"MAU (30 дней): <b>{mau}</b>"
    )


@router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    if not await _require_admin(message):
        return
    async with session_scope() as session:
        rows = await list_all_subscribers(session)
    for part in _build_users_report(rows):
        await message.answer(part)


@router.message(Command("ban"))
async def cmd_ban(message: Message) -> None:
    if not await _require_admin(message):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>/ban TELEGRAM_USER_ID [причина]</code>")
        return
    target = int(parts[1])
    reason = parts[2].strip() if len(parts) > 2 else ""
    s = get_settings()
    if target == s.superadmin_telegram_id:
        await message.answer("Нельзя забанить супер-админа.")
        return
    admin_id = message.from_user.id if message.from_user else 0
    async with session_scope() as session:
        ok = await ban_subscriber(session, target, banned_by=admin_id, reason=reason)
        await session.commit()
    if not ok:
        await message.answer(f"Пользователь <code>{target}</code> не найден. Он появится после /start.")
        return
    await message.answer(
        f"⛔ Забанен: <code>{target}</code>"
        + (f"\nПричина: {reason}" if reason else "")
    )


@router.message(Command("unban"))
async def cmd_unban(message: Message) -> None:
    if not await _require_admin(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>/unban TELEGRAM_USER_ID</code>")
        return
    target = int(parts[1])
    async with session_scope() as session:
        ok = await unban_subscriber(session, target)
        await session.commit()
    if ok:
        await message.answer(f"✅ Разбанен: <code>{target}</code>")
    else:
        await message.answer(f"Нет пользователя <code>{target}</code>.")


@router.message(Command("user"))
async def cmd_user_info(message: Message) -> None:
    if not await _require_admin(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>/user TELEGRAM_USER_ID</code>")
        return
    target = int(parts[1])
    async with session_scope() as session:
        row = await get_subscriber(session, target)
    if row is None:
        await message.answer(f"Пользователь <code>{target}</code> не в БД.")
        return
    await message.answer(
        f"<b>Пользователь</b> <code>{target}</code>\n"
        f"Имя: {row.first_name or '—'} @{row.username or '—'}\n"
        f"Подписка: <b>{'да' if row.subscribed else 'нет'}</b>\n"
        f"Бан: <b>{'да' if row.banned else 'нет'}</b>\n"
        f"Создан: {row.created_at}\n"
        f"Последняя активность: {row.last_seen_at or '—'}"
        + (f"\nПричина бана: {row.ban_reason}" if row.ban_reason else "")
    )
