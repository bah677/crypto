"""Правила переноса SL: сравнение с Bybit, запрет расширения риска."""

from __future__ import annotations

from app.bybit.rest import BybitRest, LinearPositionSnapshot
from app.services.ema_sl_levels import SlTfMode


def sl_distance_usd(pos: LinearPositionSnapshot, sl_price: float) -> float:
    """Дистанция SL от mark в $ (абсолютная)."""
    if pos.side == "Buy":
        return max(0.0, pos.mark_price - sl_price)
    return max(0.0, sl_price - pos.mark_price)


def sl_is_valid_for_side(pos: LinearPositionSnapshot, sl_price: float) -> bool:
    if pos.side == "Buy":
        return sl_price < pos.mark_price
    return sl_price > pos.mark_price


def should_update_stop_loss(
    pos: LinearPositionSnapshot,
    new_sl: float,
    *,
    allow_sl_widen: bool,
    min_move_usd: float = 0.01,
) -> tuple[bool, str]:
    """
    Решение о переносе SL.
    Увеличение дистанции = расширение риска; без allow_sl_widen не двигаем.
    """
    if not sl_is_valid_for_side(pos, new_sl):
        return False, "расчётный SL с неверной стороны от цены"

    new_dist = sl_distance_usd(pos, new_sl)
    if pos.stop_loss is None:
        return True, "SL на бирже не задан — ставим расчётный"

    cur_dist = sl_distance_usd(pos, pos.stop_loss)
    if abs(new_sl - pos.stop_loss) < min_move_usd:
        return False, "изменение меньше порога"

    if new_dist > cur_dist + min_move_usd:
        if allow_sl_widen:
            return True, f"расширение SL ${cur_dist:.2f} → ${new_dist:.2f}"
        return False, (
            f"расширение запрещено (${cur_dist:.2f} → ${new_dist:.2f}), ждём ужесточение"
        )

    if new_dist < cur_dist - min_move_usd:
        return True, f"ужесточение SL ${cur_dist:.2f} → ${new_dist:.2f}"

    return False, "дистанция SL без существенного изменения"


def format_sl_follow_summary(
    symbol: str,
    side: str,
    tf_label: str,
    task_line: str,
    allow_widen: bool,
) -> str:
    widen = "да" if allow_widen else "нет"
    side_ru = "Long" if side == "Buy" else "Short"
    return (
        f"<b>Автоследование SL</b>\n"
        f"Символ: <code>{symbol}</code> · {side_ru}\n"
        f"ТФ: <b>{tf_label}</b>\n"
        f"Задание: {task_line}\n"
        f"Разрешить увеличение SL (расширение риска): <b>{widen}</b>\n\n"
        "При закрытии каждой новой свечи выбранного ТФ бот переставит SL на Bybit "
        "по расчёту EMA cross. Текущий SL всегда читается с биржи."
    )


def format_move_report(
    symbol: str,
    tf_label: str,
    old_sl: float | None,
    new_sl: float,
    reason: str,
    *,
    skipped: bool = False,
) -> str:
    old_s = f"{old_sl:g}" if old_sl is not None else "—"
    action = "пропуск" if skipped else "перенос SL"
    return (
        f"<b>SL follow · {action}</b> · <code>{symbol}</code> · {tf_label}\n"
        f"Было: {old_s} → <b>{new_sl:g}</b>\n"
        f"{reason}"
    )


def round_sl_price(client: BybitRest, symbol: str, price: float) -> str:
    tick, _ = client.instrument_filters(symbol)
    return BybitRest.round_to_tick(price, tick)
