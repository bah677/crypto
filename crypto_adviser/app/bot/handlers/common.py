from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.handlers.subscription import _subscribe_kb
from app.bot.help_text import (
    build_admin_welcome_text,
    build_help_parts,
    build_status_text,
    build_user_welcome_text,
)
from app.config import get_settings
from app.db.session import session_scope
from app.repository.admins import add_admin, is_telegram_admin, list_admins, remove_admin
from app.repository.subscribers import get_subscriber, set_subscribed
from app.bot.admin_guard import invalidate_admin_cache, is_admin_user

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not message.from_user:
        return
    async with session_scope() as session:
        row = await set_subscribed(session, message.from_user.id, subscribed=True)
        await session.commit()
    subscribed = bool(row and row.subscribed)
    is_admin = await is_admin_user(message.from_user.id)

    if is_admin:
        text = build_admin_welcome_text(subscribed=subscribed)
    else:
        text = build_user_welcome_text(
            first_name=message.from_user.first_name or "",
        )

    await message.answer(
        text,
        reply_markup=_subscribe_kb(subscribed=subscribed),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    s = get_settings()
    is_admin = await is_admin_user(message.from_user.id) if message.from_user else False
    for part in build_help_parts(s, is_admin=is_admin):
        await message.answer(part)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    s = get_settings()
    subscribed = False
    if message.from_user:
        async with session_scope() as session:
            row = await get_subscriber(session, message.from_user.id)
            subscribed = bool(row and row.subscribed)
    is_admin = await is_admin_user(message.from_user.id) if message.from_user else False
    await message.answer(build_status_text(s, subscribed=subscribed, is_admin=is_admin))


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    s = get_settings()
    is_admin = await is_admin_user(uid)
    async with session_scope() as session:
        in_admins = await is_telegram_admin(session, uid)
        sub = await get_subscriber(session, uid)
    sub_txt = "нет в БД"
    if sub:
        if sub.banned:
            sub_txt = "⛔ забанен"
        elif sub.subscribed:
            sub_txt = "✅ подписан"
        else:
            sub_txt = "🔕 не подписан"
    await message.answer(
        f"Ваш id: <code>{uid}</code>\n"
        f"Подписка: <b>{sub_txt}</b>\n"
        f"Админ: <b>{'да' if is_admin else 'нет'}</b> "
        f"(таблица admins: {'да' if in_admins else 'нет'})\n"
        f"Супер-админ .env: <code>{s.superadmin_telegram_id}</code>"
    )


@router.message(Command("admins"))
async def cmd_admins(message: Message) -> None:
    if not await is_admin_user(message.from_user.id if message.from_user else 0):
        await message.answer("⛔ Только для админов.")
        return
    async with session_scope() as session:
        rows = await list_admins(session)
    if not rows:
        await message.answer("Таблица <code>admins</code> пуста.")
        return
    lines = ["<b>Администраторы</b>", ""]
    for row in rows:
        note = f" — {row.note}" if row.note else ""
        lines.append(f"• <code>{row.telegram_user_id}</code>{note}")
    await message.answer("\n".join(lines))


@router.message(Command("admin_add"))
async def cmd_admin_add(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    s = get_settings()
    if uid != s.superadmin_telegram_id:
        await message.answer("⛔ Только супер-админ из .env.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Формат: <code>/admin_add USER_ID</code>")
        return
    target = int(parts[1].strip())
    async with session_scope() as session:
        await add_admin(session, target)
    invalidate_admin_cache(target)
    await message.answer(f"✅ Админ добавлен: <code>{target}</code>")


@router.message(Command("admin_del"))
async def cmd_admin_del(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    s = get_settings()
    if uid != s.superadmin_telegram_id:
        await message.answer("⛔ Только супер-админ из .env.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Формат: <code>/admin_del USER_ID</code>")
        return
    target = int(parts[1].strip())
    if target == s.superadmin_telegram_id:
        await message.answer("Нельзя удалить супер-админа.")
        return
    async with session_scope() as session:
        removed = await remove_admin(session, target)
    invalidate_admin_cache(target)
    if removed:
        await message.answer(f"✅ Удалён: <code>{target}</code>")
    else:
        await message.answer(f"Нет в admins: <code>{target}</code>")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")
