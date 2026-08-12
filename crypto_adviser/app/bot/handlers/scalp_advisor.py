"""Telegram: скальп-советник M5/M1."""

from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.handlers.tasks import _parse_trading_hours
from app.bot.keyboards import back_menu_kb, cancel_kb
from app.bot.states import (
    CreateScalpAdvisorStates,
    EditScalpLevelsStates,
    EditScalpStrategyStates,
)
from app.config import get_settings
from app.db.session import session_scope
from app.repository.scalp_advisor import (
    add_scalp_task,
    delete_scalp_task,
    fetch_all_scalp_tasks,
    find_scalp_task_by_symbol,
    get_scalp_task,
    set_scalp_task_enabled,
    update_scalp_levels,
    update_scalp_strategy,
)
from app.scalp_advisor.strategy_params import (
    CONDITION_LABELS,
    CONDITION_ORDER,
    EDIT_GROUPS,
    FIELD_LABELS,
    parse_field_value,
)
from app.scalp_advisor.tasks import TRADE_OPEN, scalp_task_from_row
from app.services.scalp_advisor_debug import rotate_debug_logs

router = Router()

SCALP_UI_REV = "v5"

# Callback-и (v5): sc:edit — меню блоков · sc:eg — параметры блока · sc:ef — ввод числа
# Legacy sc:ped/sc:peg/sc:pef — редирект на v5 (старые кнопки «Значения»)


def _parse_level_prices(text: str, *, min_count: int = 1) -> list[float]:
    raw = text.replace(",", " ").split()
    if len(raw) < min_count:
        raise ValueError(
            f"Минимум {min_count} уровн{'я' if min_count == 1 else 'ей'} через пробел или запятую.\n"
            "Пример: 2650, 2660 2675"
        )
    out: list[float] = []
    for p in raw:
        try:
            out.append(float(p.replace(",", ".")))
        except ValueError as e:
            raise ValueError(f"Некорректный уровень: {p}") from e
    return sorted(set(out))


def _parse_levels(text: str) -> list[float]:
    return _parse_level_prices(text, min_count=2)


def _parse_alias(raw: str) -> str:
    if raw.strip() == "-":
        return ""
    if len(raw.strip()) > 64:
        raise ValueError("Псевдоним ≤ 64 символов")
    return raw.strip()



def _scalp_status_emoji(row) -> str:
    if not row.enabled:
        return "⚪"
    t = scalp_task_from_row(row)
    if (row.trade_state or "idle") == TRADE_OPEN:
        return "📈"
    from app.trading_schedule import now_msk_in_windows

    if now_msk_in_windows(t.trading_hours):
        return "🟢"
    return "🟡"


def _trade_label(row) -> str:
    trade = row.trade_state or "idle"
    if trade == TRADE_OPEN and row.trade_side:
        side = "LONG" if row.trade_side == "Buy" else "SHORT"
        return f"open · {side}"
    return trade


def _render_scalp_list(rows) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"<b>Scalp M5/M1</b> · UI {SCALP_UI_REV}",
        "🟢 в расписании · 🟡 вне · ⚪ выкл · 📈 сделка открыта",
        "⚙️ стратегия · 📊 уровни — кнопки под каждым символом",
        "",
    ]
    buttons: list[list[InlineKeyboardButton]] = []
    for r in rows:
        t = scalp_task_from_row(r)
        em = _scalp_status_emoji(r)
        en = "вкл" if r.enabled else "выкл"
        lines.append(f"{em} #{r.id} <b>{t.display_name()}</b> · {en} · {_trade_label(r)}")
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{em} #{r.id} {t.symbol}",
                    callback_data=f"sc:view:{r.id}",
                ),
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text="⚙️ Стратегия",
                    callback_data=f"sc:params:{r.id}",
                ),
                InlineKeyboardButton(
                    text="📊 Уровни",
                    callback_data=f"sc:lv:{r.id}",
                ),
            ]
        )
    buttons.append([InlineKeyboardButton(text="« Меню", callback_data="task:menu")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


def _confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Создать", callback_data="sc:confirm:yes"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="task:cancel"),
            ]
        ]
    )


