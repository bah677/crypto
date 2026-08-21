"""Telegram: CRUD заданий советчика (режим BOT_MODE=advisor)."""

from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.advisor.intervals import parse_advisor_ema_block
from app.advisor.tasks import advisor_task_from_row
from app.bybit.instruments import MARKET_CATEGORIES, find_symbol_markets, market_label
from app.bot.handlers.tasks import _parse_trading_hours
from app.bot.keyboards import (
    advisor_delete_confirm_kb,
    advisor_task_manage_kb,
    back_menu_kb,
    cancel_kb,
)
from app.bot.states import CreateAdvisorTaskStates, EditAdvisorTaskStates
from app.config import get_settings
from app.db.session import session_scope
from app.repository.advisor_tasks import (
    add_advisor_task,
    delete_advisor_task,
    fetch_all_advisor_tasks,
    find_advisor_task_by_key,
    get_advisor_task,
    set_advisor_task_enabled,
    update_advisor_task,
)

router = Router()

_EMA_INTERVAL_LEGEND = (
    "Три значения через пробел: <b>EMA быстрая</b> <b>EMA медленная</b> <b>интервал</b>\n\n"
    "Интервал — как в Bybit (число = <b>минуты</b>):\n"
    "• 5, 15, 30 — минуты\n"
    "• <b>60</b> — 1 ч · <b>120</b> — 2 ч · <b>240</b> — 4 ч · <b>720</b> — 12 ч\n"
    "• D — день · W — неделя · M — месяц\n\n"
    "Примеры: <code>9 21 5</code> (5m) · <code>9 21 60</code> (1h) · <code>12 26 240</code> (4h)"
)

_CREATE_EMA_STEP = "Шаг 2/4.\n" + _EMA_INTERVAL_LEGEND

_CREATE_STEP1 = (
    "Шаг 1/4. **Тикер** — как в API Bybit, **без слэша**: `BTCUSDT`, `LABUSDT`.\n"
    "На сайте спот — BTC/USDT, в боте пишите `BTCUSDT`.\n\n"
    "Бот проверит **Спот** и **Бессрочные фьючерсы**. "
    "Если тикер есть на обоих — вы выберете рынок для EMA."
)


def _create_step1_text() -> str:
    return _CREATE_STEP1


def _parse_alias(raw: str) -> str:
    text = raw.strip()
    if text == "-":
        return ""
    if "\n" in text:
        raise ValueError("Псевдоним — одна строка, без переносов.")
    if len(text) > 64:
        raise ValueError("Псевдоним не длиннее 64 символов.")
    return text


def _task_signal_name(task) -> str:
    alias = (task.alias or "").strip()
    if alias:
        return f"{alias} ({task.symbol})"
    return task.symbol


def _market_pick_kb(categories: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=market_label(cat), callback_data=f"adv:market:{cat}"
            )
        ]
        for cat in categories
    ]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="task:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _go_to_ema_step(
    target: Message,
    state: FSMContext,
    *,
    sym: str,
    category: str,
    is_edit: bool,
    ema_hint: str | None = None,
    replace_message: bool = False,
) -> None:
    await state.update_data(symbol=sym, bybit_category=category)
    if is_edit:
        await state.set_state(EditAdvisorTaskStates.ema_params)
        if ema_hint is None:
            tid = (await state.get_data()).get("edit_task_id")
            async with session_scope() as session:
                row = await get_advisor_task(session, tid) if tid else None
            ema_hint = (
                f"{row.ema_fast} {row.ema_slow} {row.kline_interval}" if row else "9 21 5"
            )
        text = (
            f"Рынок: <b>{market_label(category)}</b>\n\n"
            f"Шаг 2/4. EMA и интервал или <code>-</code> (сейчас <code>{ema_hint}</code>).\n\n"
            f"{_EMA_INTERVAL_LEGEND}"
        )
    else:
        await state.set_state(CreateAdvisorTaskStates.ema_params)
        text = (
            f"Тикер <b>{sym}</b> · {market_label(category)}\n\n{_CREATE_EMA_STEP}"
        )
    await target.answer(text, parse_mode="HTML", reply_markup=cancel_kb())


