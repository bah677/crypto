"""Telegram: Pump&Dump scanner (фаза 1)."""

from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.states import PumpScanStates
from app.config import get_settings
from app.db.session import session_scope
from app.pump_scan.params import (
    EDIT_GROUPS,
    FIELD_LABELS,
    GROUP_LABELS,
    PumpScanParams,
    field_prompt,
    parse_field_value,
)
from app.pump_scan.tvh_ui import format_tvh_home, format_tvh_watchlist_page
from app.repository.pump_scan import get_pump_config, set_pump_enabled, update_pump_params
from app.repository.pump_tvh_watch import fetch_active_pump_tvh_watches, purge_expired_pump_tvh_watches
from app.services.pump_tvh_monitor import compute_watchlist_scores_sync, tvh_params_from_scan
from app.services.pump_scan import (
    format_pool_list_page,
    format_pump_status,
    parse_manual_scan_time,
    run_pump_manual_scan,
    run_pump_universe_refresh,
)

router = Router()


def _main_kb(enabled: bool) -> InlineKeyboardMarkup:
    toggle = "🔴 Выключить" if enabled else "🟢 Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=toggle, callback_data="pu:toggle"),
            ],
            [
                InlineKeyboardButton(text="🔍 Сейчас", callback_data="pu:scan:now"),
                InlineKeyboardButton(text="🕐 На дату", callback_data="pu:scan:at"),
            ],
            [
                InlineKeyboardButton(text="🌐 Обновить пул", callback_data="pu:pool"),
            ],
            [
                InlineKeyboardButton(text="📋 Пул монет", callback_data="pu:list:0"),
                InlineKeyboardButton(text="🎯 ТВХ", callback_data="pu:tvh:home"),
            ],
            [
                InlineKeyboardButton(text="📉 Fade A/B", callback_data="pu:fade:home"),
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="pu:settings"),
            ],
        ]
    )


def _settings_kb() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=GROUP_LABELS[g],
                callback_data=f"pu:grp:{g}",
            )
        ]
        for g in (
            "universe",
            "detect",
            "schedule",
            "downtrend",
            "oi",
            "climax",
            "funding_roc",
            "funding_oi",
            "isolation",
            "distance",
            "outcomes",
            "risk_sizing",
            "orderbook",
            "tvh",
        )
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="pu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _fade_mode_label(mode: str) -> str:
    m = (mode or "boost").strip().lower()
    if m == "filter":
        return "filter (только даунтренд)"
    if m == "tag_only":
        return "tag_only (только метка)"
    return "boost (приоритет даунтренда)"


def _format_fade_status(params: PumpScanParams) -> str:
    block = "вкл" if params.oi_new_money_hard_block else "выкл"
    return (
        "<b>Pump-in-Downtrend · A/B</b>\n\n"
        "Быстрое переключение режима fade без правки всех параметров.\n\n"
        f"• <b>downtrend_mode</b>: <code>{params.downtrend_mode}</code> "
        f"— {_fade_mode_label(params.downtrend_mode)}\n"
        f"• <b>oi_new_money_hard_block</b>: <b>{block}</b>\n\n"
        "<b>Пресеты</b>\n"
        "• <b>A (baseline)</b> — boost + block выкл (сбор статистики)\n"
        "• <b>B (strict fade)</b> — filter + block вкл (только fade-кандидаты)\n"
        "• <b>Tag only</b> — только метки в алерте, без фильтра/score"
    )


def _fade_kb(params: PumpScanParams) -> InlineKeyboardMarkup:
    mode = (params.downtrend_mode or "filter").strip().lower()
    block_on = bool(params.oi_new_money_hard_block)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🅰️ A: boost (baseline)",
                    callback_data="pu:fade:preset:a",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🅱️ B: filter + block OI",
                    callback_data="pu:fade:preset:b",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏷 Tag only",
                    callback_data="pu:fade:preset:tag",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=(
                        "📉 Downtrend: filter ✓"
                        if mode == "filter"
                        else "📉 Downtrend → filter"
                    ),
                    callback_data="pu:fade:toggle:filter",
                ),
                InlineKeyboardButton(
                    text=(
                        "📈 Downtrend: boost ✓"
                        if mode == "boost"
                        else "📈 Downtrend → boost"
                    ),
                    callback_data="pu:fade:toggle:boost",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=(
                        "🔴 OI block: ВКЛ ✓"
                        if block_on
                        else "⚪ OI block: выкл → вкл"
                    ),
                    callback_data="pu:fade:toggle:oi_block",
                ),
            ],
            [
                InlineKeyboardButton(text="« Pump scanner", callback_data="pu:home"),
            ],
        ]
    )


