"""
Проверка рыночного SHORT: только **точный** тикер из TEST_ORDER_SYMBOL (без подстановок).

Запуск из каталога traiding_bot_ema:
  TEST_ORDER_SYMBOL=BTCUSDT python -m scripts.test_xau_market_short

Реальная отправка:
  LIVE_ORDER=1 TEST_ORDER_SYMBOL=XAUTUSDT python -m scripts.test_xau_market_short

Параметры: TEST_ORDER_QTY, TEST_SL_USD, TEST_TP_USD
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    sym = os.environ.get("TEST_ORDER_SYMBOL", "").strip()
    if not sym:
        print(
            "Задайте TEST_ORDER_SYMBOL=… (точный тикер в Bybit API для вашего BYBIT_CATEGORY). "
            "Подстановка «похожих» тикеров отключена.",
            file=sys.stderr,
        )
        return 2
    sym = sym.upper()
    qty = os.environ.get("TEST_ORDER_QTY", "0.01").strip()
    sl_usd = float(os.environ.get("TEST_SL_USD", "3"))
    tp_usd = float(os.environ.get("TEST_TP_USD", "10"))
    live = os.environ.get("LIVE_ORDER", "").strip().lower() in ("1", "yes", "true", "on")

    if not os.path.isfile(".env"):
        print("Ожидается запуск из каталога traiding_bot_ema (рядом с .env).", file=sys.stderr)
        return 2

    if not os.getenv("SUPERADMIN_TELEGRAM_ID", "").strip():
        os.environ["SUPERADMIN_TELEGRAM_ID"] = "1"
    if not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
        os.environ["TELEGRAM_BOT_TOKEN"] = "x"

    from app.bybit.rest import BybitRest
    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    print("=== Проверка рыночного SHORT (только указанный тикер) ===")
    print(f"Файл: {__file__}")
    print(f"BYBIT_CATEGORY={settings.bybit_category!r}  symbol={sym!r}  qty={qty!r}")
    print(f"SL = цена + {sl_usd}$, TP = цена − {tp_usd}$ (шорт)  LIVE_ORDER={live}\n")

    client = BybitRest()
    try:
        tick, step = client.instrument_filters(sym)
    except Exception as e:
        print("Тикер в канале Bybit API не найден (подстановки отключены):", repr(e))
        return 1

    last = client.last_price(sym)
    if last is None:
        print("Не удалось получить lastPrice")
        return 1

    sl_raw = last + sl_usd
    tp_raw = last - tp_usd
    sl_s = BybitRest.round_to_tick(sl_raw, tick)
    tp_s = BybitRest.round_to_tick(tp_raw, tick)
    qty_s = BybitRest.round_qty(qty, step)

    print(f"last={last}  tickSize={tick}  qtyStep={step}")
    print(f"qty={qty_s}  stopLoss={sl_s}  takeProfit={tp_s}")

    if not live:
        print("\nДля отправки ордера: LIVE_ORDER=1")
        return 0

    try:
        r = client.place_market_with_tp_sl(sym, "Sell", qty_s, tp_s, sl_s)
    except Exception as e:
        print("place_order:", repr(e))
        return 1
    print("Ответ:", r)
    if (r or {}).get("retCode") != 0:
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