def _task_label(row) -> str:
    t = scalp_task_from_row(row)
    return f"Scalp #{row.id} · {t.display_name()}"


def _strategy_header(row) -> str:
    """Шапка на всех экранах редактирования стратегии: символ + имя + id."""
    t = scalp_task_from_row(row)
    sym = f"<code>{t.symbol}</code>"
    alias = (t.alias or "").strip()
    if alias:
        who = f"<b>{alias}</b> · {sym}"
    else:
        who = f"<b>{sym}</b>"
    return f"{who}\nScalp <b>#{row.id}</b> · стратегия M5/M1"


def _strategy_text(row) -> str:
    return _strategy_header(row) + "\n\n" + row.strategy_params().format_telegram()


def _edit_menu_text(row) -> str:
    return (
        _strategy_header(row)
        + "\n\n<b>Что изменить?</b>\n"
        "Выберите блок → параметр → введите число.\n"
        "⇄ на экране стратегии — вкл/выкл условия."
    )


def _params_kb(tid: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key in CONDITION_ORDER:
        short = CONDITION_LABELS[key].split(". ", 1)[-1]
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"⇄ {short}",
                    callback_data=f"sc:pt:{tid}:{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="✏️ Параметры", callback_data=f"sc:edit:{tid}")])
    rows.append(
        [
            InlineKeyboardButton(text="« К заданию", callback_data=f"sc:view:{tid}"),
            InlineKeyboardButton(text="« Список", callback_data="sc:list"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _edit_pick_kb(tid: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for key in CONDITION_ORDER:
        short = CONDITION_LABELS[key].split(". ", 1)[-1]
        if key not in EDIT_GROUPS:
            continue
        pair.append(
            InlineKeyboardButton(
                text=f"✏️ {short[:18]}",
                callback_data=f"sc:eg:{tid}:{key}",
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append(
        [InlineKeyboardButton(text="✏️ Общие EMA/ATR/SL", callback_data=f"sc:eg:{tid}:global")]
    )
    rows.append([InlineKeyboardButton(text="« Стратегия", callback_data=f"sc:params:{tid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _edit_fields_kb(tid: int, group: str, p) -> InlineKeyboardMarkup:
    fields = EDIT_GROUPS.get(group, ())
    rows = [
        [
            InlineKeyboardButton(
                text=f"{FIELD_LABELS.get(f, f)}: {p.format_field_value(f)}",
                callback_data=f"sc:ef:{tid}:{f}",
            )
        ]
        for f in fields
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"sc:edit:{tid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _levels_text(row) -> str:
    t = scalp_task_from_row(row)
    lines = [
        f"<b>Уровни TP</b> · {_task_label(row)}",
        f"Всего: {len(t.levels)} (минимум 2)",
        "",
    ]
    for i, p in enumerate(t.levels, 1):
        lines.append(f"{i}. <code>{p:g}</code>")
    lines.append("")
    lines.append("<i>TP1/TP2 берутся из ближайших уровней выше/ниже входа.</i>")
    return "\n".join(lines)


def _levels_kb(tid: int, levels: list[float]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for i, p in enumerate(levels):
        pair.append(
            InlineKeyboardButton(
                text=f"🗑 {p:g}",
                callback_data=f"sc:ld:{tid}:{i}",
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data=f"sc:la:{tid}")])
    rows.append(
        [
            InlineKeyboardButton(text="« К заданию", callback_data=f"sc:view:{tid}"),
            InlineKeyboardButton(text="« Список", callback_data="sc:list"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _fetch_row(tid: int):
    async with session_scope() as session:
        return await get_scalp_task(session, tid)


async def _show_strategy(callback: CallbackQuery, row, *, toast: str | None = None) -> None:
    await callback.message.edit_text(
        _strategy_text(row),
        parse_mode="HTML",
        reply_markup=_params_kb(row.id),
    )
    await callback.answer(toast or "")


async def _show_card(callback: CallbackQuery, row, *, toast: str | None = None) -> None:
    await callback.message.edit_text(
        _card(row),
        parse_mode="HTML",
        reply_markup=_manage_kb(row.id, row.enabled),
    )
    await callback.answer(toast or "")


async def _show_levels(
    callback: CallbackQuery, row, *, extra: str = "", toast: str | None = None
) -> None:
    t = scalp_task_from_row(row)
    text = _levels_text(row)
    if extra:
        text += f"\n\n{extra}"
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=_levels_kb(row.id, t.levels),
    )
    await callback.answer(toast or "")


async def _show_edit_menu(
    callback: CallbackQuery, row, *, toast: str | None = None
) -> None:
    await callback.message.edit_text(
        _edit_menu_text(row),
        parse_mode="HTML",
        reply_markup=_edit_pick_kb(row.id),
    )
    await callback.answer(toast or "")


async def _show_edit_group(callback: CallbackQuery, row, group: str) -> None:
    p = row.strategy_params()
    await callback.message.edit_text(
        _strategy_header(row) + "\n\n" + p.edit_group_text(group),
        parse_mode="HTML",
        reply_markup=_edit_fields_kb(row.id, group, p),
    )
    await callback.answer()


def _manage_kb(tid: int, enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Выключить" if enabled else "Включить",
                    callback_data=f"sc:toggle:{tid}",
                )
            ],
            [InlineKeyboardButton(text="⚙️ Стратегия", callback_data=f"sc:params:{tid}")],
            [InlineKeyboardButton(text="📊 Уровни", callback_data=f"sc:lv:{tid}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"sc:del:{tid}")],
            [InlineKeyboardButton(text="« Список", callback_data="sc:list")],
        ]
    )


def _card(row) -> str:
    from app.trading_schedule import format_schedule_label

    t = scalp_task_from_row(row)
    lv = ", ".join(f"{x:g}" for x in t.levels[:8])
    if len(t.levels) > 8:
        lv += "…"
    wh = format_schedule_label(t.trading_hours).replace("; ", "\n")
    em = _scalp_status_emoji(row)
    en_label = "включено" if row.enabled else "выключено"
    if (row.trade_state or "idle") == TRADE_OPEN:
        side = "LONG" if row.trade_side == "Buy" else "SHORT"
        trade_line = f"Сделка: <b>открыта · {side}</b>"
        if row.entry_price is not None and row.trade_sl is not None:
            trade_line += f"\nEntry {row.entry_price:g} · SL {row.trade_sl:g}"
    else:
        trade_line = f"Сделка: {row.trade_state or 'idle'}"
    p = row.strategy_params()
    bb = "вкл" if p.bb_enabled else "выкл"
    return (
        f"<b>Scalp #{row.id}</b> {em} · {t.display_name()}\n"
        f"Мониторинг: <b>{en_label}</b>\n"
        f"{trade_line}\n"
        f"Уровни ({len(t.levels)}): {lv}\n"
        f"Часы МСК:\n{wh}\n"
        f"Стратегия: rev {p.revision} · BB {bb}\n"
        f"Кнопки: <b>⚙️ Стратегия</b> · <b>📊 Уровни</b>"
    )


async def _summary(data: dict) -> str:
    from app.trading_schedule import format_schedule_label

    lv = ", ".join(f"{x:g}" for x in data.get("levels", [])[:10])
    return (
        "<b>Scalp · подтверждение</b>\n"
        f"Символ: <code>{data['symbol']}</code> (linear)\n"
        f"Уровни: {lv}\n"
        f"Псевдоним: {data.get('alias') or '—'}\n"
        f"Часы: {format_schedule_label(data.get('trading_hours', []))}\n\n"
        "BB M1 по умолчанию выкл · закрытие в R (1R = entry − SL).\n"
        "Задание создаётся <b>выключенным</b>."
    )


@router.message(Command("scalp_add"))
async def cmd_scalp_add(message: Message, state: FSMContext) -> None:
    if not get_settings().is_advisor_mode:
        await message.answer("Scalp — только BOT_MODE=advisor.")
        return
    await state.clear()
    await state.set_state(CreateScalpAdvisorStates.symbol)
    await message.answer(
        "Шаг 1/4. <b>Тикер</b> linear: <code>BTCUSDT</code>, <code>XAUTUSDT</code>…",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(Command("scalp_tasks"))
async def cmd_scalp_tasks(message: Message, state: FSMContext) -> None:
    if not get_settings().is_advisor_mode:
        await message.answer("Scalp — только BOT_MODE=advisor.")
        return
    await state.clear()
    async with session_scope() as session:
        rows = await fetch_all_scalp_tasks(session)
    if not rows:
        await message.answer("Нет заданий. /scalp_add", reply_markup=back_menu_kb(advisor_mode=True))
        return
    text, kb = _render_scalp_list(rows)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "sc:list")
async def sc_list_cb(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session_scope() as session:
        rows = await fetch_all_scalp_tasks(session)
    if not rows:
        await callback.message.edit_text(
            "Нет заданий Scalp. /scalp_add",
            reply_markup=back_menu_kb(advisor_mode=True),
        )
        await callback.answer()
        return
    text, kb = _render_scalp_list(rows)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.message(CreateScalpAdvisorStates.symbol, F.text)
async def sc_symbol(message: Message, state: FSMContext) -> None:
    sym = message.text.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]+", sym):
        await message.answer("Некорректный тикер.")
        return
    await state.update_data(symbol=sym)
    await state.set_state(CreateScalpAdvisorStates.levels)
    await message.answer(
        "Шаг 2/4. <b>Уровни TP</b> (≥2 числа через пробел).\n"
        "Пример BTC: <code>95000 96000 97500</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(CreateScalpAdvisorStates.levels, F.text)
async def sc_levels(message: Message, state: FSMContext) -> None:
    try:
        levels = _parse_levels(message.text)
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.update_data(levels=levels)
    await state.set_state(CreateScalpAdvisorStates.trading_hours)
    from app.trading_schedule import SCHEDULE_HELP

    await message.answer(
        "Шаг 3/4. Расписание (МСК).\n\n" + SCHEDULE_HELP,
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(CreateScalpAdvisorStates.trading_hours, F.text)
async def sc_hours(message: Message, state: FSMContext) -> None:
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
    await state.set_state(CreateScalpAdvisorStates.alias)
    await message.answer("Шаг 4/4. Псевдоним или <code>-</code>", parse_mode="HTML", reply_markup=cancel_kb())


@router.message(CreateScalpAdvisorStates.alias, F.text)
async def sc_alias(message: Message, state: FSMContext) -> None:
    try:
        alias = _parse_alias(message.text)
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.update_data(alias=alias)
    await state.set_state(CreateScalpAdvisorStates.confirm)
    data = await state.get_data()
    await message.answer(
        await _summary(data),
        parse_mode="HTML",
        reply_markup=_confirm_kb(),
    )


@router.callback_query(F.data == "sc:confirm:yes")
async def sc_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    async with session_scope() as session:
        dup = await find_scalp_task_by_symbol(session, data["symbol"])
        if dup:
            await callback.answer(f"Уже есть #{dup.id}", show_alert=True)
            return
        row = await add_scalp_task(
            session,
            symbol=data["symbol"],
            levels=data["levels"],
            trading_hours=data.get("trading_hours", []),
            alias=data.get("alias", ""),
            enabled=False,
        )
    rotate_debug_logs(row.symbol, row.strategy_params(), task_id=row.id, reason="create")
    await state.clear()
    await callback.message.edit_text(
        f"Scalp #{row.id} создан (выключен). /scalp_tasks",
        reply_markup=back_menu_kb(advisor_mode=True),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^sc:params:\d+$"))
async def sc_params(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    tid = int(callback.data.split(":")[-1])
    row = await _fetch_row(tid)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    await _show_strategy(callback, row)


@router.callback_query(F.data.regexp(r"^sc:pt:\d+:[a-z0-9_]+$"))
async def sc_param_toggle(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    tid = int(parts[2])
    key = parts[3]
    async with session_scope() as session:
        row = await get_scalp_task(session, tid)
        if not row:
            await callback.answer("Не найдено", show_alert=True)
            return
        p = row.strategy_params()
        try:
            p.toggle_condition(key)
        except ValueError:
            await callback.answer("Неизвестное условие", show_alert=True)
            return
        row = await update_scalp_strategy(session, tid, p)
    rotate_debug_logs(row.symbol, row.strategy_params(), task_id=tid, reason="toggle")
    await _show_strategy(callback, row, toast="Сохранено · debug обновлён")


async def _open_edit_menu(
    callback: CallbackQuery, state: FSMContext, tid: int, *, toast: str | None = None
) -> None:
    await state.clear()
    row = await _fetch_row(tid)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    await _show_edit_menu(callback, row, toast=toast)


@router.callback_query(F.data.regexp(r"^sc:edit:\d+$"))
async def sc_edit_menu(callback: CallbackQuery, state: FSMContext) -> None:
    tid = int(callback.data.split(":")[-1])
    await _open_edit_menu(callback, state, tid)


@router.callback_query(F.data.regexp(r"^sc:ped:\d+$"))
async def sc_legacy_ped(callback: CallbackQuery, state: FSMContext) -> None:
    """Старые кнопки «Значения» (key=value) → новое меню параметров."""
    tid = int(callback.data.split(":")[-1])
    await _open_edit_menu(callback, state, tid, toast="Обновлено → меню параметров")


async def _open_edit_group(
    callback: CallbackQuery, state: FSMContext, tid: int, group: str
) -> None:
    fields = EDIT_GROUPS.get(group)
    if not fields:
        await callback.answer("Нет числовых параметров — только ⇄", show_alert=True)
        return
    row = await _fetch_row(tid)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    if len(fields) == 1:
        await _start_field_edit(callback, state, tid, fields[0])
        return
    await _show_edit_group(callback, row, group)


async def _start_field_edit(
    callback: CallbackQuery, state: FSMContext, tid: int, field: str
) -> None:
    async with session_scope() as session:
        row = await get_scalp_task(session, tid)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    p = row.strategy_params()
    await state.set_state(EditScalpStrategyStates.value)
    await state.update_data(edit_task_id=tid, edit_field=field)
    await callback.message.answer(
        p.field_prompt(field, task_label=_strategy_header(row)),
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^sc:eg:\d+:.+$"))
async def sc_edit_group(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    tid = int(parts[2])
    group = parts[3]
    await _open_edit_group(callback, state, tid, group)


@router.callback_query(F.data.regexp(r"^sc:peg:\d+:.+$"))
async def sc_legacy_peg(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    tid = int(parts[2])
    group = parts[3]
    await _open_edit_group(callback, state, tid, group)


@router.callback_query(F.data.regexp(r"^sc:ef:\d+:\w+$"))
async def sc_edit_field(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    tid = int(parts[2])
    field = parts[3]
    await _start_field_edit(callback, state, tid, field)


@router.callback_query(F.data.regexp(r"^sc:pef:\d+:\w+$"))
async def sc_legacy_pef(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    tid = int(parts[2])
    field = parts[3]
    await _start_field_edit(callback, state, tid, field)


@router.message(EditScalpStrategyStates.value, F.text)
async def sc_param_edit_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    tid = int(data["edit_task_id"])
    field = data["edit_field"]
    try:
        val = parse_field_value(field, message.text)
    except ValueError as e:
        await message.answer(str(e))
        return
    async with session_scope() as session:
        row = await get_scalp_task(session, tid)
        if not row:
            await message.answer("Задание не найдено")
            await state.clear()
            return
        p = row.strategy_params()
        changed = p.apply_patch({field: val})
        if not changed:
            await message.answer("Значение не изменилось")
            return
        row = await update_scalp_strategy(session, tid, p)
    rotate_debug_logs(row.symbol, row.strategy_params(), task_id=tid, reason="patch")
    await state.clear()
    label = FIELD_LABELS.get(field, field)
    val_s = row.strategy_params().format_field_value(field)
    await message.answer(
        _strategy_text(row)
        + f"\n\n✅ <b>{label}</b> → <code>{val_s}</code>",
        parse_mode="HTML",
        reply_markup=_params_kb(tid),
    )


@router.callback_query(F.data.regexp(r"^sc:lv:\d+$"))
async def sc_levels_view(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    tid = int(callback.data.split(":")[-1])
    row = await _fetch_row(tid)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    await _show_levels(callback, row)


@router.callback_query(F.data.regexp(r"^sc:ld:\d+:\d+$"))
async def sc_level_delete(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    tid = int(parts[2])
    idx = int(parts[3])
    async with session_scope() as session:
        row = await get_scalp_task(session, tid)
        if not row:
            await callback.answer("Не найдено", show_alert=True)
            return
        levels = row.level_prices()
        if idx < 0 or idx >= len(levels):
            await callback.answer("Уровень не найден", show_alert=True)
            return
        if len(levels) <= 2:
            await callback.answer("Минимум 2 уровня", show_alert=True)
            return
        removed = levels.pop(idx)
        try:
            row = await update_scalp_levels(session, tid, levels)
        except ValueError as e:
            await callback.answer(str(e), show_alert=True)
            return
    rotate_debug_logs(row.symbol, row.strategy_params(), task_id=tid, reason="levels")
    await _show_levels(
        callback,
        row,
        extra=f"Удалён: <code>{removed:g}</code>",
        toast="Удалено",
    )


@router.callback_query(F.data.regexp(r"^sc:la:\d+$"))
async def sc_level_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    tid = int(callback.data.split(":")[-1])
    async with session_scope() as session:
        row = await get_scalp_task(session, tid)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    t = scalp_task_from_row(row)
    cur = ", ".join(f"{x:g}" for x in t.levels)
    await state.set_state(EditScalpLevelsStates.add)
    await state.update_data(edit_task_id=tid)
    await callback.message.answer(
        f"<b>{_task_label(row)}</b>\n"
        "<b>Добавить уровни TP</b>\n"
        f"<b>Сейчас:</b> {cur}\n\n"
        "<b>Что ввести:</b> новые цены через запятую или пробел.\n"
        "Пример: <code>2680, 2695</code>\n\n"
        "<b>На что влияет:</b> уровни задают TP1 и TP2 при входе. "
        "Дубликаты объединяются, список сортируется.\n\n"
        "Отмена — /cancel",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(EditScalpLevelsStates.add, F.text)
async def sc_level_add_values(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    tid = int(data["edit_task_id"])
    try:
        new_prices = _parse_level_prices(message.text, min_count=1)
    except ValueError as e:
        await message.answer(str(e))
        return
    async with session_scope() as session:
        row = await get_scalp_task(session, tid)
        if not row:
            await message.answer("Задание не найдено")
            await state.clear()
            return
        merged = sorted(set(row.level_prices()) | set(new_prices))
        try:
            row = await update_scalp_levels(session, tid, merged)
        except ValueError as e:
            await message.answer(str(e))
            return
    rotate_debug_logs(row.symbol, row.strategy_params(), task_id=tid, reason="levels")
    await state.clear()
    t = scalp_task_from_row(row)
    await message.answer(
        _levels_text(row) + f"\n\n✅ Добавлено · всего {len(t.levels)}",
        parse_mode="HTML",
        reply_markup=_levels_kb(tid, t.levels),
    )


@router.callback_query(F.data.regexp(r"^sc:view:\d+$"))
async def sc_view(callback: CallbackQuery) -> None:
    tid = int(callback.data.split(":")[-1])
    row = await _fetch_row(tid)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    await _show_card(callback, row)


@router.callback_query(F.data.regexp(r"^sc:toggle:\d+$"))
async def sc_toggle(callback: CallbackQuery) -> None:
    tid = int(callback.data.split(":")[-1])
    async with session_scope() as session:
        row = await get_scalp_task(session, tid)
        if not row:
            await callback.answer("Не найдено", show_alert=True)
            return
        new_st = not row.enabled
        await set_scalp_task_enabled(session, tid, new_st)
        row = await get_scalp_task(session, tid)
    await _show_card(callback, row, toast="Включено" if new_st else "Выключено")


@router.callback_query(F.data.regexp(r"^sc:del:\d+$"))
async def sc_del(callback: CallbackQuery) -> None:
    tid = int(callback.data.split(":")[-1])
    async with session_scope() as session:
        ok = await delete_scalp_task(session, tid)
    if not ok:
        await callback.answer("Не найдено", show_alert=True)
        return
    async with session_scope() as session:
        rows = await fetch_all_scalp_tasks(session)
    if rows:
        text, kb = _render_scalp_list(rows)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await callback.message.edit_text(
            f"Scalp #{tid} удалён.\nНет заданий — /scalp_add",
            reply_markup=back_menu_kb(advisor_mode=True),
        )
    await callback.answer()
