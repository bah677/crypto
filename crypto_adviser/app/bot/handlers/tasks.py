from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.bot.keyboards import (
    back_menu_kb,
    cancel_kb,
    levels_done_kb,
    main_menu_kb,
    task_toggle_kb,
)
from app.bot.states import CreateTaskStates
from app.config import get_settings
from app.db.session import session_scope
from app.repository.tasks import (
    add_task,
    fetch_all_tasks,
    get_task,
    set_task_enabled,
)

router = Router()

CHANNEL_BYBIT = "bybit_v5"
CHANNEL_MT5 = "mt5"

_VALID_INTERVALS = {
    "1",
    "3",
    "5",
    "15",
    "30",
    "60",
    "120",
    "240",
    "360",
    "720",
    "D",
    "W",
    "M",
}

_HHMM = re.compile(r"^\d{1,2}:\d{2}$")


def _parse_trading_channel(text: str) -> str:
    t = text.strip().lower()
    if t in ("api", "bybit", "v5", "rest", "спот", "фьюч"):
        return CHANNEL_BYBIT
    if t in ("mt5", "tradfi", "традфи", "терминал"):
        return CHANNEL_MT5
    raise ValueError(
        "Отправьте `api` — торговля только через **Bybit REST v5** "
        "(тикер должен существовать в API, **без подстановок**).\n"
        "Или `mt5` — символ как в **MetaTrader 5** (исполнение через MT5 в боте пока не подключено)."
    )


def _verify_bybit_symbol_exists(symbol: str) -> str:
    from app.bybit.instruments import verify_bybit_symbol

    return verify_bybit_symbol(symbol)
def _reject_comma(label: str, s: str) -> None:
    if "," in s:
        raise ValueError(
            f"{label}: десятичный разделитель только точка «.», запятая недопустима."
        )


def _parse_level_price(text: str) -> str:
    s = text.strip().replace(" ", "")
    _reject_comma("Уровень", s)
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        raise ValueError("Уровень: допустимы цифры и одна точка в дробной части, например 2321.5")
    float(s)
    return s


def _parse_lots(text: str) -> str:
    s = text.strip().replace(" ", "")
    _reject_comma("Лоты", s)
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        raise ValueError(
            "Размер в лотах: только цифры и точка как разделитель, например 0.01 или 1"
        )
    v = float(s)
    if v <= 0:
        raise ValueError("Размер позиции должен быть больше нуля.")
    return s


def _parse_ema_block(text: str) -> tuple[int, int, str]:
    raw = text.strip()
    _reject_comma("EMA / интервал", raw)
    parts = raw.split()
    if len(parts) != 3:
        raise ValueError(
            "Нужно ровно три значения через пробел: "
            "<EMA быстрая> <EMA медленная> <интервал минут, например 5>"
        )
    fast, slow, interval = int(parts[0]), int(parts[1]), parts[2].strip()
    if fast <= 0 or slow <= 0:
        raise ValueError("Периоды EMA должны быть положительными.")
    if interval not in _VALID_INTERVALS:
        raise ValueError(
            f"Интервал {interval} не поддерживается API. Допустимо: "
            + ", ".join(sorted(_VALID_INTERVALS, key=lambda x: (len(x), x)))
        )
    return fast, slow, interval


def _parse_trading_hours(text: str) -> list[dict[str, str]]:
    from app.trading_schedule import parse_schedule_text

    return parse_schedule_text(text)  # type: ignore[return-value]


def _task_card(task) -> str:
    from app.trading_schedule import format_schedule_label

    lv = ", ".join(x.price for x in task.levels) or "—"
    wh = format_schedule_label(task.trading_hours()).replace("; ", "\n") or "круглосуточно"
    sl = task.stop_loss_ticks
    ch = getattr(task, "trading_channel", CHANNEL_BYBIT) or CHANNEL_BYBIT
    ch_human = "MT5 (REST не торгует)" if ch == CHANNEL_MT5 else "Bybit API v5"
    return (
        f"Задание #{task.id}\n"
        f"Канал: {ch_human}\n"
        f"Пара: {task.symbol}\n"
        f"EMA: {task.ema_fast} / {task.ema_slow}, TF: {task.kline_interval}\n"
        f"Дельта: {task.delta_ticks} тиков, TP: {task.take_profit_ticks} тиков, SL: {sl} тиков\n"
        f"Лоты (qty): {task.order_qty}\n"
        f"Уровни: {lv}\n"
        f"Часы МСК:\n{wh}\n"
        f"Статус: {'включено' if task.enabled else 'выключено'}"
    )


