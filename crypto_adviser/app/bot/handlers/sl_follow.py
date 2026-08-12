"""Автоследование SL на Bybit: мастер включения, список, отключение."""

from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.advisor.tasks import advisor_task_from_row
from app.bot.keyboards import sl_follow_confirm_kb, sl_follow_disable_confirm_kb
from app.bot.states import SlFollowStates
from app.bybit.rest import BybitRest
from app.db.session import session_scope
from app.repository.advisor_tasks import fetch_enabled_advisor_tasks
from app.repository.sl_follow import (
    disable_sl_follow,
    fetch_all_sl_follow,
    get_sl_follow_by_symbol,
    upsert_sl_follow,
)
from app.services.ema_sl_levels import follow_tf_label
from app.services.sl_follow_logic import format_sl_follow_summary

router = Router()


def _pos_side_label(side: str) -> str:
    return "Long" if side == "Buy" else "Short"


async def _open_positions() -> list[tuple[str, str]]:
    """(symbol, side) для linear-позиций."""
    client = BybitRest(category="linear")
    out: list[tuple[str, str]] = []
    for sym in await asyncio.to_thread(client.list_open_linear_symbols):
        snap = await asyncio.to_thread(client.get_linear_position_snapshot, sym)
        if snap:
            out.append((snap.symbol, snap.side))
    return out


def _positions_kb(positions: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{sym} · {_pos_side_label(side)}",
                callback_data=f"sf:pos:{sym}",
            )
        ]
        for sym, side in positions
    ]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="sf:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _tasks_kb(tasks: list) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=(
                    f"#{t.id} EMA{t.ema_fast}/{t.ema_slow} "
                    f"{t.kline_interval}m"
                    + (f" · {t.alias}" if (t.alias or "").strip() else "")
                ),
                callback_data=f"sf:task:{t.id}",
            )
        ]
        for t in tasks
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="sf:back:pos")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _tf_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Базовый ТФ задания", callback_data="sf:tf:base"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Младший ТФ (МТФ)", callback_data="sf:tf:junior"
                )
            ],
            [InlineKeyboardButton(text="« Назад", callback_data="sf:back:task")],
        ]
    )


def _widen_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Нет — только ужесточать SL",
                    callback_data="sf:widen:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Да — можно расширять SL",
                    callback_data="sf:widen:1",
                )
            ],
            [InlineKeyboardButton(text="« Назад", callback_data="sf:back:tf")],
        ]
    )


@router.message(Command("sl_follow"))
async def cmd_sl_follow_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    try:
        positions = await _open_positions()
    except Exception as e:
        await message.answer(f"⚠️ Не удалось загрузить позиции Bybit: {e}")
        return
    if not positions:
        await message.answer(
            "Нет открытых linear-позиций на Bybit.\n"
            "Автоследование SL привязано к реальной позиции."
        )
        return
    await message.answer(
        "<b>Автоследование SL</b>\n"
        "Выберите позицию (символ и сторона с биржи):",
        reply_markup=_positions_kb(positions),
    )


