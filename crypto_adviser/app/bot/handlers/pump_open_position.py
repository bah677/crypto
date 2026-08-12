"""Мастер: шорт по EMA 1D / 1W из pump-алерта."""

from __future__ import annotations

import asyncio
import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.pump_dm import answer_pump_callback_continue, send_pump_private
from app.bot.states import PumpOpenPositionStates
from app.bybit.rest import BybitRest
from app.config import get_settings
from app.db.session import session_scope
from app.pump_scan.daily_ema import compute_daily_emas
from app.pump_scan.weekly_ema import compute_weekly_emas, format_ema_entry_label
from app.pump_scan.detect import pump_alert_keyboard
from app.pump_scan.params import PumpScanParams
from app.repository.bot_order_watches import create_bot_order_watch
from app.repository.pump_scan import get_pump_config
from app.services.pump_open_position import (
    build_pump_short_market_plan,
    build_pump_short_plan,
    execute_pump_short_plan,
    format_plan_message,
)
from app.services.pump_scan import build_pump_impulse_alert_bundle, find_best_current_pump_hit

log = logging.getLogger(__name__)
router = Router()

_SIZE_PRESETS = (100, 200, 500, 1000)
_LEV_PRESETS = (10, 20, 30, 50, 75, 100)
_VALID_EMA_KEYS = frozenset({"50", "100", "200", "7W", "14W", "28W"})


def _ema_kb(ema_map: dict[str, float | None]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    def _add_row(keys: list[str]) -> None:
        row: list[InlineKeyboardButton] = []
        for key in keys:
            price = ema_map.get(key)
            if price is None:
                continue
            label = format_ema_entry_label(key)
            row.append(
                InlineKeyboardButton(
                    text=f"{label} ({price:.5g})",
                    callback_data=f"pump:pos:ema:{key}",
                )
            )
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)

    _add_row(["50", "100", "200"])
    _add_row(["7W", "14W", "28W"])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="pump:pos:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="pump:pos:cancel")],
        ]
    )


def _confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Открыть", callback_data="pump:pos:confirm:yes"),
                InlineKeyboardButton(text="❌ Нет", callback_data="pump:pos:confirm:no"),
            ],
        ]
    )