async def _handle_symbol_markets(
    message: Message,
    state: FSMContext,
    sym: str,
    markets: list[str],
    *,
    is_edit: bool,
) -> None:
    if not markets:
        tried = ", ".join(market_label(c) for c in MARKET_CATEGORIES)
        await message.answer(
            f"Тикер <code>{sym}</code> не найден на Bybit ({tried}).\n\n"
            "Проверьте написание: на фьючах часто суффикс USDT "
            "(например <code>BTCUSDT</code>, не <code>BTC</code>). "
            "TradFi/индексы на Bybit могут называться иначе, чем в MT5.",
            parse_mode="HTML",
        )
        return

    await state.update_data(symbol=sym)
    if len(markets) == 1:
        await _go_to_ema_step(message, state, sym=sym, category=markets[0], is_edit=is_edit)
        return

    market_state = EditAdvisorTaskStates.market if is_edit else CreateAdvisorTaskStates.market
    await state.set_state(market_state)
    found = "\n".join(f"• {market_label(c)}" for c in markets)
    await message.answer(
        f"Тикер <b>{sym}</b> найден на нескольких рынках:\n{found}\n\n"
        "Выберите, где считать EMA и смотреть свечи:",
        parse_mode="HTML",
        reply_markup=_market_pick_kb(markets),
    )


async def _begin_create_advisor_task(message: Message, state: FSMContext) -> None:
    await state.set_state(CreateAdvisorTaskStates.symbol)
    await state.set_data({})
    await message.answer(
        _create_step1_text(),
        parse_mode="Markdown",
        reply_markup=cancel_kb(),
    )


def _tasks_list_content(rows) -> tuple[str, InlineKeyboardMarkup]:
    lines = []
    for r in rows:
        t = advisor_task_from_row(r)
        st = "🟢 вкл" if r.enabled else "⚪️ выкл"
        name = _task_signal_name(t)
        lines.append(
            f"#{r.id} {st} — {name} [{market_label(r.bybit_category)}] "
            f"EMA{t.ema_fast}/{t.ema_slow} TF {t.interval_label}"
        )
    text = "Задания советчика:\n" + "\n".join(lines) + "\n\nВыберите задание:"
    ikb_rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"#{r.id} {_task_signal_name(advisor_task_from_row(r))}",
                callback_data=f"adv:view:{r.id}",
            )
        ]
        for r in rows
    ]
    ikb_rows.append([InlineKeyboardButton(text="« Меню", callback_data="task:menu")])
    return text, InlineKeyboardMarkup(inline_keyboard=ikb_rows)


@router.message(Command("task_add"))
async def cmd_task_add(message: Message, state: FSMContext) -> None:
    if not get_settings().is_advisor_mode:
        await message.answer("Команда /task_add — только в режиме советчика (BOT_MODE=advisor).")
        return
    await _begin_create_advisor_task(message, state)


@router.message(Command("tasks"))
async def cmd_tasks(message: Message, state: FSMContext) -> None:
    if not get_settings().is_advisor_mode:
        await message.answer("Команда /tasks — только в режиме советчика (BOT_MODE=advisor).")
        return
    await state.clear()
    async with session_scope() as session:
        rows = await fetch_all_advisor_tasks(session)
    if not rows:
        await message.answer(
            "Пока нет заданий. Создайте: /task_add",
            reply_markup=back_menu_kb(advisor_mode=True),
        )
        return
    text, markup = _tasks_list_content(rows)
    await message.answer(text, reply_markup=markup)


def _advisor_task_card(row) -> str:
    from app.trading_schedule import format_schedule_label

    task = advisor_task_from_row(row)
    wh = format_schedule_label(task.trading_hours).replace("; ", "\n")
    alias_line = f"Псевдоним: {task.alias}\n" if task.alias else ""
    return (
        f"Задание советчика #{row.id}\n"
        f"Рынок: {market_label(row.bybit_category)}\n"
        f"Пара: {task.symbol}\n"
        f"{alias_line}"
        f"EMA: {task.ema_fast} / {task.ema_slow}, TF: {task.interval_label}\n"
        f"Часы МСК:\n{wh}\n"
        f"Статус: {'включено' if row.enabled else 'выключено'}"
    )


