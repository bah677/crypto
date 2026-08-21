"""Telegram: EMA-будильник (пересечение цены и EMA)."""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.pump_dm import answer_pump_callback_continue
from app.bybit.rest import BybitRest
from app.config import get_settings
from app.db.session import session_scope
from app.pump_scan.ema_levels import (
    ALL_EMA_KEYS,
    ema_price_from_map,
    fetch_ema_map_sync,
    price_side,
)
from app.pump_scan.weekly_ema import format_ema_entry_label
from app.repository.pump_ema_alarms import (
    create_pump_ema_alarm,
    fetch_user_pump_ema_alarms,
    set_pump_ema_alarm_active,
)

log = logging.getLogger(__name__)
router = Router()

_DIR_LABELS = {
    "up": "⬆️ Снизу вверх",
    "down": "⬇️ Сверху вниз",
    "both": "↕️ Оба",
}


def _alarm_ema_kb(symbol: str, ema_map: dict[str, float | None]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for key in ALL_EMA_KEYS:
        price = ema_map.get(key)
        if price is None:
            continue
        lbl = format_ema_entry_label(key)
        row.append(
            InlineKeyboardButton(
                text=f"{lbl}",
                callback_data=f"pump:alarm:ema:{symbol}:{key}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="pump:alarm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _alarm_dir_kb(symbol: str, ema_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_DIR_LABELS["down"],
                    callback_data=f"pump:alarm:dir:{symbol}:{ema_key}:down",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_DIR_LABELS["up"],
                    callback_data=f"pump:alarm:dir:{symbol}:{ema_key}:up",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_DIR_LABELS["both"],
                    callback_data=f"pump:alarm:dir:{symbol}:{ema_key}:both",
                ),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="pump:alarm:cancel")],
        ]
    )


def _alarms_list_kb(alarms) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for a in alarms:
        if not a.active:
            continue
        ema_lbl = format_ema_entry_label(a.ema_key)
        dir_lbl = _DIR_LABELS.get(a.direction, a.direction)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔕 #{a.id} {a.symbol} {ema_lbl}",
                    callback_data=f"pump:alarm:off:{a.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="« Закрыть", callback_data="pump:alarm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("pump:alarm:start:"))
async def alarm_start(cb: CallbackQuery) -> None:
    if not cb.from_user or not cb.data:
        await cb.answer()
        return
    if not get_settings().pump_ema_alarm_enabled:
        await cb.answer("Будильники выключены", show_alert=True)
        return
    symbol = cb.data.split(":", 3)[3].upper()
    ema_map = await asyncio.to_thread(
        fetch_ema_map_sync, BybitRest(category="linear"), symbol
    )
    if not any(v is not None for v in ema_map.values()):
        await cb.answer("Нет EMA для монеты", show_alert=True)
        return
    await answer_pump_callback_continue(
        cb,
        text=(
            f"<b>🔔 EMA будильник</b> · <code>{symbol}</code>\n\n"
            "Выберите EMA для отслеживания пересечения цены:"
        ),
        reply_markup=_alarm_ema_kb(symbol, ema_map),
    )


@router.callback_query(F.data.startswith("pump:alarm:ema:"))
async def alarm_pick_ema(cb: CallbackQuery) -> None:
    if not cb.data:
        await cb.answer()
        return
    parts = cb.data.split(":")
    if len(parts) < 5:
        await cb.answer("Ошибка данных")
        return
    symbol = parts[3].upper()
    ema_key = parts[4].upper()
    if ema_key not in ALL_EMA_KEYS:
        await cb.answer("Неверный EMA", show_alert=True)
        return
    ema_lbl = format_ema_entry_label(ema_key)
    await cb.answer()
    await cb.message.edit_text(
        f"<b>🔔 {symbol}</b> · {ema_lbl}\n\n"
        "Какое пересечение отслеживать?",
        reply_markup=_alarm_dir_kb(symbol, ema_key),
    )