def _size_kb() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for usd in _SIZE_PRESETS:
        row.append(
            InlineKeyboardButton(
                text=f"${usd}",
                callback_data=f"pump:pos:usd:{usd}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="pump:pos:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _leverage_options(max_leverage: int, min_leverage: int) -> list[int]:
    opts: list[int] = []
    first = min(10, max_leverage)
    if first >= min_leverage:
        opts.append(first)
    for lev in _LEV_PRESETS:
        if lev > first and lev <= max_leverage:
            opts.append(lev)
    if max_leverage not in opts and max_leverage >= min_leverage:
        opts.append(max_leverage)
    return sorted(set(opts))


def _leverage_label(lev: int, *, max_leverage: int) -> str:
    if lev == max_leverage and lev not in _LEV_PRESETS:
        return f"{lev}x (макс.)"
    if lev == max_leverage and lev < 10:
        return f"{lev}x (макс.)"
    return f"{lev}x"


def _leverage_kb(max_leverage: int, min_leverage: int) -> InlineKeyboardMarkup:
    opts = _leverage_options(max_leverage, min_leverage)
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for lev in opts:
        row.append(
            InlineKeyboardButton(
                text=_leverage_label(lev, max_leverage=max_leverage),
                callback_data=f"pump:pos:lev:{lev}",
            )
        )
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="pump:pos:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _go_to_leverage_step(
    message: Message, state: FSMContext, usd: float
) -> None:
    data = await state.get_data()
    symbol = str(data.get("symbol", "")).upper()
    risk = await asyncio.to_thread(
        BybitRest(category="linear").instrument_risk_info, symbol
    )

    await state.update_data(position_usd=usd)
    await state.set_state(PumpOpenPositionStates.leverage)

    await message.answer(
        f"Номинал: <b>${usd:.0f}</b>\n\n"
        f"Для <code>{symbol}</code> на Bybit доступно плечо "
        f"<b>{risk.min_leverage}–{risk.max_leverage}x</b>\n\n"
        "Выберите плечо или введите число:",
        parse_mode="HTML",
        reply_markup=_leverage_kb(risk.max_leverage, risk.min_leverage),
    )


async def _go_to_confirm_step(
    message: Message, state: FSMContext, lev: int
) -> None:
    data = await state.get_data()
    symbol = str(data.get("symbol", "")).upper()
    ema_label = str(data.get("ema_label", ""))
    entry = float(data.get("entry_price", 0))
    usd = float(data.get("position_usd", 0))
    order_mode = str(data.get("order_mode", "limit"))

    if order_mode == "market":
        mark = await asyncio.to_thread(BybitRest(category="linear").last_price, symbol)
        if not mark or mark <= 0:
            await message.answer(f"❌ Не удалось получить цену для <code>{symbol}</code>")
            return
        plan, err = await asyncio.to_thread(
            build_pump_short_market_plan,
            symbol=symbol,
            mark_price=float(mark),
            position_usd=usd,
            leverage=lev,
        )
    else:
        plan, err = await asyncio.to_thread(
            build_pump_short_plan,
            symbol=symbol,
            ema_label=ema_label,
            entry_price=entry,
            position_usd=usd,
            leverage=lev,
        )
    if plan is None:
        await message.answer(f"❌ {err}")
        return

    extra_lines: list[str] = []
    try:
        async with session_scope() as session:
            row = await get_pump_config(session)
            params: PumpScanParams = row.params()
        if params.atr_stop_sizing_enabled:
            def _hint_sync() -> str | None:
                client = BybitRest(category="linear")
                # ATR(1D)
                bars = client.get_kline_ohlcv(symbol, "D", limit=max(25, params.atr_period_1d + 5))
                if not bars:
                    return None
                bars.sort(key=lambda x: x[0])
                # closed bars only: use all except last if it is forming (heuristic by time is expensive here)
                # ATR with TR over last period
                trs: list[float] = []
                for i in range(1, len(bars)):
                    _ts, _o, h, l, c, _v = bars[i]
                    prev_c = bars[i - 1][4]
                    tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                    trs.append(float(tr))
                p = max(2, int(params.atr_period_1d))
                if len(trs) < p:
                    return None
                atr = sum(trs[-p:]) / p
                if atr <= 0:
                    return None

                # Approx local high from recent intraday bars (15m × 6 by default)
                iv = "15"
                intr = client.get_kline_ohlcv(symbol, iv, limit=12)
                intr.sort(key=lambda x: x[0])
                local_high = max(float(b[2]) for b in intr[-6:]) if intr else float(plan.entry_price)
                stop = float(local_high) + float(params.stop_atr_multiplier) * float(atr)
                risk = float(params.fixed_risk_usd)
                dist = abs(float(plan.entry_price) - stop)
                if dist <= 0:
                    return None
                qty = risk / dist
                notional = qty * float(plan.entry_price)
                return (
                    f"\n\n<b>ATR‑хелпер</b>\n"
                    f"Stop (оценка): <b>{stop:.5g}</b> "
                    f"(лок. high {local_high:.5g} + {params.stop_atr_multiplier:.2f}×ATR)\n"
                    f"Риск: <b>${risk:.0f}</b> → рекоменд. qty ~<b>{qty:.5g}</b> "
                    f"(~${notional:.0f} номинал)"
                )
            hint = await asyncio.to_thread(_hint_sync)
            if hint:
                extra_lines.append(hint)
    except Exception:
        log.exception("ATR sizing helper failed")

    await state.update_data(
        leverage=lev,
        plan_entry=plan.entry_str,
        plan_qty=plan.qty,
        plan_liq=plan.liq_str,
        plan_move_pct=plan.move_pct,
    )
    await state.set_state(PumpOpenPositionStates.confirm)

    text = format_plan_message(plan) + ("".join(extra_lines) if extra_lines else "")
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=_confirm_kb(),
    )


