"""Открытие шорта по EMA 1D/1W из pump-алерта (мастер в личке)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.bybit.rest import BybitRest
from app.pump_scan.weekly_ema import format_ema_entry_label

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PumpPositionPlan:
    symbol: str
    ema_label: str
    entry_price: float
    entry_str: str
    position_usd: float
    leverage: int
    qty: str
    liq_price: float
    liq_str: str
    move_pct: float
    margin_usd: float
    max_leverage: int
    order_mode: str = "limit"  # limit | market
    account_equity_usd: float = 0.0
    account_available_usd: float = 0.0


@dataclass(frozen=True)
class PlacedOrderInfo:
    order_id: str
    status: str
    symbol: str
    side: str
    order_type: str
    qty: str
    price: str
    cum_exec_qty: str = "0"
    avg_price: str = ""


def estimated_cross_liquidation_short(
    entry: float,
    qty: float,
    account_equity: float,
    *,
    mm_rate: float = 0.005,
) -> float:
    """Оценка liq шорта при cross: обеспечение = equity всего UNIFIED счёта."""
    q = float(qty)
    if entry <= 0 or q <= 0 or account_equity <= 0:
        return entry * 1.5
    move = (account_equity / q) * (1.0 - mm_rate)
    return entry + move


def _build_pump_short_plan_common(
    *,
    symbol: str,
    ema_label: str,
    entry_price: float,
    position_usd: float,
    leverage: int,
    order_mode: str,
    client: BybitRest,
) -> tuple[PumpPositionPlan | None, str]:
    sym = symbol.upper()
    if entry_price <= 0:
        return None, "Некорректная цена входа"
    if position_usd <= 0:
        return None, "Номинал позиции должен быть > 0"
    if leverage < 1:
        return None, "Плечо должно быть ≥ 1"

    risk = client.instrument_risk_info(sym)
    if leverage > risk.max_leverage:
        return None, f"Плечо {leverage} > макс. {risk.max_leverage} для {sym}"
    if leverage < risk.min_leverage:
        return None, f"Плечо {leverage} < мин. {risk.min_leverage} для {sym}"

    wallet = client.get_unified_wallet_snapshot()
    position_im = position_usd / leverage
    if wallet.total_available_balance < position_im * 0.99:
        return None, (
            f"Недостаточно свободной маржи (cross): нужно ~${position_im:.2f} IM, "
            f"доступно ${wallet.total_available_balance:.2f}"
        )

    pos_side, pos_qty = client.get_open_position_side_qty(sym)
    if pos_side is not None and float(pos_qty or 0) > 0:
        if pos_side != "Sell":
            return None, f"Уже открыта позиция {pos_side} — закройте перед шортом"
        return None, "Позиция по символу уже открыта"

    try:
        qty = client.qty_from_notional_usd(sym, position_usd, entry_price)
    except ValueError as e:
        return None, str(e)

    if float(qty) < float(risk.min_order_qty):
        return None, f"Qty {qty} < мин. {risk.min_order_qty}"

    entry_str = BybitRest.round_to_tick(entry_price, risk.tick_size)
    entry_f = float(entry_str)
    liq = estimated_cross_liquidation_short(
        entry_f, float(qty), wallet.total_equity
    )
    liq_str = BybitRest.round_to_tick(liq, risk.tick_size)
    liq_f = float(liq_str)
    move_pct = (liq_f - entry_f) / entry_f * 100.0 if entry_f > 0 else 0.0

    return (
        PumpPositionPlan(
            symbol=sym,
            ema_label=ema_label,
            entry_price=entry_f,
            entry_str=entry_str,
            position_usd=position_usd,
            leverage=leverage,
            qty=qty,
            liq_price=liq_f,
            liq_str=liq_str,
            move_pct=move_pct,
            margin_usd=position_im,
            max_leverage=risk.max_leverage,
            order_mode=order_mode,
            account_equity_usd=wallet.total_equity,
            account_available_usd=wallet.total_available_balance,
        ),
        "",
    )


def build_pump_short_plan(
    *,
    symbol: str,
    ema_label: str,
    entry_price: float,
    position_usd: float,
    leverage: int,
    client: BybitRest | None = None,
) -> tuple[PumpPositionPlan | None, str]:
    """Шорт (limit Sell) на уровне EMA 1D."""
    client = client or BybitRest(category="linear")
    return _build_pump_short_plan_common(
        symbol=symbol,
        ema_label=ema_label,
        entry_price=entry_price,
        position_usd=position_usd,
        leverage=leverage,
        order_mode="limit",
        client=client,
    )


def build_pump_short_market_plan(
    *,
    symbol: str,
    mark_price: float,
    position_usd: float,
    leverage: int,
    client: BybitRest | None = None,
) -> tuple[PumpPositionPlan | None, str]:
    """Шорт (market Sell) по текущей цене."""
    client = client or BybitRest(category="linear")
    return _build_pump_short_plan_common(
        symbol=symbol,
        ema_label="",
        entry_price=mark_price,
        position_usd=position_usd,
        leverage=leverage,
        order_mode="market",
        client=client,
    )


def format_plan_message(plan: PumpPositionPlan) -> str:
    if plan.order_mode == "market":
        entry_line = f"Вход (market): <b>~{plan.entry_str}</b>"
        confirm = "Открыть market Sell?"
        title = f"<b>Шорт {plan.symbol}</b> · <b>market</b>"
    else:
        entry_line = f"Вход (limit): <b>{plan.entry_str}</b>"
        confirm = "Открыть limit Sell на уровне EMA?"
        title = f"<b>Шорт {plan.symbol}</b> · вход по <b>{format_ema_entry_label(plan.ema_label)}</b>"
    return (
        f"{title}\n"
        f"{entry_line}\n"
        f"Номинал: <b>${plan.position_usd:.0f}</b> · плечо <b>{plan.leverage}x</b> "
        f"(макс. {plan.max_leverage}x)\n"
        f"IM позиции: ~<b>${plan.margin_usd:.2f}</b> · qty <b>{plan.qty}</b>\n"
        f"Счёт (cross): equity <b>${plan.account_equity_usd:.2f}</b> · "
        f"свободно <b>${plan.account_available_usd:.2f}</b>\n"
        f"Ликвидация (cross, оценка): <b>~{plan.liq_str}</b> "
        f"(<b>+{plan.move_pct:.2f}%</b> от входа)\n\n"
        f"{confirm}"
    )


def _placed_order_info(
    client: BybitRest, plan: PumpPositionPlan, resp: dict
) -> PlacedOrderInfo | None:
    order_id = str((resp or {}).get("result", {}).get("orderId") or "").strip()
    if not order_id:
        return None
    order = client.get_linear_order(plan.symbol, order_id)
    if order:
        return PlacedOrderInfo(
            order_id=order_id,
            status=str(order.get("orderStatus") or "New"),
            symbol=plan.symbol,
            side="Sell",
            order_type=str(order.get("orderType") or plan.order_mode.title()),
            qty=str(order.get("qty") or plan.qty),
            price=str(order.get("price") or plan.entry_str),
            cum_exec_qty=str(order.get("cumExecQty") or "0"),
            avg_price=str(order.get("avgPrice") or ""),
        )
    return PlacedOrderInfo(
        order_id=order_id,
        status="New",
        symbol=plan.symbol,
        side="Sell",
        order_type="Market" if plan.order_mode == "market" else "Limit",
        qty=plan.qty,
        price=plan.entry_str,
    )


def execute_pump_short_plan(
    plan: PumpPositionPlan,
) -> tuple[bool, str, PlacedOrderInfo | None]:
    client = BybitRest(category="linear")
    try:
        client.set_symbol_leverage(plan.symbol, plan.leverage)
    except Exception as e:
        log.warning("pump open leverage %s: %s", plan.symbol, e)
        return False, f"Не удалось выставить плечо: {e}", None

    try:
        if plan.order_mode == "market":
            resp = client.place_market_order(plan.symbol, "Sell", plan.qty)
        else:
            resp = client.place_limit_order(
                plan.symbol,
                "Sell",
                plan.qty,
                plan.entry_str,
            )
    except Exception as e:
        log.exception("pump %s sell %s", plan.order_mode, plan.symbol)
        return False, f"Ордер не принят: {e}", None

    placed = _placed_order_info(client, plan, resp)

    if plan.order_mode == "market":
        return True, (
            f"✅ Market Sell <b>{plan.symbol}</b>\n"
            f"Оценка входа ~<b>{plan.entry_str}</b> · qty <b>{plan.qty}</b>\n"
            f"Номинал ~${plan.position_usd:.0f} · {plan.leverage}x · "
            f"IM ~${plan.margin_usd:.2f}\n"
            f"Ликвидация (cross) ~<b>{plan.liq_str}</b> (+{plan.move_pct:.2f}% от входа)"
        ), placed
    return True, (
        f"✅ Limit Sell <b>{plan.symbol}</b>\n"
        f"Цена <b>{plan.entry_str}</b> ({format_ema_entry_label(plan.ema_label)}) · qty <b>{plan.qty}</b>\n"
        f"Номинал ~${plan.position_usd:.0f} · {plan.leverage}x · IM ~${plan.margin_usd:.2f}\n"
        f"Ликвидация (cross) ~<b>{plan.liq_str}</b> (+{plan.move_pct:.2f}% от входа)"
    ), placed