@router.callback_query(F.data == "task:menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    s = get_settings()
    await callback.message.edit_text(
        "Меню:",
        reply_markup=main_menu_kb(advisor_mode=s.is_advisor_mode),
    )
    await callback.answer()


@router.callback_query(F.data == "task:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    s = get_settings()
    await callback.message.edit_text(
        "Сценарий создания отменён.",
        reply_markup=main_menu_kb(advisor_mode=s.is_advisor_mode),
    )
    await callback.answer()


@router.callback_query(F.data == "task:new")
async def cb_new(callback: CallbackQuery, state: FSMContext) -> None:
    if get_settings().is_advisor_mode:
        await callback.answer(
            "В режиме советчика используйте «Новое задание» в главном меню.",
            show_alert=True,
        )
        return
    await state.set_state(CreateTaskStates.trading_channel)
    await state.set_data({"levels": []})
    await callback.message.edit_text(
        "Шаг 1/10. **Канал исполнения** (строго один из вариантов):\n"
        "• `api` — только **Bybit REST v5** (как в `.env`: `BYBIT_CATEGORY`). "
        "Тикер должен **точно** существовать в этом API; **никаких «похожих» тикеров**.\n"
        "• `mt5` — символ как в **MetaTrader 5** / TradFi; через REST бот **пока не торгует**, "
        "задание можно сохранить на будущее.\n\n"
        "Отправьте слово `api` или `mt5`.",
        parse_mode="Markdown",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(CreateTaskStates.trading_channel, F.text)
async def st_trading_channel(message: Message, state: FSMContext) -> None:
    try:
        ch = _parse_trading_channel(message.text)
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.update_data(trading_channel=ch)
    await state.set_state(CreateTaskStates.symbol)
    if ch == CHANNEL_BYBIT:
        await message.answer(
            "Шаг 2/10. **Тикер для Bybit API** — только как в `instruments-info` "
            f"для category=`{get_settings().bybit_category}` (латиница и цифры, например BTCUSDT). "
            "Если тикера нет — задание не создастся.",
            parse_mode="Markdown",
            reply_markup=cancel_kb(),
        )
    else:
        await message.answer(
            "Шаг 2/10. **Символ как в MT5** (например `XAUUSD.s`). "
            "Регистр и точка сохраняются. Исполнение через MT5 в этом боте **ещё не подключено**.",
            parse_mode="Markdown",
            reply_markup=cancel_kb(),
        )


@router.message(CreateTaskStates.symbol, F.text)
async def st_symbol(message: Message, state: FSMContext) -> None:
    import asyncio

    data = await state.get_data()
    ch = data.get("trading_channel", CHANNEL_BYBIT)
    if ch == CHANNEL_BYBIT:
        sym = message.text.strip().upper()
        if len(sym) < 3:
            await message.answer("Слишком короткий символ. Повторите.")
            return
        if not re.fullmatch(r"[A-Z0-9]+", sym):
            await message.answer(
                "Для канала `api` допустимы только латинские буквы и цифры (тикер Bybit API)."
            )
            return
        try:
            await asyncio.to_thread(_verify_bybit_symbol_exists, sym)
        except Exception as e:
            cat = get_settings().bybit_category
            await message.answer(
                f"Тикер `{sym}` **не найден** в канале Bybit API (`category={cat!r}`).\n"
                f"Подстановка других тикеров **запрещена**. Укажите существующий тикер или канал `mt5`.\n"
                f"Ответ: `{e!s}`",
                parse_mode="Markdown",
            )
            return
    else:
        sym = message.text.strip()
        if len(sym) < 2 or "\n" in sym or len(sym) > 64:
            await message.answer("Некорректный символ MT5. Повторите.")
            return
    await state.update_data(symbol=sym)
    await state.set_state(CreateTaskStates.ema_params)
    await message.answer(
        "Шаг 3/10. Три значения через пробел: "
        "<EMA короткая> <EMA длинная> <интервал свечей (1,3,5,15,30,60,...)>\n"
        "Дробный разделитель в этом шаге не используется. Пример: `7 21 5`",
        parse_mode="Markdown",
        reply_markup=cancel_kb(),
    )


@router.message(CreateTaskStates.ema_params, F.text)
async def st_ema(message: Message, state: FSMContext) -> None:
    try:
        fast, slow, interval = _parse_ema_block(message.text)
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.update_data(ema_fast=fast, ema_slow=slow, kline_interval=interval)
    await state.set_state(CreateTaskStates.levels)
    await message.answer(
        "Шаг 4/10. Уровни цены — по одному числу в сообщении (дробная часть **только через точку**).\n"
        "Когда закончите — кнопка ниже.",
        parse_mode="Markdown",
        reply_markup=levels_done_kb(),
    )


@router.callback_query(F.data == "task:levels_done")
async def cb_levels_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    levels: list[str] = data.get("levels") or []
    if not levels:
        await callback.answer("Добавьте хотя бы один уровень.", show_alert=True)
        return
    await state.set_state(CreateTaskStates.delta_ticks)
    await callback.message.edit_text(
        "Шаг 5/10. Дельта от уровня в тиках (целое число ≥ 0).",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(CreateTaskStates.levels, F.text)
async def st_levels(message: Message, state: FSMContext) -> None:
    try:
        s = _parse_level_price(message.text)
    except ValueError as e:
        await message.answer(str(e))
        return
    data = await state.get_data()
    levels: list[str] = list(data.get("levels") or [])
    levels.append(s)
    await state.update_data(levels=levels)
    await message.answer(f"Уровень сохранён (всего {len(levels)}). Можно добавить ещё.")


@router.message(CreateTaskStates.delta_ticks, F.text)
async def st_delta(message: Message, state: FSMContext) -> None:
    try:
        d = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно целое число тиков.")
        return
    if d < 0:
        await message.answer("Дельта не может быть отрицательной.")
        return
    await state.update_data(delta_ticks=d)
    await state.set_state(CreateTaskStates.tp_ticks)
    await message.answer(
        "Шаг 6/10. Take-profit в тиках от точки входа (целое число > 0).",
        reply_markup=cancel_kb(),
    )


@router.message(CreateTaskStates.tp_ticks, F.text)
async def st_tp(message: Message, state: FSMContext) -> None:
    try:
        tp = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно целое число тиков.")
        return
    if tp <= 0:
        await message.answer("TP должен быть > 0.")
        return
    await state.update_data(tp_ticks=tp)
    await state.set_state(CreateTaskStates.sl_ticks)
    await message.answer(
        "Шаг 7/10. Stop-loss в тиках от точки входа (целое число).\n"
        "Отправьте `0`, если не выставлять SL в ордере.",
        parse_mode="Markdown",
        reply_markup=cancel_kb(),
    )


@router.message(CreateTaskStates.sl_ticks, F.text)
async def st_sl(message: Message, state: FSMContext) -> None:
    try:
        sl = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно целое число тиков (0 = без SL в ордере).")
        return
    if sl < 0:
        await message.answer("SL не может быть отрицательным.")
        return
    await state.update_data(sl_ticks=sl)
    await state.set_state(CreateTaskStates.trading_hours)
    from app.trading_schedule import SCHEDULE_HELP

    await message.answer(
        "Шаг 8/10. Расписание по МСК (когда разрешено открывать сделки).\n\n"
        + SCHEDULE_HELP,
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(CreateTaskStates.trading_hours, F.text)
async def st_hours(message: Message, state: FSMContext) -> None:
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
    await state.set_state(CreateTaskStates.position_lots)
    d = get_settings().bybit_default_position_lots
    await message.answer(
        "Шаг 9/10. Размер позиции в **стандартных лотах** этого инструмента "
        "(то же значение уйдёт в поле `qty` API после округления по шагу лота).\n"
        "Дробная часть **только через точку** (например `0.01`).\n"
        f"Отправьте `-` чтобы взять значение из .env: `{d}`",
        parse_mode="Markdown",
        reply_markup=cancel_kb(),
    )


@router.message(CreateTaskStates.position_lots, F.text)
async def st_position_lots(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    if raw == "-":
        lots = get_settings().bybit_default_position_lots
    else:
        try:
            lots = _parse_lots(raw)
        except ValueError as e:
            await message.answer(str(e))
            return
    data = await state.get_data()
    try:
        async with session_scope() as session:
            task = await add_task(
                session,
                symbol=data["symbol"],
                trading_channel=data.get("trading_channel", CHANNEL_BYBIT),
                ema_fast=data["ema_fast"],
                ema_slow=data["ema_slow"],
                kline_interval=data["kline_interval"],
                delta_ticks=data["delta_ticks"],
                take_profit_ticks=data["tp_ticks"],
                stop_loss_ticks=data["sl_ticks"],
                order_qty=lots,
                trading_hours=data["trading_hours"],
                levels=data["levels"],
            )
    except Exception as e:
        await message.answer(f"Ошибка сохранения: {e}")
        return
    await state.clear()
    await message.answer(
        f"Задание #{task.id} создано (по умолчанию выключено).\n"
        f"Канал: {task.trading_channel}  {task.symbol} EMA {task.ema_fast}/{task.ema_slow} TF {task.kline_interval}\n"
        f"TP {task.take_profit_ticks} тиков, SL {task.stop_loss_ticks} тиков, лоты {task.order_qty}\n\n"
        "Включите его в «Список заданий».",
        reply_markup=main_menu_kb(advisor_mode=get_settings().is_advisor_mode),
    )


@router.callback_query(F.data == "task:list")
async def cb_list(callback: CallbackQuery, state: FSMContext) -> None:
    if get_settings().is_advisor_mode:
        await callback.answer(
            "В режиме советчика — «Список заданий» в главном меню.",
            show_alert=True,
        )
        return
    await state.clear()
    async with session_scope() as session:
        tasks = await fetch_all_tasks(session)
    if not tasks:
        await callback.message.edit_text(
            "Пока нет заданий.", reply_markup=back_menu_kb()
        )
        await callback.answer()
        return
    lines = []
    for t in tasks:
        st = "🟢 вкл" if t.enabled else "⚪️ выкл"
        sl = t.stop_loss_ticks
        ch_tag = "[MT5]" if getattr(t, "trading_channel", CHANNEL_BYBIT) == CHANNEL_MT5 else "[API]"
        lines.append(
            f"#{t.id} {ch_tag} {st} — {t.symbol} EMA{t.ema_fast}/{t.ema_slow} TF {t.kline_interval} "
            f"TP{t.take_profit_ticks}/SL{sl}"
        )
    text = "Задания:\n" + "\n".join(lines) + "\n\nВыберите номер для переключения:"
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"#{t.id} {t.symbol}", callback_data=f"task:view:{t.id}")]
        for t in tasks
    ]
    rows.append([InlineKeyboardButton(text="« Меню", callback_data="task:menu")])
    ikb = InlineKeyboardMarkup(inline_keyboard=rows)
    await callback.message.edit_text(text, reply_markup=ikb)
    await callback.answer()


@router.callback_query(F.data.startswith("task:view:"))
async def cb_view(callback: CallbackQuery, state: FSMContext) -> None:
    tid = int(callback.data.split(":")[-1])
    async with session_scope() as session:
        task = await get_task(session, tid)
    if not task:
        await callback.answer("Задание не найдено", show_alert=True)
        return
    await callback.message.edit_text(
        _task_card(task), reply_markup=task_toggle_kb(task.id, task.enabled)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task:toggle:"))
async def cb_toggle(callback: CallbackQuery) -> None:
    tid = int(callback.data.split(":")[-1])
    async with session_scope() as session:
        task = await get_task(session, tid)
        if not task:
            await callback.answer("Не найдено", show_alert=True)
            return
        new_val = not task.enabled
        await set_task_enabled(session, tid, new_val)
        task = await get_task(session, tid)
    assert task is not None
    await callback.message.edit_text(
        _task_card(task), reply_markup=task_toggle_kb(task.id, task.enabled)
    )
    await callback.answer("Сохранено")