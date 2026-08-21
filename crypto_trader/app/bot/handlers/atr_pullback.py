"""Telegram: ATR Pullback — создание и управление заданиями."""

from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.atr_pullback.intervals import (
    interval_label,
    lower_intervals_for_btf,
    validate_btf_mtf,
)
from app.atr_pullback.tasks import atr_pullback_task_from_row
from app.bot.handlers.tasks import _parse_trading_hours
from app.bot.keyboards import back_menu_kb, cancel_kb
from app.bot.states import CreateAtrPullbackStates
from app.config import get_settings
from app.db.session import session_scope
from app.repository.atr_pullback import (
    add_atr_pullback_task,
    delete_atr_pullback_task,
    fetch_all_atr_pullback_tasks,
    find_atr_pullback_task_by_key,
    get_atr_pullback_task,
    set_atr_pullback_enabled,
)

router = Router()


def _parse_ema_pair(text: str) -> tuple[int, int]:
    parts = text.strip().split()
    if len(parts) != 2:
        raise ValueError("Два числа через пробел: быстрая и медленная EMA.\nПример: 12 26")
    fast, slow = int(parts[0]), int(parts[1])
    if fast <= 0 or slow <= 0:
        raise ValueError("EMA должны быть > 0")
    if fast == slow:
        raise ValueError("EMA быстрая и медленная не должны совпадать")
    return fast, slow


def _parse_alias(raw: str) -> str:
    text = raw.strip()
    if text == "-":
        return ""
    if len(text) > 64:
        raise ValueError("Псевдоним не длиннее 64 символов")
    return text


def _btf_kb() -> InlineKeyboardMarkup:
    from app.atr_pullback.intervals import allowed_btf_intervals

    rows = [
        [InlineKeyboardButton(text=f"{interval_label(iv)}", callback_data=f"ap:btf:{iv}")]
        for iv in allowed_btf_intervals()
    ]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="task:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _mtf_kb(btf: str) -> InlineKeyboardMarkup:
    lowers = lower_intervals_for_btf(btf)
    rows = [
        [InlineKeyboardButton(text=f"{interval_label(iv)}", callback_data=f"ap:mtf:{iv}")]
        for iv in lowers
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="ap:back:btf")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _yes_no_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=f"{prefix}:yes"),
                InlineKeyboardButton(text="Нет", callback_data=f"{prefix}:no"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="task:cancel")],
        ]
    )


def _confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Создать", callback_data="ap:confirm:yes"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="task:cancel"),
            ]
        ]
    )


def _task_card(row) -> str:
    from app.trading_schedule import format_schedule_label

    t = atr_pullback_task_from_row(row)
    wh = format_schedule_label(t.trading_hours).replace("; ", "\n")
    alias = f"Псевдоним: {t.alias}\n" if t.alias else ""
    auto = (
        f"Автоторговля: да · ${t.position_usd:.0f} · {t.leverage}x\n"
        if t.auto_trade
        else "Автоторговля: нет (только сигналы)\n"
    )
    return (
        f"<b>ATR Pullback #{row.id}</b>\n"
        f"{t.symbol} · {t.tf_pair_label()}\n"
        f"{alias}"
        f"EMA {t.ema_fast}/{t.ema_slow}\n"
        f"{auto}"
        f"Состояние: <b>{row.state}</b>\n"
        f"Часы МСК:\n{wh}\n"
        f"Статус: {'включено' if row.enabled else 'выключено'}"
    )


def _manage_kb(task_id: int, enabled: bool) -> InlineKeyboardMarkup:
    toggle = "Выключить" if enabled else "Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle, callback_data=f"ap:toggle:{task_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=f"ap:del:{task_id}"
                )
            ],
            [InlineKeyboardButton(text="« Список", callback_data="ap:list")],
        ]
    )


async def _summary(data: dict) -> str:
    auto = data.get("auto_trade", False)
    auto_line = "нет"
    if auto:
        auto_line = f"да · ${data.get('position_usd', 0):.0f} · {data.get('leverage', 1)}x"
    alias = data.get("alias") or "—"
    from app.trading_schedule import format_schedule_label

    wh = format_schedule_label(data.get("trading_hours", []))
    return (
        "<b>Подтверждение ATR Pullback</b>\n"
        f"Символ: <code>{data['symbol']}</code> (linear)\n"
        f"EMA: {data['ema_fast']}/{data['ema_slow']}\n"
        f"БТФ/МТФ: {interval_label(data['btf_interval'])}/"
        f"{interval_label(data['mtf_interval'])}\n"
        f"Псевдоним: {alias}\n"
        f"Автоторговля: {auto_line}\n"
        f"Часы: {wh}\n\n"
        "Задание создаётся <b>выключенным</b>."
    )