@router.callback_query(F.data.startswith("pump:alarm:dir:"))
async def alarm_pick_dir(cb: CallbackQuery) -> None:
    if not cb.from_user or not cb.message or not cb.data:
        await cb.answer()
        return
    parts = cb.data.split(":")
    if len(parts) < 6:
        await cb.answer("Ошибка данных")
        return
    symbol = parts[3].upper()
    ema_key = parts[4].upper()
    direction = parts[5].lower()
    if direction not in ("up", "down", "both"):
        await cb.answer("Неверное направление", show_alert=True)
        return

    ema_map = await asyncio.to_thread(
        fetch_ema_map_sync, BybitRest(category="linear"), symbol
    )
    ema_val = ema_price_from_map(ema_map, ema_key)
    if ema_val is None:
        await cb.answer("EMA недоступна", show_alert=True)
        return

    price = await asyncio.to_thread(BybitRest(category="linear").last_price, symbol)
    if not price or price <= 0:
        await cb.answer("Нет цены", show_alert=True)
        return

    side = price_side(float(price), ema_val)
    async with session_scope() as session:
        row = await create_pump_ema_alarm(
            session,
            telegram_user_id=cb.from_user.id,
            telegram_chat_id=cb.message.chat.id,
            symbol=symbol,
            ema_key=ema_key,
            direction=direction,
            last_side=side,
            last_ema_value=ema_val,
        )
        await session.commit()
        alarm_id = row.id

    ema_lbl = format_ema_entry_label(ema_key)
    dir_lbl = _DIR_LABELS.get(direction, direction)
    side_lbl = "выше" if side == "above" else "ниже"
    await cb.answer("Будильник включён")
    await cb.message.edit_text(
        f"✅ <b>Будильник #{alarm_id}</b> · <code>{symbol}</code>\n"
        f"EMA: <b>{ema_lbl}</b> = <code>{ema_val:.5g}</code>\n"
        f"Пересечение: {dir_lbl}\n"
        f"Сейчас цена <code>{float(price):.5g}</code> — {side_lbl} EMA\n\n"
        f"Проверка каждые {get_settings().pump_ema_alarm_interval_sec} с.\n"
        "Список и отключение: /pump_alarms",
        reply_markup=None,
    )


@router.callback_query(F.data.startswith("pump:alarm:off:"))
async def alarm_off_cb(cb: CallbackQuery) -> None:
    if not cb.from_user or not cb.data or not cb.message:
        await cb.answer()
        return
    try:
        alarm_id = int(cb.data.rsplit(":", 1)[-1])
    except ValueError:
        await cb.answer("Ошибка id")
        return
    async with session_scope() as session:
        ok = await set_pump_ema_alarm_active(
            session, alarm_id, active=False, user_id=cb.from_user.id
        )
        await session.commit()
    if not ok:
        await cb.answer("Не найдено", show_alert=True)
        return
    await cb.answer("Выключено")
    async with session_scope() as session:
        alarms = await fetch_user_pump_ema_alarms(session, cb.from_user.id, active_only=True)
    if not alarms:
        await cb.message.edit_text("🔔 Активных EMA-будильников больше нет.")
        return
    lines = ["<b>🔔 EMA-будильники</b>", ""]
    for a in alarms:
        ema_lbl = format_ema_entry_label(a.ema_key)
        dir_lbl = _DIR_LABELS.get(a.direction, a.direction)
        side = a.last_side or "—"
        lines.append(
            f"#{a.id} <code>{a.symbol}</code> · {ema_lbl} · {dir_lbl} · "
            f"цена {side} EMA"
        )
    lines.append("")
    lines.append("Нажмите 🔕 чтобы выключить:")
    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=_alarms_list_kb(alarms),
    )


@router.callback_query(F.data == "pump:alarm:cancel")
async def alarm_cancel(cb: CallbackQuery) -> None:
    await cb.answer("Отменено")
    if cb.message:
        await cb.message.edit_reply_markup(reply_markup=None)


@router.message(Command("pump_alarms"))
async def cmd_pump_alarms(message: Message) -> None:
    if not message.from_user:
        return
    uid = message.from_user.id
    async with session_scope() as session:
        alarms = await fetch_user_pump_ema_alarms(session, uid, active_only=True)
    if not alarms:
        await message.answer(
            "🔔 Активных EMA-будильников нет.\n"
            "Создайте из pump-алерта: кнопка «🔔 EMA будильник»."
        )
        return
    lines = ["<b>🔔 EMA-будильники</b>", ""]
    for a in alarms:
        ema_lbl = format_ema_entry_label(a.ema_key)
        dir_lbl = _DIR_LABELS.get(a.direction, a.direction)
        side = a.last_side or "—"
        lines.append(
            f"#{a.id} <code>{a.symbol}</code> · {ema_lbl} · {dir_lbl} · "
            f"цена {side} EMA"
        )
    lines.append("")
    lines.append("Нажмите 🔕 чтобы выключить:")
    await message.answer(
        "\n".join(lines),
        reply_markup=_alarms_list_kb(alarms),
    )
