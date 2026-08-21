from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.repository.settings import GoldRuntimeSettings


def panel_keyboard(cfg: GoldRuntimeSettings) -> InlineKeyboardMarkup:
    toggle = "⏸ Выключить" if cfg.enabled else "▶️ Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle,
                    callback_data="gold:toggle",
                )
            ],
            [
                InlineKeyboardButton(text="× тело −", callback_data="gold:mult:-"),
                InlineKeyboardButton(
                    text=f"×{cfg.body_mult:g}",
                    callback_data="gold:noop",
                ),
                InlineKeyboardButton(text="× тело +", callback_data="gold:mult:+"),
            ],
            [
                InlineKeyboardButton(text="окно −", callback_data="gold:look:-"),
                InlineKeyboardButton(
                    text=f"{cfg.lookback} свечей",
                    callback_data="gold:noop",
                ),
                InlineKeyboardButton(text="окно +", callback_data="gold:look:+"),
            ],
            [
                InlineKeyboardButton(text="TTL кеша −", callback_data="gold:ttl:-"),
                InlineKeyboardButton(
                    text=f"{cfg.settings_cache_ttl_sec}с",
                    callback_data="gold:noop",
                ),
                InlineKeyboardButton(text="TTL кеша +", callback_data="gold:ttl:+"),
            ],
            [
                InlineKeyboardButton(text="🔄 Обновить панель", callback_data="gold:refresh"),
                InlineKeyboardButton(text="🧪 Скан сейчас", callback_data="gold:scan_now"),
            ],
            [
                InlineKeyboardButton(
                    text=f"📈 График {cfg.lookback} M1",
                    callback_data="gold:chart",
                ),
            ],
        ]
    )


def format_status(cfg: GoldRuntimeSettings, *, generation: int) -> str:
    state = "🟢 включён" if cfg.enabled else "🔴 выключен"
    return (
        f"<b>Gold Adviser</b> · XAU M1 (Bybit perp → spot fallback)\n"
        f"Статус: <b>{state}</b>\n"
        f"Порог тела: <b>×{cfg.body_mult:g}</b> от среднего\n"
        f"Окно: <b>{cfg.lookback}</b> свечей\n"
        f"TTL кеша настроек: <b>{cfg.settings_cache_ttl_sec}с</b> "
        f"(gen={generation})\n\n"
        f"Скан: ~3с после close M1, опрос ~5с (Bybit public kline).\n"
        f"Провайдеры: Bybit XAUUSDT → Twelve Data → RealMarket.\n"
        f"Цены Bybit ≠ спот, геометрия свечей обычно совпадает.\n"
        f"Изменения через панель сразу пушатся в кеш — рестарт не нужен."
    )