@router.callback_query(F.data.startswith("sf:pos:"))
async def cb_sl_follow_pick_pos(callback: CallbackQuery, state: FSMContext) -> None:
    sym = callback.data.split(":")[-1].upper()
    snap = await asyncio.to_thread(
        BybitRest(category="linear").get_linear_position_snapshot, sym
    )
    if not snap:
        await callback.answer("Позиция уже закрыта", show_alert=True)
        return

    async with session_scope() as session:
        rows = await fetch_enabled_advisor_tasks(session)
    tasks = [r for r in rows if r.symbol.upper() == sym]
    if not tasks:
        await callback.answer(
            "Нет включённого задания EMA для этого тикера", show_alert=True
        )
        return

    await state.update_data(symbol=sym, position_side=snap.side)
    if len(tasks) == 1:
        await state.update_data(advisor_task_id=tasks[0].id)
        await callback.message.edit_text(
            f"<code>{sym}</code> · {_pos_side_label(snap.side)}\n\n"
            "По какому ТФ переставлять SL при закрытии свечи?",
            reply_markup=_tf_kb(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"<code>{sym}</code> · {_pos_side_label(snap.side)}\n\n"
        "Выберите задание EMA (расчёт SL):",
        reply_markup=_tasks_kb(tasks),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sf:task:"))
async def cb_sl_follow_pick_task(callback: CallbackQuery, state: FSMContext) -> None:
    tid = int(callback.data.split(":")[-1])
    await state.update_data(advisor_task_id=tid)
    data = await state.get_data()
    sym = data.get("symbol", "")
    await callback.message.edit_text(
        f"<code>{sym}</code>\n\nПо какому ТФ переставлять SL при закрытии свечи?",
        reply_markup=_tf_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sf:tf:"))
async def cb_sl_follow_pick_tf(callback: CallbackQuery, state: FSMContext) -> None:
    mode = callback.data.split(":")[-1]
    if mode not in ("base", "junior"):
        await callback.answer("Некорректный ТФ", show_alert=True)
        return
    await state.update_data(sl_tf_mode=mode)
    await callback.message.edit_text(
        "<b>Увеличение SL</b>\n\n"
        "Если расчётный SL <b>дальше</b> от цены, чем текущий на Bybit "
        "(больше риск в $), переносить?\n\n"
        "Текущий SL всегда читается с биржи (в т.ч. после ручной правки).",
        reply_markup=_widen_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sf:widen:"))
async def cb_sl_follow_pick_widen(callback: CallbackQuery, state: FSMContext) -> None:
    allow = callback.data.endswith(":1")
    await state.update_data(allow_sl_widen=allow)
    data = await state.get_data()
    sym = data["symbol"]
    tid = data["advisor_task_id"]
    mode = data["sl_tf_mode"]

    async with session_scope() as session:
        from app.repository.advisor_tasks import get_advisor_task

        row = await get_advisor_task(session, tid)
    if not row:
        await callback.answer("Задание не найдено", show_alert=True)
        await state.clear()
        return

    task = advisor_task_from_row(row)
    tf_label = follow_tf_label(task, mode)  # type: ignore[arg-type]
    alias = (row.alias or "").strip()
    task_line = (
        f"{alias} ({row.symbol})" if alias else row.symbol
    ) + f" · EMA {row.ema_fast}/{row.ema_slow} · #{tid}"

    summary = format_sl_follow_summary(
        sym,
        data["position_side"],
        tf_label,
        task_line,
        allow,
    )
    await state.set_state(SlFollowStates.confirm_enable)
    await callback.message.edit_text(
        "⚠️ <b>Вы уверены, что хотите включить автоследование SL?</b>\n\n"
        + summary,
        reply_markup=sl_follow_confirm_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "sf:confirm:yes", SlFollowStates.confirm_enable)
async def cb_sl_follow_confirm_enable(
    callback: CallbackQuery, state: FSMContext
) -> None:
    data = await state.get_data()
    required = ("symbol", "position_side", "advisor_task_id", "sl_tf_mode", "allow_sl_widen")
    if not all(k in data for k in required):
        await callback.answer("Данные мастера потеряны", show_alert=True)
        await state.clear()
        return

    sym = data["symbol"].upper()
    async with session_scope() as session:
        await upsert_sl_follow(
            session,
            symbol=sym,
            position_side=data["position_side"],
            advisor_task_id=int(data["advisor_task_id"]),
            sl_tf_mode=data["sl_tf_mode"],
            allow_sl_widen=bool(data["allow_sl_widen"]),
        )

    await state.clear()
    widen = "да" if data["allow_sl_widen"] else "нет"
    await callback.message.edit_text(
        f"✅ Автоследование SL включено для <code>{sym}</code>.\n"
        f"ТФ: <b>{data['sl_tf_mode']}</b> · расширение SL: <b>{widen}</b>\n\n"
        "Отчёты о переносах — в топик SL follow.\n"
        "Выключить: <code>/sl_follow_stop {sym}</code> или <code>/sl_follow_list</code>",
    )
    await callback.answer("Включено")

    from app.services.admin_notify import notify_sl_follow_channel

    await notify_sl_follow_channel(
        f"✅ Включено автоследование SL · <code>{sym}</code> · "
        f"{_pos_side_label(data['position_side'])} · ТФ {data['sl_tf_mode']} · "
        f"расширение: {widen}"
    )


@router.message(Command("sl_follow_list"))
async def cmd_sl_follow_list(message: Message) -> None:
    async with session_scope() as session:
        rows = await fetch_all_sl_follow(session)

    if not rows:
        await message.answer(
            "Автоследование SL не настроено.\n<code>/sl_follow</code> — включить."
        )
        return

    lines: list[str] = []
    for r in rows:
        st = "🟢" if r.enabled else "⚪️"
        widen = "расширение да" if r.allow_sl_widen else "только ужесточение"
        tf = "базовый" if r.sl_tf_mode == "base" else "младший"
        lines.append(
            f"{st} <code>{r.symbol}</code> · {_pos_side_label(r.position_side)} · "
            f"ТФ {tf} · #{r.advisor_task_id} · {widen}"
        )

    await message.answer(
        "<b>Автоследование SL</b>\n\n"
        + "\n".join(lines)
        + "\n\n<code>/sl_follow_stop SYMBOL</code> — выключить",
    )


@router.message(Command("sl_follow_stop"))
async def cmd_sl_follow_stop(message: Message, state: FSMContext) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        async with session_scope() as session:
            rows = await fetch_all_sl_follow(session)
        enabled = [r for r in rows if r.enabled]
        if not enabled:
            await message.answer("Укажите символ: <code>/sl_follow_stop ZECUSDT</code>")
            return
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=r.symbol,
                        callback_data=f"sf:off_ask:{r.symbol}",
                    )
                ]
                for r in enabled
            ]
            + [[InlineKeyboardButton(text="❌ Отмена", callback_data="sf:cancel")]]
        )
        await message.answer("Выберите, для чего выключить SL follow:", reply_markup=kb)
        return

    sym = parts[1].upper().replace("/", "")
    async with session_scope() as session:
        row = await get_sl_follow_by_symbol(session, sym)
    if not row or not row.enabled:
        await message.answer(f"Для <code>{sym}</code> автоследование не активно.")
        return

    await state.set_state(SlFollowStates.confirm_disable)
    await state.update_data(disable_symbol=sym)
    await message.answer(
        f"⚠️ <b>Вы уверены, что хотите выключить автоследование SL?</b>\n\n"
        f"<code>{sym}</code> · {_pos_side_label(row.position_side)}",
        reply_markup=sl_follow_disable_confirm_kb(sym),
    )


