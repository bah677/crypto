"""Telegram: подписка / отписка."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db.session import session_scope
from app.repository.subscribers import fetch_active_subscribers, set_subscribed

router = Router()


def _subscribe_kb(*, subscribed: bool) -> InlineKeyboardMarkup:
    if subscribed:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔕 Отписаться от алертов",
                        callback_data="sub:off",
                    )
                ],
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔔 Подписаться на алерты",
                    callback_data="sub:on",
                )
            ],
        ]
    )


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message) -> None:
    if not message.from_user:
        return
    async with session_scope() as session:
        row = await set_subscribed(session, message.from_user.id, subscribed=True)
        await session.commit()
    if row is None:
        await message.answer("⛔ Подписка недоступна (бан или ошибка). /start")
        return
    await message.answer(
        "✅ Вы подписаны на <b>Pump-in-Downtrend</b> алерты.\n"
        "Графики, EMA и мнение ИИ приходят сюда при каждом сигнале.\n\n"
        "Отписаться: /unsubscribe",
        reply_markup=_subscribe_kb(subscribed=True),
    )


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message) -> None:
    if not message.from_user:
        return
    async with session_scope() as session:
        await set_subscribed(session, message.from_user.id, subscribed=False)
        await session.commit()
    await message.answer(
        "🔕 Подписка отключена — pump-алерты больше не приходят.\n"
        "Ваши EMA-будильники продолжают работать.\n\n"
        "Вернуться: /subscribe",
        reply_markup=_subscribe_kb(subscribed=False),
    )


@router.callback_query(F.data == "sub:on")
async def cb_sub_on(cb: CallbackQuery) -> None:
    if not cb.from_user:
        await cb.answer()
        return
    async with session_scope() as session:
        row = await set_subscribed(session, cb.from_user.id, subscribed=True)
        await session.commit()
    if row is None:
        await cb.answer("⛔ Недоступно", show_alert=True)
        return
    await cb.answer("Подписка включена")
    if cb.message:
        await cb.message.edit_text(
            "✅ <b>Подписка активна</b>\n\n"
            "Алерты pump + EMA + мнение ИИ будут приходить в этот чат.",
            reply_markup=_subscribe_kb(subscribed=True),
        )


@router.callback_query(F.data == "sub:off")
async def cb_sub_off(cb: CallbackQuery) -> None:
    if not cb.from_user:
        await cb.answer()
        return
    async with session_scope() as session:
        await set_subscribed(session, cb.from_user.id, subscribed=False)
        await session.commit()
    await cb.answer("Отписаны")
    if cb.message:
        await cb.message.edit_text(
            "🔕 <b>Подписка отключена</b>\n\n"
            "Pump-алерты не приходят. EMA-будильники — по-прежнему ваши.",
            reply_markup=_subscribe_kb(subscribed=False),
        )