@router.callback_query(F.data.startswith("pump:pos:open:"))
async def pump_pos_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.from_user or not cb.data:
        await cb.answer()
        return
    symbol = cb.data.split(":", 3)[-1].upper()
    log.info("Pump limit wizard: user=%s symbol=%s", cb.from_user.id, symbol)

    emas = await asyncio.to_thread(
        compute_daily_emas, BybitRest(category="linear"), symbol
    )
    weekly = await asyncio.to_thread(
        compute_weekly_emas, BybitRest(category="linear"), symbol
    )
    if emas is None and weekly is None:
        await cb.answer("Нет данных EMA", show_alert=True)
        await send_pump_private(
            cb,
            f"❌ Не удалось загрузить EMA для <code>{symbol}</code>",
        )
        return

    ema_map: dict[str, float | None] = {}
    if emas is not None:
        ema_map.update({"50": emas.ema50, "100": emas.ema100, "200": emas.ema200})
    if weekly is not None:
        ema_map.update(weekly.as_label_map())
    if not any(v is not None for v in ema_map.values()):
        await cb.answer("Мало истории EMA", show_alert=True)
        return

    await state.clear()
    await state.update_data(symbol=symbol, ema_map=ema_map, order_mode="limit")
    await state.set_state(PumpOpenPositionStates.ema)

    await answer_pump_callback_continue(
        cb,
        text=(
            f"<b>Шорт {symbol}</b> после pump\n\n"
            "У какого уровня EMA открыть <b>limit Sell</b>?\n"
            "<i>1D: 50/100/200 · 1W: 7/14/28</i>"
        ),
        reply_markup=_ema_kb(ema_map),
    )


@router.callback_query(F.data.startswith("pump:pos:mkt:"))
async def pump_mkt_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.from_user or not cb.data:
        await cb.answer()
        return
    symbol = cb.data.split(":", 3)[-1].upper()

    await state.clear()
    await state.update_data(symbol=symbol, order_mode="market")
    await state.set_state(PumpOpenPositionStates.position_usd)

    await answer_pump_callback_continue(
        cb,
        text=(
            f"<b>Шорт {symbol}</b> по <b>market</b> после pump\n\n"
            "Выберите номинал или введите сумму в USDT:"
        ),
        reply_markup=_size_kb(),
    )