async def adv_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateAdvisorTaskStates.symbol)
    await state.set_data({})
    await callback.message.edit_text(
        _create_step1_text(),
        parse_mode="Markdown",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "adv:new")
async def cb_adv_new(callback: CallbackQuery, state: FSMContext) -> None:
    await adv_new(callback, state)


@router.message(CreateAdvisorTaskStates.symbol, F.text)
async def adv_st_symbol(message: Message, state: FSMContext) -> None:
    import asyncio

    sym = message.text.strip().upper()
    if len(sym) < 3 or not re.fullmatch(r"[A-Z0-9]+", sym):
        await message.answer("Некорректный тикер. Латиница и цифры, например BTCUSDT.")
        return
    markets = await asyncio.to_thread(find_symbol_markets, sym)
    await _handle_symbol_markets(message, state, sym, markets, is_edit=False)


@router.callback_query(F.data.startswith("adv:market:"))
async def adv_pick_market(callback: CallbackQuery, state: FSMContext) -> None:
    cat = callback.data.split(":")[-1]
    if cat not in MARKET_CATEGORIES:
        await callback.answer("Некорректный выбор", show_alert=True)
        return
    data = await state.get_data()
    sym = data.get("symbol")
    if not sym:
        await callback.answer("Сначала укажите тикер", show_alert=True)
        return
    cur = await state.get_state()
    is_edit = cur == EditAdvisorTaskStates.market.state
    await _go_to_ema_step(
        callback.message, state, sym=sym, category=cat, is_edit=is_edit, replace_message=True
    )
    await callback.answer()


