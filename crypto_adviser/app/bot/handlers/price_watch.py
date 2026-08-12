"""Мониторинг скачков цены: ручной список + открытые linear-позиции."""

from __future__ import annotations

import asyncio

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bybit.rest import BybitRest
from app.config import get_settings
from app.db.session import session_scope
from app.repository.price_watch import (
    add_price_watch,
    delete_price_watch,
    fetch_all_price_watch,
    fetch_enabled_price_watch,
    set_price_watch_enabled,
)

router = Router()


def _parse_watch_add(text: str) -> tuple[str, str]:
    parts = text.strip().split(maxsplit=1)
    if not parts:
        raise ValueError("Укажите тикер: <code>/watch_add BTCUSDT</code> или с псевдонимом.")
    sym = parts[0].upper().replace("/", "")
    if not sym.isalnum():
        raise ValueError("Тикер: только буквы и цифры, например BTCUSDT.")
    alias = parts[1].strip() if len(parts) > 1 else ""
    return sym, alias


@router.message(Command("watch_list", "watch"))
async def cmd_watch_list(message: Message) -> None:
    s = get_settings()
    async with session_scope() as session:
        rows = await fetch_all_price_watch(session)

    try:
        open_syms = await asyncio.to_thread(
            BybitRest(category="linear").list_open_linear_symbols
        )
    except Exception as e:
        open_syms = []
        pos_block = f"⚠️ Позиции linear: ошибка ({e})\n"
    else:
        pos_block = (
            "Открытые позиции (linear, авто):\n"
            + ("\n".join(f"• <code>{x}</code>" for x in open_syms) if open_syms else "• нет")
            + "\n\n"
        )

    if rows:
        manual = "\n".join(
            f"{'🟢' if r.enabled else '⚪️'} <code>{r.symbol}</code>"
            + (f" · {r.alias}" if (r.alias or "").strip() else "")
            for r in rows
        )
    else:
        manual = "• пусто"

    await message.answer(
        "<b>Мониторинг скачков цены</b>\n"
        f"Порог: <b>{s.price_spike_ratio:g}×</b> среднего хода 1m за час · "
        f"пауза между алертами: <b>{s.price_spike_alert_cooldown_min}</b> мин\n\n"
        f"{pos_block}"
        f"<b>Ручной список</b> (/watch_add):\n{manual}\n\n"
        "<code>/watch_add SYMBOL</code> или <code>/watch_add LABUSDT LAB</code>\n"
        "<code>/watch_off SYMBOL</code> · <code>/watch_on SYMBOL</code>\n"
        "<code>/watch_del SYMBOL</code> — удалить из списка",
    )


@router.message(Command("watch_add"))
async def cmd_watch_add(message: Message) -> None:
    raw = (message.text or "").split(maxsplit=1)
    if len(raw) < 2:
        await message.answer(
            "Формат: <code>/watch_add BTCUSDT</code> или "
            "<code>/watch_add LABUSDT LAB</code>"
        )
        return
    try:
        sym, alias = _parse_watch_add(raw[1])
    except ValueError as e:
        await message.answer(str(e))
        return

    try:
        await asyncio.to_thread(
            BybitRest(category="linear").instrument_filters, sym
        )
    except Exception as e:
        await message.answer(f"Тикер {sym} не найден на linear: {e}")
        return

    async with session_scope() as session:
        row = await add_price_watch(session, symbol=sym, alias=alias)

    label = row.display_name()
    await message.answer(
        f"✅ Добавлен мониторинг <b>{label}</b> (linear).\n"
        "Считается вместе с открытыми позициями; позиция не обязательна."
    )


@router.message(Command("watch_off"))
async def cmd_watch_off(message: Message) -> None:
    raw = (message.text or "").split(maxsplit=1)
    if len(raw) < 2:
        await message.answer("Формат: <code>/watch_off BTCUSDT</code>")
        return
    sym = raw[1].upper().strip()
    async with session_scope() as session:
        row = await set_price_watch_enabled(session, sym, False)
    if row is None:
        await message.answer(f"Нет в списке: <code>{sym}</code>")
        return
    await message.answer(f"⚪️ Мониторинг <code>{sym}</code> выключен (запись в БД осталась).")


@router.message(Command("watch_on"))
async def cmd_watch_on(message: Message) -> None:
    raw = (message.text or "").split(maxsplit=1)
    if len(raw) < 2:
        await message.answer("Формат: <code>/watch_on BTCUSDT</code>")
        return
    sym = raw[1].upper().strip()
    async with session_scope() as session:
        row = await set_price_watch_enabled(session, sym, True)
    if row is None:
        await message.answer(
            f"Нет в списке. Добавьте: <code>/watch_add {sym}</code>"
        )
        return
    await message.answer(f"🟢 Мониторинг <code>{sym}</code> включён.")


@router.message(Command("watch_del", "watch_remove"))
async def cmd_watch_del(message: Message) -> None:
    raw = (message.text or "").split(maxsplit=1)
    if len(raw) < 2:
        await message.answer("Формат: <code>/watch_del BTCUSDT</code>")
        return
    sym = raw[1].upper().strip()
    async with session_scope() as session:
        ok = await delete_price_watch(session, sym)
    if not ok:
        await message.answer(f"Нет в списке: <code>{sym}</code>")
        return
    await message.answer(f"🗑 Удалён из мониторинга: <code>{sym}</code>")