@router.message(Command("atr_add"))
async def cmd_atr_add(message: Message, state: FSMContext) -> None:
    if not get_settings().is_advisor_mode:
        await message.answer("ATR Pullback — только в BOT_MODE=advisor.")
        return
    await state.clear()
    await state.set_state(CreateAtrPullbackStates.symbol)
    await message.answer(
        "Шаг 1/9. <b>Тикер</b> linear Bybit, например <code>BTCUSDT</code>.",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(Command("atr_tasks"))
async def cmd_atr_tasks(message: Message, state: FSMContext) -> None:
    if not get_settings().is_advisor_mode:
        await message.answer("ATR Pullback — только в BOT_MODE=advisor.")
        return
    await state.clear()
    async with session_scope() as session:
        rows = await fetch_all_atr_pullback_tasks(session)
    if not rows:
        await message.answer(
            "Нет заданий ATR Pullback. Создайте: /atr_add",
            reply_markup=back_menu_kb(advisor_mode=True),
        )
        return
    lines = []
    buttons = []
    for r in rows:
        t = atr_pullback_task_from_row(r)
        st = "🟢" if r.enabled else "⚪️"
        lines.append(f"#{r.id} {st} {t.display_name()} {t.tf_pair_label()} [{r.state}]")
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"#{r.id} {t.symbol}",
                    callback_data=f"ap:view:{r.id}",
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text="« Меню", callback_data="task:menu")])
    await message.answer(
        "ATR Pullback:\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.message(CreateAtrPullbackStates.symbol, F.text)
async def ap_symbol(message: Message, state: FSMContext) -> None:
    sym = message.text.strip().upper()
    if len(sym) < 3 or not re.fullmatch(r"[A-Z0-9]+", sym):
        await message.answer("Некорректный тикер.")
        return
    await state.update_data(symbol=sym)
    await state.set_state(CreateAtrPullbackStates.ema_params)
    await message.answer(
        "Шаг 2/9. <b>EMA</b> — два числа: быстрая и медленная.\nПример: <code>12 26</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(CreateAtrPullbackStates.ema_params, F.text)
async def ap_ema(message: Message, state: FSMContext) -> None:
    try:
        fast, slow = _parse_ema_pair(message.text)
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.update_data(ema_fast=fast, ema_slow=slow)
    await state.set_state(CreateAtrPullbackStates.btf_interval)
    await message.answer(
        "Шаг 3/9. <b>Базовый ТФ (БТФ)</b> — кросс EMA:",
        parse_mode="HTML",
        reply_markup=_btf_kb(),
    )


@router.callback_query(F.data.startswith("ap:btf:"))
async def ap_btf(callback: CallbackQuery, state: FSMContext) -> None:
    btf = callback.data.split(":")[-1]
    await state.update_data(btf_interval=btf)
    await state.set_state(CreateAtrPullbackStates.mtf_interval)
    await callback.message.edit_text(
        f"Шаг 4/9. <b>Младший ТФ (МТФ)</b> — ATR и вход (БТФ {interval_label(btf)}):",
        parse_mode="HTML",
        reply_markup=_mtf_kb(btf),
    )
    await callback.answer()


@router.callback_query(F.data == "ap:back:btf")
async def ap_back_btf(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateAtrPullbackStates.btf_interval)
    await callback.message.edit_text(
        "Шаг 3/9. <b>Базовый ТФ (БТФ)</b>:",
        parse_mode="HTML",
        reply_markup=_btf_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ap:mtf:"))
async def ap_mtf(callback: CallbackQuery, state: FSMContext) -> None:
    mtf = callback.data.split(":")[-1]
    data = await state.get_data()
    try:
        validate_btf_mtf(data["btf_interval"], mtf)
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
        return
    await state.update_data(mtf_interval=mtf)
    await state.set_state(CreateAtrPullbackStates.trading_hours)
    from app.trading_schedule import SCHEDULE_HELP

    await callback.message.edit_text(
        "Шаг 5/9. Расписание (МСК).\n\n" + SCHEDULE_HELP,
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(CreateAtrPullbackStates.trading_hours, F.text)
async def ap_hours(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    if raw == "-":
        wins: list[dict[str, str]] = []
    else:
        try:
            wins = _parse_trading_hours(message.text)
        except ValueError as e:
            await message.answer(str(e))
            return
    await state.update_data(trading_hours=wins)
    await state.set_state(CreateAtrPullbackStates.alias)
    await message.answer(
        "Шаг 6/9. <b>Псевдоним</b> или <code>-</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(CreateAtrPullbackStates.alias, F.text)
async def ap_alias(message: Message, state: FSMContext) -> None:
    try:
        alias = _parse_alias(message.text)
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.update_data(alias=alias)
    await state.set_state(CreateAtrPullbackStates.auto_trade)
    await message.answer(
        "Шаг 7/9. <b>Автоматически открывать позицию</b> на Bybit linear?",
        parse_mode="HTML",
        reply_markup=_yes_no_kb("ap:auto"),
    )


@router.callback_query(F.data == "ap:auto:yes")
async def ap_auto_yes(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(auto_trade=True)
    await state.set_state(CreateAtrPullbackStates.position_usd)
    await callback.message.edit_text(
        "Шаг 8/9. <b>Номинал позиции в $</b> (полная стоимость с учётом плеча).\n"
        "Пример: <code>500</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "ap:auto:no")
async def ap_auto_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(auto_trade=False, position_usd=0.0, leverage=1)
    await state.set_state(CreateAtrPullbackStates.confirm)
    data = await state.get_data()
    await callback.message.edit_text(
        await _summary(data),
        parse_mode="HTML",
        reply_markup=_confirm_kb(),
    )
    await callback.answer()


@router.message(CreateAtrPullbackStates.position_usd, F.text)
async def ap_position_usd(message: Message, state: FSMContext) -> None:
    try:
        usd = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("Число в долларах, например 500")
        return
    if usd <= 0:
        await message.answer("Номинал должен быть > 0")
        return
    await state.update_data(position_usd=usd)
    await state.set_state(CreateAtrPullbackStates.leverage)
    await message.answer(
        "Шаг 9/9. <b>Кредитное плечо</b> (целое число).\nПример: <code>10</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(CreateAtrPullbackStates.leverage, F.text)
async def ap_leverage(message: Message, state: FSMContext) -> None:
    try:
        lev = int(message.text.strip())
    except ValueError:
        await message.answer("Целое число, например 10")
        return
    if lev < 1:
        await message.answer("Плечо ≥ 1")
        return
    await state.update_data(leverage=lev)
    await state.set_state(CreateAtrPullbackStates.confirm)
    data = await state.get_data()
    await message.answer(
        await _summary(data),
        parse_mode="HTML",
        reply_markup=_confirm_kb(),
    )


@router.callback_query(F.data == "ap:confirm:yes")
async def ap_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        validate_btf_mtf(data["btf_interval"], data["mtf_interval"])
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
        return
    async with session_scope() as session:
        dup = await find_atr_pullback_task_by_key(
            session,
            symbol=data["symbol"],
            btf_interval=data["btf_interval"],
            mtf_interval=data["mtf_interval"],
            ema_fast=data["ema_fast"],
            ema_slow=data["ema_slow"],
        )
        if dup:
            await callback.answer(f"Уже есть задание #{dup.id}", show_alert=True)
            return
        row = await add_atr_pullback_task(
            session,
            symbol=data["symbol"],
            ema_fast=data["ema_fast"],
            ema_slow=data["ema_slow"],
            btf_interval=data["btf_interval"],
            mtf_interval=data["mtf_interval"],
            trading_hours=data.get("trading_hours", []),
            alias=data.get("alias", ""),
            auto_trade=data.get("auto_trade", False),
            position_usd=float(data.get("position_usd", 0)),
            leverage=int(data.get("leverage", 1)),
            enabled=False,
        )
    await state.clear()
    await callback.message.edit_text(
        f"Создано задание ATR Pullback #{row.id} (выключено).\n"
        f"Включите в /atr_tasks",
        reply_markup=back_menu_kb(advisor_mode=True),
    )
    await callback.answer()


@router.callback_query(F.data == "ap:list")
async def ap_list_cb(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session_scope() as session:
        rows = await fetch_all_atr_pullback_tasks(session)
    if not rows:
        await callback.message.edit_text("Нет заданий.")
        await callback.answer()
        return
    lines = [f"#{r.id} {r.symbol} [{r.state}]" for r in rows]
    buttons = [
        [InlineKeyboardButton(text=f"#{r.id}", callback_data=f"ap:view:{r.id}")]
        for r in rows
    ]
    await callback.message.edit_text(
        "ATR Pullback:\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ap:view:"))
async def ap_view(callback: CallbackQuery) -> None:
    tid = int(callback.data.split(":")[-1])
    async with session_scope() as session:
        row = await get_atr_pullback_task(session, tid)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.message.edit_text(
        _task_card(row),
        parse_mode="HTML",
        reply_markup=_manage_kb(tid, row.enabled),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ap:toggle:"))
async def ap_toggle(callback: CallbackQuery) -> None:
    tid = int(callback.data.split(":")[-1])
    async with session_scope() as session:
        row = await get_atr_pullback_task(session, tid)
        if not row:
            await callback.answer("Не найдено", show_alert=True)
            return
        new_st = not row.enabled
        await set_atr_pullback_enabled(session, tid, new_st)
        row = await get_atr_pullback_task(session, tid)
    await callback.message.edit_text(
        _task_card(row),
        parse_mode="HTML",
        reply_markup=_manage_kb(tid, row.enabled),
    )
    await callback.answer("Включено" if new_st else "Выключено")


@router.callback_query(F.data.startswith("ap:del:"))
async def ap_del(callback: CallbackQuery) -> None:
    tid = int(callback.data.split(":")[-1])
    async with session_scope() as session:
        ok = await delete_atr_pullback_task(session, tid)
    if not ok:
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.message.edit_text(f"Задание #{tid} удалено.")
    await callback.answer()
