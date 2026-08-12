"""Проверки и market-вход ATR Pullback (linear)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.atr_pullback.tasks import AtrPullbackTask
from app.bybit.rest import BybitRest
from app.services.sl_follow_logic import round_sl_price

log = logging.getLogger(__name__)


@dataclass
class EntryCheckResult:
    ok: bool
    reason: str
    qty: str | None = None
    sl_str: str | None = None
    margin_usd: float | None = None


def estimated_liquidation(side: str, mark: float, leverage: int) -> float:
    """Оценка liq до открытия позиции (консервативно)."""
    mm = 0.005
    inv = (1.0 / max(leverage, 1)) * (1.0 - mm)
    if side == "Buy":
        return mark * (1.0 - inv)
    return mark * (1.0 + inv)


def sl_vs_liquidation_ok(side: str, sl_price: float, liq: float | None) -> bool:
    if liq is None or liq <= 0:
        return True
    if side == "Buy":
        return sl_price > liq
    return sl_price < liq


def validate_entry(
    task: AtrPullbackTask,
    *,
    client: BybitRest,
    side: str,
    sl_price: float,
    mark_price: float,
) -> EntryCheckResult:
    if not task.auto_trade:
        return EntryCheckResult(False, "автоторговля выключена")
    if task.position_usd <= 0:
        return EntryCheckResult(False, "номинал позиции не задан")
    if task.leverage < 1:
        return EntryCheckResult(False, "плечо < 1")

    risk = client.instrument_risk_info(task.symbol)
    if task.leverage > risk.max_leverage:
        return EntryCheckResult(
            False,
            f"плечо {task.leverage} > макс. {risk.max_leverage} для {task.symbol}",
        )

    margin = task.position_usd / task.leverage
    avail = client.get_usdt_available_balance()
    if avail < margin * 0.99:
        return EntryCheckResult(
            False,
            f"недостаточно USDT: нужно ~${margin:.2f}, доступно ${avail:.2f}",
        )

    pos_side, pos_qty = client.get_open_position_side_qty(task.symbol)
    if pos_side is not None and float(pos_qty or 0) > 0:
        if pos_side != side:
            return EntryCheckResult(
                False,
                f"уже открыта противоположная позиция ({pos_side})",
            )
        return EntryCheckResult(False, "позиция по символу уже открыта")

    snap = client.get_linear_position_snapshot(task.symbol)
    liq = snap.liquidation_price if snap else None
    if not sl_vs_liquidation_ok(side, sl_price, liq):
        liq_s = f"{liq:g}" if liq else "?"
        return EntryCheckResult(
            False,
            f"SL {sl_price:g} не безопаснее ликвидации ({liq_s})",
        )

    try:
        qty = client.qty_from_notional_usd(task.symbol, task.position_usd, mark_price)
    except ValueError as e:
        return EntryCheckResult(False, str(e))

    if float(qty) < float(risk.min_order_qty):
        return EntryCheckResult(
            False,
            f"qty {qty} < мин. {risk.min_order_qty}",
        )

    sl_str = round_sl_price(client, task.symbol, sl_price)
    return EntryCheckResult(
        True,
        "ok",
        qty=qty,
        sl_str=sl_str,
        margin_usd=margin,
    )


def execute_entry(
    task: AtrPullbackTask,
    *,
    client: BybitRest,
    side: str,
    sl_price: float,
    mark_price: float,
) -> tuple[bool, str]:
    check = validate_entry(
        task, client=client, side=side, sl_price=sl_price, mark_price=mark_price
    )
    if not check.ok:
        return False, check.reason

    try:
        client.set_symbol_leverage(task.symbol, task.leverage)
    except Exception as e:
        log.warning("set_leverage %s %sx: %s", task.symbol, task.leverage, e)
        return False, f"не удалось выставить плечо: {e}"

    assert check.sl_str
    if check.reason == "позиция уже открыта":
        try:
            client.set_position_stop_loss(task.symbol, check.sl_str)
        except Exception as e:
            return False, f"не удалось обновить SL: {e}"
        return True, f"позиция уже была · SL → {check.sl_str}"

    assert check.qty
    try:
        client.place_market_with_sl(
            task.symbol,
            side,
            check.qty,
            check.sl_str,
        )
    except Exception as e:
        log.exception("place_market_with_sl %s", task.symbol)
        return False, f"ордер не принят: {e}"

    return True, (
        f"market {side} qty={check.qty} · номинал ~${task.position_usd:.0f} · "
        f"плечо {task.leverage}x · маржа ~${check.margin_usd:.2f} · SL {check.sl_str}"
    )
