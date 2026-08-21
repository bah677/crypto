"""Меню вкл/выкл автоотправки алертов (SL EMA, скачки цены, funding)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import get_settings
from app.db.session import session_scope
from app.repository.alerts_flags import (
    get_alerts_flags,
    set_ema_sl_reports,
    set_funding_reports,
    set_price_spike_reports,
)
from app.services.alert_toggles import (
    ema_sl_env_allowed,
    funding_env_allowed,
    price_spike_env_allowed,
)

router = Router()


def _toggle_btn(label: str, on: bool, callback: str) -> InlineKeyboardButton:
    icon = "🟢" if on else "🔴"
    action = "выкл" if on else "вкл"
    return InlineKeyboardButton(
        text=f"{icon} {label} — {action}",
        callback_data=callback,
    )


def _env_off_btn(label: str, noop_key: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=f"⚪️ {label} — выкл в .env",
        callback_data=f"alerts:noop:{noop_key}",
    )


def alerts_menu_kb(
    *,
    ema_sl_on: bool,
    spike_on: bool,
    funding_on: bool,
    ema_sl_env: bool,
    spike_env: bool,
    funding_env: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if ema_sl_env:
        rows.append([_toggle_btn("SL EMA в топик", ema_sl_on, "alerts:toggle:ema_sl")])
    else:
        rows.append([_env_off_btn("SL EMA", "ema_sl")])
    if spike_env:
        rows.append([_toggle_btn("Скачки цены", spike_on, "alerts:toggle:spike")])
    else:
        rows.append([_env_off_btn("Скачки", "spike")])
    if funding_env:
        rows.append([_toggle_btn("Funding scan", funding_on, "alerts:toggle:funding")])
    else:
        rows.append([_env_off_btn("Funding", "funding")])
    rows.append([InlineKeyboardButton(text="« Меню", callback_data="task:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_alerts_menu_text(
    ema_sl_on: bool,
    spike_on: bool,
    funding_on: bool,
    ema_sl_env: bool,
    spike_env: bool,
    funding_env: bool,
) -> str:
    def line(name: str, on: bool, env_ok: bool) -> str:
        if not env_ok:
            return f"{name}: <b>недоступно</b> (<code>…_ENABLED=0</code> в .env)"
        return f"{name}: <b>{'вкл' if on else 'выкл'}</b>"

    return (
        "<b>🔔 Алерты в группу</b>\n\n"
        f"{line('Отчёт SL EMA', ema_sl_on, ema_sl_env)}\n"
        f"{line('Скачки цены (1m)', spike_on, spike_env)}\n"
        f"{line('Funding scan (:55 MSK)', funding_on, funding_env)}\n\n"
        "Переключатели действуют сразу, без перезапуска.\n"
        "Ручной <code>/funding_scan</code> работает всегда.\n"
        "Сигналы EMA и автоследование SL на Bybit — отдельно."
    )


async def _menu_state() -> tuple:
    s = get_settings()
    async with session_scope() as session:
        flags = await get_alerts_flags(session)
    return (
        flags.ema_sl_reports,
        flags.price_spike_reports,
        flags.funding_reports,
        ema_sl_env_allowed(s),
        price_spike_env_allowed(s),
        funding_env_allowed(s),
    )


async def _send_alerts_menu(target: Message) -> None:
    ema, spike, fund, ema_e, spike_e, fund_e = await _menu_state()
    await target.answer(
        _build_alerts_menu_text(ema, spike, fund, ema_e, spike_e, fund_e),
        reply_markup=alerts_menu_kb(
            ema_sl_on=ema,
            spike_on=spike,
            funding_on=fund,
            ema_sl_env=ema_e,
            spike_env=spike_e,
            funding_env=fund_e,
        ),
    )


@router.message(Command("alerts"))
async def cmd_alerts(message: Message) -> None:
    await _send_alerts_menu(message)


async def _refresh_alerts_menu(callback: CallbackQuery) -> None:
    ema, spike, fund, ema_e, spike_e, fund_e = await _menu_state()
    await callback.message.edit_text(
        _build_alerts_menu_text(ema, spike, fund, ema_e, spike_e, fund_e),
        reply_markup=alerts_menu_kb(
            ema_sl_on=ema,
            spike_on=spike,
            funding_on=fund,
            ema_sl_env=ema_e,
            spike_env=spike_e,
            funding_env=fund_e,
        ),
    )


@router.callback_query(F.data == "alerts:menu")
async def cb_alerts_menu(callback: CallbackQuery) -> None:
    await _refresh_alerts_menu(callback)
    await callback.answer()


@router.callback_query(F.data == "alerts:toggle:ema_sl")
async def cb_toggle_ema_sl(callback: CallbackQuery) -> None:
    if not ema_sl_env_allowed():
        await callback.answer("SL EMA выключен в .env", show_alert=True)
        return
    async with session_scope() as session:
        flags = await get_alerts_flags(session)
        row = await set_ema_sl_reports(session, not flags.ema_sl_reports)
    await callback.answer(f"SL EMA: {'вкл' if row.ema_sl_reports else 'выкл'}")
    await _refresh_alerts_menu(callback)


@router.callback_query(F.data == "alerts:toggle:spike")
async def cb_toggle_spike(callback: CallbackQuery) -> None:
    if not price_spike_env_allowed():
        await callback.answer("Скачки выключены в .env", show_alert=True)
        return
    async with session_scope() as session:
        flags = await get_alerts_flags(session)
        row = await set_price_spike_reports(session, not flags.price_spike_reports)
    await callback.answer(f"Скачки: {'вкл' if row.price_spike_reports else 'выкл'}")
    await _refresh_alerts_menu(callback)


@router.callback_query(F.data == "alerts:toggle:funding")
async def cb_toggle_funding(callback: CallbackQuery) -> None:
    if not funding_env_allowed():
        await callback.answer("Funding выключен в .env", show_alert=True)
        return
    async with session_scope() as session:
        flags = await get_alerts_flags(session)
        row = await set_funding_reports(session, not flags.funding_reports)
    await callback.answer(f"Funding: {'вкл' if row.funding_reports else 'выкл'}")
    await _refresh_alerts_menu(callback)


@router.callback_query(F.data.startswith("alerts:noop:"))
async def cb_alerts_noop(callback: CallbackQuery) -> None:
    await callback.answer(
        "Включите в .env: FUNDING_SCAN_ENABLED, EMA_SL_MONITOR_ENABLED, "
        "PRICE_SPIKE_MONITOR_ENABLED",
        show_alert=True,
    )
