from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InputMediaPhoto

from app.config import get_settings

log = logging.getLogger(__name__)


async def _send_message(
    chat_id: int,
    text: str,
    *,
    parse_mode: ParseMode | None = ParseMode.HTML,
    message_thread_id: int | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    reply_to_message_id: int | None = None,
) -> int | None:
    s = get_settings()
    bot = Bot(s.telegram_bot_token)
    try:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text[:4000],
            parse_mode=parse_mode,
            message_thread_id=message_thread_id,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )
        return msg.message_id
    finally:
        await bot.session.close()


async def reply_to_chat_message(
    chat_id: int,
    reply_to_message_id: int,
    text: str,
) -> None:
    await _send_message(
        chat_id,
        text,
        reply_to_message_id=reply_to_message_id,
    )


async def _send_photo(
    chat_id: int,
    photo: bytes,
    caption: str,
    *,
    parse_mode: ParseMode | None = ParseMode.HTML,
    message_thread_id: int | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    filename: str = "chart.png",
) -> int | None:
    s = get_settings()
    bot = Bot(s.telegram_bot_token)
    try:
        msg = await bot.send_photo(
            chat_id=chat_id,
            photo=BufferedInputFile(photo, filename=filename),
            caption=caption[:1024],
            parse_mode=parse_mode,
            message_thread_id=message_thread_id,
            reply_markup=reply_markup,
        )
        return msg.message_id
    finally:
        await bot.session.close()


