"""Telegram: слежение до окна входа (entry watch)."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.pump_dm import send_pump_private
from app.config import get_settings
from app.db.session import session_scope
from app.pump_scan.entry_watch_plan import format_plan_summary
from app.repository.pump_entry_watch import (
    fetch_user_entry_watches,
    set_entry_watch_status,
)
from app.services.pump_entry_watch import create_watch_from_context

log = logging.getLogger(__name__)
router = Router()


def _watches_list_kb(watches) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for w in watches:
        if w.status != "active":
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔕 #{w.id} {w.symbol}",
                    callback_data=f"pump:watch:off:{w.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="« Закрыть", callback_data="pump:watch:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_confirm(cb: CallbackQuery, msg: str) -> None:
    """Явное подтверждение reply к алерту (видно в ленте)."""
    if not cb.message:
        return
    try:
        await cb.message.reply(msg, parse_mode="HTML")
        return
    except Exception:
        log.exception("entry_watch confirm reply failed")
    try:
        await cb.message.answer(msg, parse_mode="HTML")
    except Exception:
        log.exception("entry_watch confirm answer failed")
        # last resort: личка
        try:
            await send_pump_private(cb, msg)
        except Exception:
            log.exception("entry_watch confirm DM failed")


@router.callback_query(F.data.startswith("pump:watch:add:"))
async def watch_add(cb: CallbackQuery) -> None:
    if not cb.from_user or not cb.data:
        await cb.answer()
        return
    if not get_settings().pump_entry_watch_enabled:
        await cb.answer("Слежение выключено", show_alert=True)
        return
    symbol = cb.data.split(":", 3)[3].upper()
    if not symbol.endswith("USDT"):
        symbol = f"{symbol}USDT"

    # Сразу закрываем «часики» на кнопке — иначе клик выглядит мёртвым
    try:
        await cb.answer(f"Ставлю {symbol} на слежение…")
    except Exception:
        log.debug("cb.answer early failed", exc_info=True)

    source_chat_id = None
    source_message_id = None
    alert_text = ""
    if cb.message:
        source_chat_id = cb.message.chat.id
        source_message_id = cb.message.message_id
        # у фото caption; у текста — text/html_text
        alert_text = (
            getattr(cb.message, "html_text", None)
            or cb.message.caption
            or cb.message.text
            or ""
        )

    notify_chat = cb.from_user.id
    try:
        ok, msg = await create_watch_from_context(
            telegram_user_id=cb.from_user.id,
            telegram_chat_id=notify_chat,
            symbol=symbol,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            alert_text=alert_text[:4000],
        )
    except Exception:
        log.exception("create_watch_from_context failed %s", symbol)
        # второй answer уже нельзя — шлём сообщение
        if cb.message:
            await cb.message.reply(
                f"❌ Не удалось поставить <code>{html.escape(symbol)}</code> на слежение. "
                f"Смотрите логи.",
                parse_mode="HTML",
            )
        return

    await _send_confirm(cb, msg if ok else f"❌ {msg}")
    log.info(
        "entry_watch button user=%s %s ok=%s",
        cb.from_user.id,
        symbol,
        ok,
    )


@router.callback_query(F.data.startswith("pump:watch:off:"))
async def watch_off(cb: CallbackQuery) -> None:
    if not cb.from_user or not cb.data:
        await cb.answer()
        return
    try:
        wid = int(cb.data.rsplit(":", 1)[-1])
    except ValueError:
        await cb.answer()
        return
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(tz=ZoneInfo("Europe/Moscow"))
    async with session_scope() as session:
        ok = await set_entry_watch_status(
            session,
            wid,
            status="cancelled",
            user_id=cb.from_user.id,
            note="Снято пользователем",
            completed_at=now,
        )
        await session.commit()
        watches = await fetch_user_entry_watches(
            session, cb.from_user.id, active_only=True
        )
    if not ok:
        await cb.answer("Не найдено", show_alert=True)
        return
    await cb.answer(f"Снято #{wid}", show_alert=True)
    if cb.message:
        try:
            if watches:
                await cb.message.edit_text(
                    _format_watches_list(watches),
                    parse_mode="HTML",
                    reply_markup=_watches_list_kb(watches),
                )
            else:
                await cb.message.edit_text("Активных слежений нет.", reply_markup=None)
        except Exception:
            pass


@router.callback_query(F.data == "pump:watch:cancel")
async def watch_cancel(cb: CallbackQuery) -> None:
    await cb.answer()
    if cb.message:
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


def _format_watches_list(watches) -> str:
    import json

    lines = ["<b>👀 Вотчлист — слежение до входа</b>\n"]
    for w in watches:
        plan = {}
        try:
            plan = json.loads(w.watch_plan_json or "{}")
        except Exception:
            pass
        exp = w.expires_at.strftime("%d.%m %H:%M") if w.expires_at else "?"
        lines.append(
            f"#{w.id} <code>{html.escape(w.symbol)}</code> · до {exp} MSK\n"
            f"<i>{html.escape(format_plan_summary(plan))}</i>"
        )
    lines.append("\nНажмите 🔕 чтобы снять · /pump_watches")
    return "\n".join(lines)


@router.message(Command("pump_watches"))
async def cmd_pump_watches(message: Message) -> None:
    if not message.from_user:
        return
    if not get_settings().pump_entry_watch_enabled:
        await message.answer("Слежение выключено.")
        return
    uid = message.from_user.id
    async with session_scope() as session:
        watches = await fetch_user_entry_watches(session, uid, active_only=True)
    if not watches:
        await message.answer(
            "Активных слежений нет.\n"
            "Кнопка «👀 Следить до входа» под pump-алертом."
        )
        return
    await message.answer(
        _format_watches_list(watches),
        parse_mode="HTML",
        reply_markup=_watches_list_kb(watches),
    )