@router.callback_query(
    PumpOpenPositionStates.ema, F.data.startswith("pump:pos:ema:")
)
async def pump_pos_ema(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.data or not cb.message:
        await cb.answer()
        return
    label = cb.data.rsplit(":", 1)[-1].upper()
    if label not in _VALID_EMA_KEYS:
        await cb.answer("Неверный EMA", show_alert=True)
        return

    data = await state.get_data()
    ema_map = data.get("ema_map") or {}
    price = ema_map.get(label)
    if price is None:
        await cb.answer("EMA недоступна", show_alert=True)
        return

    symbol = str(data.get("symbol", "")).upper()
    entry_label = format_ema_entry_label(label)
    await state.update_data(ema_label=label, entry_price=float(price))
    await state.set_state(PumpOpenPositionStates.position_usd)
    await cb.answer()

    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(
        f"Вход: <b>{entry_label}</b> = <b>{float(price):.5g}</b>\n\n"
        "Выберите номинал или введите сумму в USDT:",
        parse_mode="HTML",
        reply_markup=_size_kb(),
    )


@router.callback_query(
    PumpOpenPositionStates.position_usd, F.data.startswith("pump:pos:usd:")
)
async def pump_pos_size_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.data or not cb.message:
        await cb.answer()
        return
    try:
        usd = float(cb.data.rsplit(":", 1)[-1])
        if usd <= 0:
            raise ValueError
    except ValueError:
        await cb.answer("Неверная сумма", show_alert=True)
        return
    await cb.answer()
    await _go_to_leverage_step(cb.message, state, usd)


@router.message(PumpOpenPositionStates.position_usd, F.text)
async def pump_pos_size(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        usd = float(raw)
        if usd <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите положительное число USDT, например 50")
        return

    await _go_to_leverage_step(message, state, usd)


@router.callback_query(
    PumpOpenPositionStates.leverage, F.data.startswith("pump:pos:lev:")
)
async def pump_pos_leverage_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.data or not cb.message:
        await cb.answer()
        return
    raw = cb.data.rsplit(":", 1)[-1]
    if not re.fullmatch(r"\d+", raw):
        await cb.answer("Неверное плечо", show_alert=True)
        return
    await cb.answer()
    await _go_to_confirm_step(cb.message, state, int(raw))


@router.message(PumpOpenPositionStates.leverage, F.text)
async def pump_pos_leverage(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not re.fullmatch(r"\d+", raw):
        await message.answer("Введите целое число, например 10")
        return

    await _go_to_confirm_step(message, state, int(raw))


@router.callback_query(PumpOpenPositionStates.confirm, F.data == "pump:pos:confirm:yes")
async def pump_pos_confirm_yes(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    symbol = str(data.get("symbol", "")).upper()
    order_mode = str(data.get("order_mode", "limit"))
    usd = float(data.get("position_usd", 0))
    lev = int(data.get("leverage", 1))

    if order_mode == "market":
        mark = await asyncio.to_thread(BybitRest(category="linear").last_price, symbol)
        if not mark or mark <= 0:
            await state.clear()
            await cb.answer("Нет цены", show_alert=True)
            return
        plan, err = await asyncio.to_thread(
            build_pump_short_market_plan,
            symbol=symbol,
            mark_price=float(mark),
            position_usd=usd,
            leverage=lev,
        )
    else:
        plan, err = await asyncio.to_thread(
            build_pump_short_plan,
            symbol=symbol,
            ema_label=str(data.get("ema_label", "")),
            entry_price=float(data.get("entry_price", 0)),
            position_usd=usd,
            leverage=lev,
        )
    await state.clear()
    if plan is None:
        await cb.answer()
        if cb.message:
            await cb.message.edit_reply_markup(reply_markup=None)
            await cb.message.answer(f"❌ {err}")
        return

    ok, msg, placed = await asyncio.to_thread(execute_pump_short_plan, plan)
    await cb.answer("Готово" if ok else "Ошибка", show_alert=not ok)
    if cb.message:
        await cb.message.edit_reply_markup(reply_markup=None)
        sent = await cb.message.answer(msg, parse_mode="HTML")
        if ok and placed and get_settings().bot_order_watch_enabled and cb.from_user:
            async with session_scope() as session:
                await create_bot_order_watch(
                    session,
                    telegram_chat_id=sent.chat.id,
                    telegram_message_id=sent.message_id,
                    telegram_user_id=cb.from_user.id,
                    bybit_order_id=placed.order_id,
                    symbol=placed.symbol,
                    side=placed.side,
                    order_type=placed.order_type,
                    qty=placed.qty,
                    price=placed.price,
                    order_status=placed.status,
                    cum_exec_qty=placed.cum_exec_qty,
                    avg_price=placed.avg_price,
                )
                await session.commit()


@router.callback_query(F.data == "pump:pos:confirm:no")
async def pump_pos_confirm_no(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer("Отменено")
    if cb.message:
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.answer("Открытие позиции отменено.")


@router.callback_query(F.data == "pump:pos:cancel")
async def pump_pos_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer("Отменено")
    if cb.message:
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.answer("Мастер отменён.")


@router.message(PumpOpenPositionStates.position_usd)
@router.message(PumpOpenPositionStates.leverage)
async def pump_pos_need_text(message: Message) -> None:
    await message.answer("Выберите кнопку или введите число текстом.")


@router.message(Command("тест_ордер", "test_order"))
async def cmd_test_order(message: Message) -> None:
    """Отправить актуальный pump-алерт с графиком и кнопками ордеров (в личку)."""
    parts = (message.text or "").split(maxsplit=1)
    symbol = parts[1].strip().upper() if len(parts) > 1 else None

    await message.answer("⏳ Сканирую пул на pump-импульсы…")
    hit = await find_best_current_pump_hit(symbol)
    if hit is None:
        hint = f" для <code>{symbol}</code>" if symbol else ""
        await message.answer(
            f"Сейчас нет активного pump-импульса{hint}.\n"
            "Попробуйте позже или укажите символ: "
            "<code>/тест_ордер SYMBOLUSDT</code>",
            parse_mode="HTML",
        )
        return

    msg, chart_png = await build_pump_impulse_alert_bundle(hit, test_prefix=True)
    kb = pump_alert_keyboard(hit.symbol)
    if chart_png:
        await message.answer_photo(
            BufferedInputFile(chart_png, filename=f"{hit.symbol}_charts.png"),
            caption=msg,
            parse_mode="HTML",
            reply_markup=kb,
        )
    else:
        await message.answer(msg, parse_mode="HTML", reply_markup=kb)