def _group_kb(group: str, params: PumpScanParams) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for field in EDIT_GROUPS[group]:
        val = getattr(params, field)
        label = FIELD_LABELS.get(field, field)
        if isinstance(val, float) and field.startswith("tvh_") and field != "tvh_min_score":
            if 0 < val < 1:
                val = f"{val * 100:.0f}%"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✏️ {label}: {val}",
                    callback_data=f"pu:field:{field}",
                )
            ]
        )
    back_cb = "pu:tvh:home" if group == "tvh" else "pu:settings"
    back_lbl = "« ТВХ" if group == "tvh" else "« Настройки"
    rows.append([InlineKeyboardButton(text=back_lbl, callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _tvh_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 Вотчлист", callback_data="pu:tvh:list:0"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="pu:tvh:home"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Настройки ТВХ", callback_data="pu:grp:tvh"),
            ],
            [
                InlineKeyboardButton(text="« Pump scanner", callback_data="pu:home"),
            ],
        ]
    )


def _tvh_list_kb(page: int, pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"pu:tvh:list:{page - 1}"))
        nav.append(
            InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="pu:tvh:list:noop")
        )
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"pu:tvh:list:{page + 1}"))
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"pu:tvh:list:{page}"),
            InlineKeyboardButton(text="« ТВХ", callback_data="pu:tvh:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pool_list_kb(page: int, pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"pu:list:{page - 1}"))
        nav.append(
            InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="pu:list:noop")
        )
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"pu:list:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="pu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _fetch_watches():
    async with session_scope() as session:
        await purge_expired_pump_tvh_watches(session)
        return await fetch_active_pump_tvh_watches(session)


async def _render_tvh_home() -> tuple[str, InlineKeyboardMarkup]:
    async with session_scope() as session:
        row = await get_pump_config(session)
        params = row.params()
    watches = await _fetch_watches()
    return format_tvh_home(params, watches), _tvh_home_kb()