@router.message(CreateAdvisorTaskStates.ema_params, F.text)
async def adv_st_ema(message: Message, state: FSMContext) -> None:
    try:
        fast, slow, interval = parse_advisor_ema_block(message.text)
    except ValueError as e:
        await message.answer(str(e))
        return
    if fast == slow:
        await message.answer("EMA быстрая и медленная не должны совпадать.")
        return
    await state.update_data(ema_fast=fast, ema_slow=slow, kline_interval=interval)
    await state.set_state(CreateAdvisorTaskStates.trading_hours)
    from app.trading_schedule import SCHEDULE_HELP

    await message.answer(
        "Шаг 3/4. Расписание сигналов (МСК).\n\n" + SCHEDULE_HELP,
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(CreateAdvisorTaskStates.trading_hours, F.text)
async def adv_st_hours(message: Message, state: FSMContext) -> None:
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
    await state.set_state(CreateAdvisorTaskStates.alias)
    await message.answer(
        "Шаг 4/4. <b>Псевдоним</b> — любое слово для сигналов "
        "(вместо тикера в тексте).\n"
        "Пример: <code>Gold</code> → «🟢 Покупка · Gold (XAUTUSDT) · 5m»\n"
        "Отправьте <code>-</code>, если псевдоним не нужен.",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(CreateAdvisorTaskStates.alias, F.text)
async def adv_st_alias(message: Message, state: FSMContext) -> None:
    try:
        alias = _parse_alias(message.text)
    except ValueError as e:
        await message.answer(str(e))
        return

    data = await state.get_data()
    wins = data.get("trading_hours", [])
    async with session_scope() as session:
        dup = await find_advisor_task_by_key(
            session,
            symbol=data["symbol"],
            kline_interval=data["kline_interval"],
            ema_fast=data["ema_fast"],
            ema_slow=data["ema_slow"],
            bybit_category=data["bybit_category"],
        )
        if dup:
            await message.answer(
                f"Такое задание уже есть (#{dup.id}). Измените параметры или отредактируйте существующее."
            )
            return
        row = await add_advisor_task(
            session,
            symbol=data["symbol"],
            ema_fast=data["ema_fast"],
            ema_slow=data["ema_slow"],
            kline_interval=data["kline_interval"],
            bybit_category=data["bybit_category"],
            trading_hours=wins,
            alias=alias,
            enabled=False,
        )

    await state.clear()
    task = advisor_task_from_row(row)
    name = _task_signal_name(task)
    await message.answer(
        f"Задание #{row.id} создано (выключено).\n"
        f"{name} · {market_label(task.bybit_category)} · "
        f"EMA {task.ema_fast}/{task.ema_slow} TF {task.interval_label}\n\n"
        "Включите: /tasks → выберите задание → «▶️ Включить».",
    )


@router.callback_query(F.data == "adv:list")
async def adv_list(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session_scope() as session:
        rows = await fetch_all_advisor_tasks(session)
    if not rows:
        await callback.message.edit_text(
            "Пока нет заданий. Создайте: /task_add",
            reply_markup=back_menu_kb(advisor_mode=True),
        )
        await callback.answer()
        return

    text, markup = _tasks_list_content(rows)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("adv:view:"))
async def adv_view(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    tid = int(callback.data.split(":")[-1])
    async with session_scope() as session:
        row = await get_advisor_task(session, tid)
    if not row:
        await callback.answer("Задание не найдено", show_alert=True)
        return
    await callback.message.edit_text(
        _advisor_task_card(row),
        reply_markup=advisor_task_manage_kb(row.id, row.enabled),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adv:toggle:"))
async def adv_toggle(callback: CallbackQuery) -> None:
    tid = int(callback.data.split(":")[-1])
    async with session_scope() as session:
        row = await get_advisor_task(session, tid)
        if not row:
            await callback.answer("Не найдено", show_alert=True)
            return
        new_val = not row.enabled
        await set_advisor_task_enabled(session, tid, new_val)
        row = await get_advisor_task(session, tid)
    assert row is not None
    await callback.message.edit_text(
        _advisor_task_card(row),
        reply_markup=advisor_task_manage_kb(row.id, row.enabled),
    )
    await callback.answer("Сохранено — действует сразу")


@router.callback_query(F.data.startswith("adv:delete:"))
async def adv_delete_ask(callback: CallbackQuery) -> None:
    tid = int(callback.data.split(":")[-1])
    async with session_scope() as session:
        row = await get_advisor_task(session, tid)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.message.edit_text(
        f"Удалить задание #{tid} ({row.symbol})?\nЭто необратимо.",
        reply_markup=advisor_delete_confirm_kb(tid),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adv:delete_ok:"))
async def adv_delete_ok(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    tid = int(callback.data.split(":")[-1])
    async with session_scope() as session:
        ok = await delete_advisor_task(session, tid)
    if not ok:
        await callback.answer("Уже удалено", show_alert=True)
        return
    await callback.message.edit_text(
        f"Задание #{tid} удалено.",
        reply_markup=back_menu_kb(advisor_mode=True),
    )
    await callback.answer("Удалено")


@router.callback_query(F.data.startswith("adv:edit:"))
async def adv_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    tid = int(callback.data.split(":")[-1])
    async with session_scope() as session:
        row = await get_advisor_task(session, tid)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    await state.set_state(EditAdvisorTaskStates.symbol)
    await state.update_data(edit_task_id=tid)
    await callback.message.edit_text(
        f"Редактирование #{tid}.\n"
        "Шаг 1/4. Новый **тикер Bybit** или `-` чтобы оставить "
        f"`{row.symbol}`.",
        parse_mode="Markdown",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(EditAdvisorTaskStates.symbol, F.text)
async def adv_edit_symbol(message: Message, state: FSMContext) -> None:
    import asyncio

    data = await state.get_data()
    tid = data["edit_task_id"]
    raw = message.text.strip()
    if raw == "-":
        async with session_scope() as session:
            row = await get_advisor_task(session, tid)
        if not row:
            await message.answer("Задание не найдено.")
            await state.clear()
            return
        await _go_to_ema_step(
            message,
            state,
            sym=row.symbol,
            category=row.bybit_category,
            is_edit=True,
            ema_hint=f"{row.ema_fast} {row.ema_slow} {row.kline_interval}",
        )
        return

    sym = raw.upper()
    if len(sym) < 3 or not re.fullmatch(r"[A-Z0-9]+", sym):
        await message.answer("Некорректный тикер.")
        return
    markets = await asyncio.to_thread(find_symbol_markets, sym)
    await _handle_symbol_markets(message, state, sym, markets, is_edit=True)


@router.message(EditAdvisorTaskStates.ema_params, F.text)
async def adv_edit_ema(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    tid = data["edit_task_id"]
    raw = message.text.strip()
    if raw == "-":
        async with session_scope() as session:
            row = await get_advisor_task(session, tid)
        if not row:
            await message.answer("Задание не найдено.")
            await state.clear()
            return
        fast, slow, interval = row.ema_fast, row.ema_slow, row.kline_interval
    else:
        try:
            fast, slow, interval = _parse_ema_block(raw)
        except ValueError as e:
            await message.answer(str(e))
            return
        if fast == slow:
            await message.answer("EMA не должны совпадать.")
            return

    await state.update_data(ema_fast=fast, ema_slow=slow, kline_interval=interval)
    await state.set_state(EditAdvisorTaskStates.trading_hours)
    from app.trading_schedule import SCHEDULE_HELP

    await message.answer(
        "Шаг 3/4. Расписание или <code>-</code> чтобы не менять.\n\n" + SCHEDULE_HELP,
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(EditAdvisorTaskStates.trading_hours, F.text)
async def adv_edit_hours(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    tid = data["edit_task_id"]
    raw = message.text.strip()

    async with session_scope() as session:
        row = await get_advisor_task(session, tid)
        if not row:
            await message.answer("Задание не найдено.")
            await state.clear()
            return

        if raw == "-":
            wins = row.trading_hours()
        else:
            try:
                wins = _parse_trading_hours(message.text)
            except ValueError as e:
                await message.answer(str(e))
                return

    await state.update_data(trading_hours=wins)
    await state.set_state(EditAdvisorTaskStates.alias)
    cur_alias = (row.alias or "").strip()
    hint = cur_alias if cur_alias else "нет"
    await message.answer(
        f"Шаг 4/4. Псевдоним или <code>-</code> чтобы оставить "
        f"(сейчас: <code>{hint}</code>).\n"
        "Любое слово для текста сигнала вместо тикера.",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(EditAdvisorTaskStates.alias, F.text)
async def adv_edit_alias(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    tid = data["edit_task_id"]
    raw = message.text.strip()

    async with session_scope() as session:
        row = await get_advisor_task(session, tid)
        if not row:
            await message.answer("Задание не найдено.")
            await state.clear()
            return

        if raw == "-":
            alias = (row.alias or "").strip()
        else:
            try:
                alias = _parse_alias(raw)
            except ValueError as e:
                await message.answer(str(e))
                return

        wins = data.get("trading_hours", row.trading_hours())

        dup = await find_advisor_task_by_key(
            session,
            symbol=data["symbol"],
            kline_interval=data["kline_interval"],
            ema_fast=data["ema_fast"],
            ema_slow=data["ema_slow"],
            bybit_category=data["bybit_category"],
            exclude_id=tid,
        )
        if dup:
            await message.answer(f"Конфликт с заданием #{dup.id}. Измените параметры.")
            return

        updated = await update_advisor_task(
            session,
            tid,
            symbol=data["symbol"],
            ema_fast=data["ema_fast"],
            ema_slow=data["ema_slow"],
            kline_interval=data["kline_interval"],
            bybit_category=data["bybit_category"],
            trading_hours=wins,
            alias=alias,
        )

    await state.clear()
    if not updated:
        await message.answer("Не удалось сохранить.")
        return
    await message.answer(
        f"Задание #{tid} обновлено — применяется сразу.\n\n{_advisor_task_card(updated)}",
        reply_markup=advisor_task_manage_kb(updated.id, updated.enabled),
    )