@router.callback_query(F.data.startswith("sf:off_ask:"))
async def cb_sl_follow_off_ask(callback: CallbackQuery, state: FSMContext) -> None:
    sym = callback.data.split(":")[-1].upper()
    async with session_scope() as session:
        row = await get_sl_follow_by_symbol(session, sym)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    await state.set_state(SlFollowStates.confirm_disable)
    await state.update_data(disable_symbol=sym)
    await callback.message.edit_text(
        f"⚠️ <b>Вы уверены, что хотите выключить автоследование SL?</b>\n\n"
        f"<code>{sym}</code>",
        reply_markup=sl_follow_disable_confirm_kb(sym),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sf:off_ok:"))
async def cb_sl_follow_off_ok(callback: CallbackQuery, state: FSMContext) -> None:
    sym = callback.data.split(":")[-1].upper()
    async with session_scope() as session:
        ok = await disable_sl_follow(session, sym)
    await state.clear()
    if not ok:
        await callback.answer("Уже выключено", show_alert=True)
        return
    await callback.message.edit_text(f"⏹ Автоследование SL выключено для <code>{sym}</code>.")
    await callback.answer("Выключено")

    from app.services.admin_notify import notify_sl_follow_channel

    await notify_sl_follow_channel(f"⏹ Выключено автоследование SL · <code>{sym}</code>")


@router.callback_query(F.data == "sf:cancel")
async def cb_sl_follow_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.answer()


@router.callback_query(F.data == "sf:back:pos")
async def cb_sl_follow_back_pos(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    positions = await _open_positions()
    if not positions:
        await callback.message.edit_text("Нет открытых позиций.")
        return
    await callback.message.edit_text(
        "Выберите позицию:",
        reply_markup=_positions_kb(positions),
    )
    await callback.answer()


@router.callback_query(F.data == "sf:back:task")
async def cb_sl_follow_back_task(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    sym = data.get("symbol", "").upper()
    async with session_scope() as session:
        rows = await fetch_enabled_advisor_tasks(session)
    tasks = [r for r in rows if r.symbol.upper() == sym]
    if len(tasks) <= 1:
        await callback.message.edit_text(
            f"<code>{sym}</code>\n\nПо какому ТФ переставлять SL?",
            reply_markup=_tf_kb(),
        )
    else:
        await callback.message.edit_text(
            f"<code>{sym}</code>\n\nВыберите задание EMA:",
            reply_markup=_tasks_kb(tasks),
        )
    await callback.answer()


@router.callback_query(F.data == "sf:back:tf")
async def cb_sl_follow_back_tf(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        "По какому ТФ переставлять SL при закрытии свечи?",
        reply_markup=_tf_kb(),
    )
    await callback.answer()