async def _render_tvh_list(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    async with session_scope() as session:
        row = await get_pump_config(session)
        scan_params = row.params()
    watches = await _fetch_watches()
    tvh_p = tvh_params_from_scan(scan_params)
    scores = await asyncio.to_thread(compute_watchlist_scores_sync, watches, tvh_p)
    text, page, pages = format_tvh_watchlist_page(
        watches,
        page,
        scores=scores,
        min_score=scan_params.tvh_min_score,
    )
    return text, _tvh_list_kb(page, pages)


async def _render_pool_list(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    async with session_scope() as session:
        row = await get_pump_config(session)
        coins = row.pool_coins()
        text, page, pages = format_pool_list_page(
            coins, page, pool_updated_at=row.pool_updated_at
        )
    return text, _pool_list_kb(page, pages)


async def _render_home() -> tuple[str, InlineKeyboardMarkup]:
    async with session_scope() as session:
        row = await get_pump_config(session)
    return format_pump_status(row), _main_kb(row.enabled)


async def _ensure_pump_enabled(message: Message) -> bool:
    s = get_settings()
    if not s.pump_scan_enabled:
        await message.answer(
            "Pump&amp;Dump модуль выключен в .env (<code>PUMP_SCAN_ENABLED=0</code>)."
        )
        return False
    return True


async def _render_fade() -> tuple[str, InlineKeyboardMarkup]:
    async with session_scope() as session:
        row = await get_pump_config(session)
        params = row.params()
    return _format_fade_status(params), _fade_kb(params)


async def _apply_fade_updates(**updates: object) -> PumpScanParams:
    async with session_scope() as session:
        row = await get_pump_config(session)
        params = row.params()
        params_dict = params.to_dict()
        params_dict.update(updates)
        params = PumpScanParams.from_dict(params_dict)
        await update_pump_params(session, params)
        return params


@router.message(Command("pump", "pump_scan", "pump_settings"))
async def cmd_pump(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not await _ensure_pump_enabled(message):
        return
    text, kb = await _render_home()
    await message.answer(text, reply_markup=kb)


@router.message(Command("pump_tvh"))
async def cmd_pump_tvh(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not await _ensure_pump_enabled(message):
        return
    text, kb = await _render_tvh_home()
    await message.answer(text, reply_markup=kb)


@router.message(Command("pump_fade"))
async def cmd_pump_fade(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not await _ensure_pump_enabled(message):
        return
    text, kb = await _render_fade()
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "pu:fade:home")
async def cb_fade_home(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await _render_fade()
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


@router.callback_query(F.data.startswith("pu:fade:preset:"))
async def cb_fade_preset(query: CallbackQuery) -> None:
    preset = query.data.split(":", 3)[3]
    if preset == "a":
        await _apply_fade_updates(downtrend_mode="boost", oi_new_money_hard_block=False)
        note = "A: boost + OI block выкл"
    elif preset == "b":
        await _apply_fade_updates(downtrend_mode="filter", oi_new_money_hard_block=True)
        note = "B: filter + OI block вкл"
    elif preset == "tag":
        await _apply_fade_updates(downtrend_mode="tag_only", oi_new_money_hard_block=False)
        note = "Tag only: метки без фильтра"
    else:
        await query.answer("Неизвестный пресет")
        return
    text, kb = await _render_fade()
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer(f"✅ {note}")


@router.callback_query(F.data.startswith("pu:fade:toggle:"))
async def cb_fade_toggle(query: CallbackQuery) -> None:
    action = query.data.split(":", 3)[3]
    async with session_scope() as session:
        row = await get_pump_config(session)
        params = row.params()
    if action == "filter":
        await _apply_fade_updates(downtrend_mode="filter")
        note = "downtrend_mode = filter"
    elif action == "boost":
        await _apply_fade_updates(downtrend_mode="boost")
        note = "downtrend_mode = boost"
    elif action == "oi_block":
        new_val = not params.oi_new_money_hard_block
        await _apply_fade_updates(oi_new_money_hard_block=new_val)
        note = f"oi_new_money_hard_block = {'вкл' if new_val else 'выкл'}"
    else:
        await query.answer("Неизвестное действие")
        return
    text, kb = await _render_fade()
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer(f"✅ {note}")


@router.callback_query(F.data == "pu:home")
async def cb_home(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await _render_home()
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


@router.callback_query(F.data == "pu:tvh:home")
async def cb_tvh_home(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await _render_tvh_home()
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


@router.callback_query(F.data.startswith("pu:tvh:list:"))
async def cb_tvh_list(query: CallbackQuery) -> None:
    suffix = query.data.split(":")[-1]
    if suffix == "noop":
        await query.answer()
        return
    try:
        page = int(suffix)
    except ValueError:
        page = 0
    await query.answer("Обновление score…")
    text, kb = await _render_tvh_list(page)
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


@router.callback_query(F.data == "pu:toggle")
async def cb_toggle(query: CallbackQuery) -> None:
    async with session_scope() as session:
        row = await get_pump_config(session)
        row = await set_pump_enabled(session, not row.enabled)
    text, kb = await _render_home()
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer("Включено" if row.enabled else "Выключено")


@router.callback_query(F.data.in_({"pu:scan", "pu:scan:now"}))
async def cb_scan_now(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.answer("Скан…")
    processed = await run_pump_manual_scan()
    n = len(processed)
    if n:
        await query.message.answer(
            f"Скан завершён: <b>{n}</b> импульс(ов) → вотчлист ТВХ.\n"
            "Алерт в топик — когда ТВХ готова. Смотреть: <code>/pump_tvh</code>"
        )
    else:
        await query.message.answer(
            "Скан завершён: импульсов не найдено (или не добавлено в вотчлист)."
        )


@router.callback_query(F.data == "pu:scan:at")
async def cb_scan_at(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PumpScanStates.scan_at)
    await query.message.answer(
        "<b>Исторический скан</b>\n"
        "Введите дату и время среза (<b>MSK</b>):\n"
        "<code>yyyy-mm-dd hh:mm</code>\n"
        "Напр. <code>2026-06-16 12:00</code>\n\n"
        "<i>На срезе ищется готовая ТВХ сразу; если нет — алерт не шлётся.</i>"
    )
    await query.answer()


@router.message(PumpScanStates.scan_at, F.text)
async def on_scan_at_time(message: Message, state: FSMContext) -> None:
    await state.clear()
    try:
        as_of = parse_manual_scan_time(message.text or "")
    except ValueError as e:
        await message.answer(str(e))
        return

    label = as_of.strftime("%Y-%m-%d %H:%M MSK")
    status = await message.answer(f"Исторический скан на <b>{label}</b>…")
    try:
        processed = await run_pump_manual_scan(as_of=as_of)
    except ValueError as e:
        await status.edit_text(str(e))
        return

    n = len(processed)
    if n:
        await status.edit_text(
            f"Срез <b>{label}</b>: отправлено <b>{n}</b> алерт(ов) с готовой ТВХ."
        )
    else:
        await status.edit_text(
            f"Срез <b>{label}</b>: алертов с ТВХ нет "
            f"(импульс не найден или точка входа на младшем TF не подтверждена)."
        )


@router.callback_query(F.data == "pu:pool")
async def cb_pool(query: CallbackQuery) -> None:
    await query.answer("Обновление пула…")
    n = await run_pump_universe_refresh(force=True)
    text, kb = await _render_home()
    await query.message.edit_text(text, reply_markup=kb)
    await query.message.answer(f"Пул обновлён: <b>{n}</b> монет.")


@router.callback_query(F.data.startswith("pu:list:"))
async def cb_pool_list(query: CallbackQuery) -> None:
    suffix = query.data.split(":", 2)[2]
    if suffix == "noop":
        await query.answer()
        return
    try:
        page = int(suffix)
    except ValueError:
        page = 0
    text, kb = await _render_pool_list(page)
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


@router.callback_query(F.data == "pu:settings")
async def cb_settings(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = (
        "<b>Pump&amp;Dump · настройки</b>\n"
        "Импульс → вотчлист ТВХ → алерт только при готовой точке входа.\n"
        "Выберите блок:"
    )
    await query.message.edit_text(text, reply_markup=_settings_kb())
    await query.answer()


@router.callback_query(F.data.startswith("pu:grp:"))
async def cb_group(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    group = query.data.split(":", 2)[2]
    async with session_scope() as session:
        row = await get_pump_config(session)
        params = row.params()
    title = GROUP_LABELS.get(group, group)
    await query.message.edit_text(
        f"<b>{title}</b>\nНажмите параметр для изменения:",
        reply_markup=_group_kb(group, params),
    )
    await query.answer()


@router.callback_query(F.data.startswith("pu:field:"))
async def cb_field(query: CallbackQuery, state: FSMContext) -> None:
    field = query.data.split(":", 2)[2]
    async with session_scope() as session:
        row = await get_pump_config(session)
        params = row.params()
    current = getattr(params, field)
    await state.set_state(PumpScanStates.value)
    await state.update_data(field=field)
    await query.message.answer(field_prompt(field, current))
    await query.answer()


@router.message(PumpScanStates.value, F.text)
async def on_field_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = str(data.get("field") or "")
    if not field:
        await state.clear()
        await message.answer("Поле не выбрано. /pump")
        return
    try:
        val = parse_field_value(field, message.text or "")
    except ValueError as e:
        await message.answer(str(e))
        return

    async with session_scope() as session:
        row = await get_pump_config(session)
        params = row.params()
        params_dict = params.to_dict()
        params_dict[field] = val
        params = PumpScanParams.from_dict(params_dict)
        await update_pump_params(session, params)

    await state.clear()
    label = FIELD_LABELS.get(field, field)
    disp = val
    if isinstance(val, float) and field.startswith("tvh_") and field != "tvh_min_score" and 0 < val < 1:
        disp = f"{val * 100:.0f}%"
    tail = "/pump_tvh" if field.startswith("tvh_") else "/pump"
    await message.answer(f"✅ {label} = <code>{disp}</code>\n{tail} — меню")
