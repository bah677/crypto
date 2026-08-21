from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)


def tick_size(mt5: Any, symbol: str) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise ValueError(f"symbol_info is None for {symbol!r}")
    ts = float(info.trade_tick_size or info.point or 0.0)
    if ts <= 0:
        raise ValueError(f"trade_tick_size/point invalid for {symbol!r}")
    return ts


def round_to_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    d = Decimal(str(price)) / Decimal(str(tick))
    q = d.quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal(str(tick))
    return float(q)


def volume_step(mt5: Any, symbol: str) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise ValueError(f"symbol_info is None for {symbol!r}")
    return float(info.volume_step or 0.01)


def round_volume(mt5: Any, symbol: str, volume: float) -> float:
    step = volume_step(mt5, symbol)
    if step <= 0:
        return volume
    n = int(round(volume / step))
    if n < 1:
        n = 1
    return n * step


def _filling_type(mt5: Any, symbol: str) -> int:
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.ORDER_FILLING_IOC
    mode = int(info.filling_mode)
    if mode & mt5.SYMBOL_FILLING_FOK:
        return mt5.ORDER_FILLING_FOK
    if mode & mt5.SYMBOL_FILLING_IOC:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def last_price(mt5: Any, symbol: str) -> float | None:
    t = mt5.symbol_info_tick(symbol)
    if t is None:
        return None
    if t.bid and t.ask:
        return (float(t.bid) + float(t.ask)) / 2.0
    if t.last:
        return float(t.last)
    return None


def get_position_side_volume(mt5: Any, symbol: str, magic: int) -> tuple[str | None, float]:
    """Возвращает ('Buy'|'Sell', объём в лотах) по открытым позициям с фильтром magic."""
    pos = mt5.positions_get(symbol=symbol)
    if not pos:
        return None, 0.0
    buy_v = 0.0
    sell_v = 0.0
    for p in pos:
        if int(p.magic) != magic:
            continue
        v = float(p.volume)
        if p.type == mt5.POSITION_TYPE_BUY:
            buy_v += v
        elif p.type == mt5.POSITION_TYPE_SELL:
            sell_v += v
    if buy_v > 0 and sell_v > 0:
        log.warning("MT5 %s: одновременно лонг и шорт по magic=%s — берём большую сторону", symbol, magic)
    if buy_v >= sell_v and buy_v > 0:
        return "Buy", buy_v
    if sell_v > 0:
        return "Sell", sell_v
    return None, 0.0


def close_positions_market(mt5: Any, symbol: str, magic: int) -> bool:
    """Закрывает все позиции по символу и magic рыночными встречными ордерами."""
    pos = mt5.positions_get(symbol=symbol)
    if not pos:
        return True
    ok_all = True
    for p in pos:
        if int(p.magic) != magic:
            continue
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            log.error("MT5: нет тика для закрытия %s", symbol)
            ok_all = False
            continue
        if p.type == mt5.POSITION_TYPE_BUY:
            order_type = mt5.ORDER_TYPE_SELL
            price = float(tick.bid)
        else:
            order_type = mt5.ORDER_TYPE_BUY
            price = float(tick.ask)
        filling = _filling_type(mt5, symbol)
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(p.volume),
            "type": order_type,
            "position": int(p.ticket),
            "price": price,
            "deviation": 30,
            "magic": magic,
            "comment": "traiding_bot_ema close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }
        r = mt5.order_send(req)
        if r is None or r.retcode != mt5.TRADE_RETCODE_DONE:
            log.error(
                "MT5 close failed ticket=%s retcode=%s comment=%s",
                getattr(p, "ticket", "?"),
                getattr(r, "retcode", None),
                getattr(r, "comment", None),
            )
            ok_all = False
    return ok_all


def place_market_with_tp_sl(
    mt5: Any,
    symbol: str,
    side: str,
    volume_lots: float,
    tp_price: float,
    sl_price: float | None,
) -> None:
    """Рыночный вход с TP и опциональным SL (цены уже в валюте инструмента)."""
    magic = get_settings().mt5_magic
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"Нет котировки для {symbol!r}")
    filling = _filling_type(mt5, symbol)
    if side == "Buy":
        order_type = mt5.ORDER_TYPE_BUY
        price = float(tick.ask)
    elif side == "Sell":
        order_type = mt5.ORDER_TYPE_SELL
        price = float(tick.bid)
    else:
        raise ValueError(f"side must be Buy|Sell, got {side!r}")
    req: dict[str, Any] = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume_lots,
        "type": order_type,
        "price": price,
        "tp": tp_price,
        "deviation": 30,
        "magic": magic,
        "comment": "traiding_bot_ema",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling,
    }
    if sl_price is not None:
        req["sl"] = sl_price
    r = mt5.order_send(req)
    if r is None or r.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(
            f"order_send failed retcode={getattr(r, 'retcode', None)} "
            f"comment={getattr(r, 'comment', None)}"
        )