async def _send_media_group(
    chat_id: int,
    photos: list[bytes],
    caption: str,
    *,
    parse_mode: ParseMode | None = ParseMode.HTML,
    message_thread_id: int | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Альбом из 2+ фото с подписью на первом; кнопки — отдельным сообщением."""
    if not photos:
        raise ValueError("media group: пустой список фото")

    s = get_settings()
    bot = Bot(s.telegram_bot_token)
    try:
        media: list[InputMediaPhoto] = []
        for i, raw in enumerate(photos):
            if i == 0:
                media.append(
                    InputMediaPhoto(
                        media=BufferedInputFile(raw, filename=f"chart_{i + 1}.png"),
                        caption=caption[:1024],
                        parse_mode=parse_mode,
                    )
                )
            else:
                media.append(
                    InputMediaPhoto(
                        media=BufferedInputFile(raw, filename=f"chart_{i + 1}.png"),
                    )
                )
        await bot.send_media_group(
            chat_id=chat_id,
            media=media,
            message_thread_id=message_thread_id,
        )
        if reply_markup:
            await bot.send_message(
                chat_id=chat_id,
                text="👇 Открыть шорт:",
                parse_mode=parse_mode,
                message_thread_id=message_thread_id,
                reply_markup=reply_markup,
            )
    except Exception:
        log.exception(
            "send_media_group failed chat_id=%s photos=%s",
            chat_id,
            len(photos),
        )
        raise
    finally:
        await bot.session.close()


def _pump_alert_destination() -> tuple[int, int | None]:
    """Куда слать pump-алерты: временно в личку суперадмина или в топик группы."""
    s = get_settings()
    if s.pump_alerts_to_private:
        return s.superadmin_telegram_id, None
    if s.telegram_pump_channel_ready:
        return s.telegram_alerts_chat_id, s.telegram_alerts_topic_pump  # type: ignore[return-value]
    return s.superadmin_telegram_id, None


async def _deliver_pump_alert(
    text: str,
    *,
    parse_mode: ParseMode | None = ParseMode.HTML,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo: bytes | None = None,
) -> tuple[int, int | None]:
    chat_id, thread_id = _pump_alert_destination()
    msg_id: int | None = None
    if photo:
        msg_id = await _send_photo(
            chat_id,
            photo,
            text,
            parse_mode=parse_mode,
            message_thread_id=thread_id,
            reply_markup=reply_markup,
        )
    else:
        msg_id = await _send_message(
            chat_id,
            text,
            parse_mode=parse_mode,
            message_thread_id=thread_id,
            reply_markup=reply_markup,
        )
    return chat_id, msg_id


async def notify_superadmin(
    text: str, *, parse_mode: ParseMode | None = ParseMode.HTML
) -> None:
    """Личка суперадмина: ошибки, настройка, всё кроме торговых сигналов."""
    s = get_settings()
    await _send_message(s.superadmin_telegram_id, text, parse_mode=parse_mode)


async def notify_signals_channel(
    text: str, *, parse_mode: ParseMode | None = ParseMode.HTML
) -> None:
    """Топик «сигналы» в группе-форуме; иначе fallback в личку."""
    s = get_settings()
    if s.telegram_signals_channel_ready:
        await _send_message(
            s.telegram_alerts_chat_id,  # type: ignore[arg-type]
            text,
            parse_mode=parse_mode,
            message_thread_id=s.telegram_alerts_topic_signals,
        )
        return
    log.warning(
        "TELEGRAM_ALERTS_CHAT_ID / TELEGRAM_ALERTS_TOPIC_SIGNALS не заданы — "
        "сигнал EMA уходит в личку суперадмина"
    )
    await notify_superadmin(text, parse_mode=parse_mode)


async def notify_funding_channel(
    text: str, *, parse_mode: ParseMode | None = ParseMode.HTML
) -> None:
    """Топик «фандинг» в группе-форуме; иначе fallback в личку."""
    s = get_settings()
    if s.telegram_funding_channel_ready:
        await _send_message(
            s.telegram_alerts_chat_id,  # type: ignore[arg-type]
            text,
            parse_mode=parse_mode,
            message_thread_id=s.telegram_alerts_topic_funding,
        )
        return
    log.warning(
        "TELEGRAM_ALERTS_CHAT_ID / TELEGRAM_ALERTS_TOPIC_FUNDING не заданы — "
        "funding уходит в личку суперадмина"
    )
    await notify_superadmin(text, parse_mode=parse_mode)


async def notify_price_spike_channel(
    text: str, *, parse_mode: ParseMode | None = ParseMode.HTML
) -> None:
    """Топик «скачки цены» в группе-форуме; иначе fallback в личку."""
    s = get_settings()
    if s.telegram_price_spike_channel_ready:
        await _send_message(
            s.telegram_alerts_chat_id,  # type: ignore[arg-type]
            text,
            parse_mode=parse_mode,
            message_thread_id=s.telegram_alerts_topic_price_spike,
        )
        return
    log.warning(
        "TELEGRAM_ALERTS_TOPIC_PRICE_SPIKE не задан — скачок цены уходит в личку"
    )
    await notify_superadmin(text, parse_mode=parse_mode)


async def notify_sl_follow_channel(
    text: str, *, parse_mode: ParseMode | None = ParseMode.HTML
) -> None:
    """Топик «автоследование SL» (переносы и пропуски)."""
    s = get_settings()
    if s.telegram_sl_follow_channel_ready:
        await _send_message(
            s.telegram_alerts_chat_id,  # type: ignore[arg-type]
            text,
            parse_mode=parse_mode,
            message_thread_id=s.telegram_alerts_topic_sl_follow,
        )
        return
    log.warning(
        "TELEGRAM_ALERTS_TOPIC_SL_FOLLOW не задан — SL follow уходит в личку"
    )
    await notify_superadmin(text, parse_mode=parse_mode)


async def notify_ema_sl_channel(
    text: str, *, parse_mode: ParseMode | None = ParseMode.HTML
) -> None:
    """Топик «SL EMA» в группе-форуме; иначе fallback в личку."""
    s = get_settings()
    if s.telegram_ema_sl_channel_ready:
        await _send_message(
            s.telegram_alerts_chat_id,  # type: ignore[arg-type]
            text,
            parse_mode=parse_mode,
            message_thread_id=s.telegram_alerts_topic_ema_sl,
        )
        return
    log.warning("TELEGRAM_ALERTS_TOPIC_EMA_SL не задан — SL EMA уходит в личку")
    await notify_superadmin(text, parse_mode=parse_mode)


async def broadcast_pump_alert(
    text: str,
    *,
    parse_mode: ParseMode | None = ParseMode.HTML,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo: bytes | None = None,
) -> list[tuple[int, int | None]]:
    """Рассылка pump-алерта всем активным подписчикам. Возвращает (chat_id, message_id)."""
    import asyncio

    from app.db.session import session_scope
    from app.repository.subscribers import fetch_active_subscribers

    async with session_scope() as session:
        subscribers = await fetch_active_subscribers(session)

    if not subscribers:
        log.warning("broadcast_pump_alert: нет активных подписчиков")
        return []

    sent: list[tuple[int, int | None]] = []
    for sub in subscribers:
        try:
            if photo:
                msg_id = await _send_photo(
                    sub.telegram_chat_id,
                    photo,
                    text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            else:
                msg_id = await _send_message(
                    sub.telegram_chat_id,
                    text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            sent.append((sub.telegram_chat_id, msg_id))
        except Exception:
            log.exception(
                "broadcast_pump_alert failed user=%s chat=%s",
                sub.telegram_user_id,
                sub.telegram_chat_id,
            )
        await asyncio.sleep(0.05)
    return sent


async def notify_pump_channel(
    text: str,
    *,
    parse_mode: ParseMode | None = ParseMode.HTML,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo: bytes | None = None,
    photos: list[bytes] | None = None,
) -> list[tuple[int, int | None]]:
    """EMA-подписка: рассылка всем подписчикам."""
    try:
        return await broadcast_pump_alert(
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            photo=photo,
        )
    except Exception:
        log.exception("notify_pump_channel failed")
        raise


async def notify_dump_channel(
    text: str,
    *,
    parse_mode: ParseMode | None = ParseMode.HTML,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Топик «dump» в группе-форуме; иначе fallback в личку."""
    s = get_settings()
    if s.telegram_dump_channel_ready:
        await _send_message(
            s.telegram_alerts_chat_id,  # type: ignore[arg-type]
            text,
            parse_mode=parse_mode,
            message_thread_id=s.telegram_alerts_topic_dump,
            reply_markup=reply_markup,
        )
        return
    log.warning(
        "TELEGRAM_ALERTS_CHAT_ID / TELEGRAM_ALERTS_TOPIC_DUMP не заданы — "
        "dump alert уходит в личку суперадмина"
    )
    await _send_message(
        s.superadmin_telegram_id,
        text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )
