"""Автозакрытие позиции по аномальной минутной свече (отдельный мастер + правила)."""

from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from app.bybit.rest import BybitRest
from app.db.session import session_scope
from app.repository.sl_anom_close_master import get_sl_anom_close_master, set_sl_anom_close_params
from app.repository.sl_anom_close_rules import (
    disable_sl_anom_close_rule,
    fetch_all_sl_anom_close_rules,
    get_sl_anom_close_rule_by_symbol,
    upsert_sl_anom_close_rule,
)
from app.services.sl_anom_close_params import SlAnomCloseParams
router = Router()


def _pos_side_label(side: str) -> str:
    return "Long" if side == "Buy" else "Short"


async def _open_positions() -> list[tuple[str, str]]:
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
                callback_data=f"ac:pos:{sym}",
            )
        ]
        for sym, side in positions
    ]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="ac:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("sl_anom_follow"))
async def cmd_sl_anom_follow(message: Message, state: FSMContext) -> None:
    await state.clear()
    try:
        positions = await _open_positions()
    except Exception as e:
        await message.answer(f"⚠️ Не удалось загрузить позиции Bybit: {e}")
        return
    if not positions:
        await message.answer(
            "Нет открытых linear-позиций на Bybit.\n"
            "Автозакрытие привязано к реальной позиции."
        )
        return
    await message.answer(
        "<b>Автозакрытие</b> · аномальное минутное тело\n"
        "Выберите позицию (символ и сторона):",
        reply_markup=_positions_kb(positions),
    )


@router.callback_query(F.data == "ac:cancel")
async def cb_ac_cancel(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.answer()
    await query.message.edit_text("Отменено.")


@router.callback_query(F.data.startswith("ac:pos:"))
async def cb_ac_pick_pos(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    sym = query.data.split(":")[-1].upper()
    snap = await asyncio.to_thread(BybitRest(category="linear").get_linear_position_snapshot, sym)
    if not snap:
        await query.answer("Позиция уже закрыта", show_alert=True)
        return

    async with session_scope() as session:
        master = await get_sl_anom_close_master(session)

    params = SlAnomCloseParams()
    summary = (
        f"<code>{sym}</code> · {_pos_side_label(snap.side)}\n\n"
        "Условия:\n"
        f"- аномальное тело ≥ {params.body_multiplier:g}× среднее тела предыдущих {params.lookback_bars} свечей (1m)\n"
        f"- фитиль ≤ {params.wick_max_ratio*100:.0f}% от тела (верхний для Long / нижний для Short)\n"
        f"- след. свеча: обратная (слив) ИЛИ тело ≤ 1/{params.next_small_divisor:g} от аномального\n\n"
        f"Мастер: {'вкл' if master.enabled else 'выкл'}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Включить авто-закрытие",
                    callback_data=f"ac:enable:{sym}",
                )
            ],
            [InlineKeyboardButton(text="« Назад", callback_data="ac:back")],
        ]
    )
    await state.update_data(symbol=sym, position_side=snap.side)
    await query.message.edit_text(f"<b>Автозакрытие по аномалии</b>\n\n{summary}", reply_markup=kb)
    await query.answer()


@router.callback_query(F.data == "ac:back")
async def cb_ac_back(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    positions = await _open_positions()
    await query.message.edit_text(
        "Выберите позицию:", reply_markup=_positions_kb(positions) if positions else None
    )
    await query.answer()


@router.callback_query(F.data.startswith("ac:enable:"))
async def cb_ac_enable(query: CallbackQuery, state: FSMContext) -> None:
    sym = query.data.split(":")[-1].upper()
    snap = await asyncio.to_thread(BybitRest(category="linear").get_linear_position_snapshot, sym)
    if not snap:
        await query.answer("Позиция закрыта", show_alert=True)
        return
    side = snap.side

    async with session_scope() as session:
        # при включении правил включаем и мастер, чтобы стратегия реально работала
        await set_sl_anom_close_params(session, enabled=True, params=None)
        await upsert_sl_anom_close_rule(session, symbol=sym, position_side=side)

    await state.clear()
    await query.message.edit_text(f"✅ Включено автозакрытие для <code>{sym}</code> · {_pos_side_label(side)}.")
    await query.answer("Включено")

    from app.services.admin_notify import notify_sl_follow_channel

    await notify_sl_follow_channel(f"✅ Включено автозакрытие по аномальной 1m свече · <code>{sym}</code> · {_pos_side_label(side)}")


@router.message(Command("sl_anom_list"))
async def cmd_sl_anom_list(message: Message) -> None:
    async with session_scope() as session:
        rows = await fetch_all_sl_anom_close_rules(session)
    if not rows:
        await message.answer("Автозакрытие SL не настроено.\n<code>/sl_anom_follow</code> — включить.")
        return
    lines: list[str] = []
    for r in rows:
        st = "🟢" if r.enabled else "⚪️"
        lines.append(f"{st} <code>{r.symbol}</code> · {_pos_side_label(r.position_side)} · pending={r.pending_anomaly_bar_open_ms is not None}")
    await message.answer("<b>Автозакрытие SL</b>\n\n" + "\n".join(lines))


@router.message(Command("sl_anom_stop"))
async def cmd_sl_anom_stop(message: Message, state: FSMContext) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Укажите символ: <code>/sl_anom_stop SYMBOL</code>")
        return
    sym = parts[1].upper().replace("/", "")
    async with session_scope() as session:
        ok = await disable_sl_anom_close_rule(session, sym)
    if not ok:
        await message.answer(f"Для <code>{sym}</code> автозакрытие не найдено/уже выключено.")
        return
    await message.answer(f"⏹ Выключено автозакрытие для <code>{sym}</code>.")
    await state.clear()


@router.message(Command("sl_anom_master"))
async def cmd_sl_anom_master(message: Message) -> None:
    async with session_scope() as session:
        master = await get_sl_anom_close_master(session)
    params = SlAnomCloseParams()
    enabled = "вкл" if master.enabled else "выкл"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⛔ Выключить мастер",
                    callback_data="ac:master:0",
                ),
                InlineKeyboardButton(
                    text="✅ Включить мастер",
                    callback_data="ac:master:1",
                ),
            ],
        ]
    )
    await message.answer(
        "<b>Мастер автозакрытия</b>\n\n"
        f"Статус: <b>{enabled}</b>\n\n"
        "Параметры (по умолчанию):\n"
        f"- interval: <code>{params.interval}</code>\n"
        f"- lookback: <code>{params.lookback_bars}</code>\n"
        f"- body_multiplier: <code>{params.body_multiplier}</code>\n"
        f"- wick_max_ratio: <code>{params.wick_max_ratio}</code>\n"
        f"- next_small_divisor: <code>{params.next_small_divisor}</code>",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("ac:master:"))
async def cb_ac_master_toggle(query: CallbackQuery) -> None:
    v = query.data.split(":")[-1]
    enabled = v == "1"
    async with session_scope() as session:
        await set_sl_anom_close_params(session, enabled=enabled, params=None)
    await query.answer("Обновлено")
    await query.message.edit_text(f"Мастер автозакрытия: {'вкл' if enabled else 'выкл'}.")

