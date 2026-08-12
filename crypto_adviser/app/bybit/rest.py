from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pybit.unified_trading import HTTP

from pybit.exceptions import InvalidRequestError

from app.config import get_settings
from app.bybit.priority import bybit_api_slot

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinearPositionSnapshot:
    symbol: str
    side: str  # Buy | Sell
    qty: str
    stop_loss: float | None
    avg_price: float
    mark_price: float
    liquidation_price: float | None = None
    leverage: float | None = None


@dataclass(frozen=True)
class InstrumentRiskInfo:
    max_leverage: int
    min_leverage: int
    tick_size: Decimal
    qty_step: Decimal
    min_order_qty: Decimal


@dataclass(frozen=True)
class UnifiedWalletSnapshot:
    """UNIFIED счёт Bybit (cross margin)."""

    total_equity: float
    total_available_balance: float
    total_wallet_balance: float
    total_initial_margin: float
    usdt_available: float


def _interval_to_ms(interval: str) -> int:
    if interval in ("D", "W", "M"):
        return {"D": 86400_000, "W": 604800_000, "M": 2592000_000}[interval]
    return int(interval) * 60_000


class BybitRest:
    def __init__(self, *, category: str | None = None) -> None:
        s = get_settings()
        testnet = s.bybit_network.lower() == "testnet"
        self._http = HTTP(
            testnet=testnet,
            api_key=s.bybit_api_key,
            api_secret=s.bybit_api_secret,
        )
        self.category = (category or s.bybit_category).strip().lower()
        self.position_idx = s.bybit_position_idx

    def get_kline_ohlc(
        self, symbol: str, interval: str, limit: int = 200
    ) -> list[tuple[int, float, float, float, float]]:
        """Свечи: (openTime_ms, open, high, low, close)."""
        return [
            (t, o, h, l, c)
            for t, o, h, l, c, _ in self.get_kline_ohlcv(symbol, interval, limit=limit)
        ]

    def get_kline_ohlcv(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
        *,
        end_ms: int | None = None,
    ) -> list[tuple[int, float, float, float, float, float]]:
        """Свечи: (openTime_ms, open, high, low, close, volume)."""
        kwargs: dict[str, Any] = {
            "category": self.category,
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if end_ms is not None:
            kwargs["end"] = end_ms
        with bybit_api_slot():
            r = self._http.get_kline(**kwargs)
        lst = (r or {}).get("result", {}).get("list") or []
        out: list[tuple[int, float, float, float, float, float]] = []
        for row in lst:
            if not row or len(row) < 6:
                continue
            start = int(row[0])
            o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
            vol = float(row[5] or 0)
            out.append((start, o, h, l, c, vol))
        out.sort(key=lambda x: x[0])
        return out

    def closed_ohlcv_bars_with_ts(
        self, symbol: str, interval: str, limit: int = 200
    ) -> list[tuple[int, float, float, float, float, float]]:
        """Закрытые свечи OHLCV. Последняя — только что закрывшаяся."""
        raw = self.get_kline_ohlcv(symbol, interval, limit=limit)
        if not raw:
            return []
        step = _interval_to_ms(interval)
        now_ms = int(time.time() * 1000)
        return [bar for bar in raw if bar[0] + step <= now_ms]

    def get_kline(self, symbol: str, interval: str, limit: int = 200) -> list[tuple[int, float]]:
        return [(t, c) for t, _, _, _, c in self.get_kline_ohlc(symbol, interval, limit=limit)]

    def closed_ohlc_bars_with_ts(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
        *,
        end_ms: int | None = None,
    ) -> list[tuple[int, float, float, float, float]]:
        """Закрытые свечи OHLC. Последняя — только что закрывшаяся."""
        raw = self.get_kline_ohlcv(symbol, interval, limit=limit, end_ms=end_ms)
        if not raw:
            return []
        step = _interval_to_ms(interval)
        now_ms = end_ms if end_ms is not None else int(time.time() * 1000)
        return [bar for bar in raw if bar[0] + step <= now_ms]

    def closed_bars_with_ts(
        self, symbol: str, interval: str, limit: int = 200
    ) -> list[tuple[int, float]]:
        """Закрытые свечи: (openTime_ms, close). Последняя — только что закрывшаяся."""
        return [(t, c) for t, _, _, _, c in self.closed_ohlc_bars_with_ts(symbol, interval, limit=limit)]

    def closed_candles(self, symbol: str, interval: str, limit: int = 200) -> list[float]:
        """Только закрытия закрытых свечей (по времени close)."""
        return [c for _, c in self.closed_bars_with_ts(symbol, interval, limit=limit)]

    def last_price(self, symbol: str) -> float | None:
        r = self._http.get_tickers(category=self.category, symbol=symbol)
        lst = (r or {}).get("result", {}).get("list") or []
        if not lst:
            return None
        t = lst[0]
        for key in ("lastPrice", "markPrice", "indexPrice"):
            v = t.get(key)
            if v:
                return float(v)
        return None

    def list_open_linear_symbols(self) -> list[str]:
        """Символы с ненулевой позицией на linear (USDT и USDC settle)."""
        out: list[str] = []
        seen: set[str] = set()
        for settle_coin in ("USDT", "USDC"):
            try:
                with bybit_api_slot():
                    r = self._http.get_positions(
                        category="linear",
                        settleCoin=settle_coin,
                        limit=200,
                    )
            except InvalidRequestError:
                log.debug("get_positions linear settleCoin=%s — пропуск", settle_coin)
                continue
            for p in (r or {}).get("result", {}).get("list") or []:
                try:
                    sz = abs(float(p.get("size", 0) or 0))
                except (TypeError, ValueError):
                    continue
                if sz <= 0:
                    continue
                sym = str(p.get("symbol") or "").upper().strip()
                if sym and sym not in seen:
                    seen.add(sym)
                    out.append(sym)
        return sorted(out)

    def get_linear_tickers(self) -> list[dict]:
        """Все тикеры linear (fundingRate, fundingIntervalHour, …)."""
        r = self._http.get_tickers(category="linear")
        return list((r or {}).get("result", {}).get("list") or [])

    def _instrument_info(self, symbol: str) -> dict[str, Any]:
        try:
            with bybit_api_slot():
                r = self._http.get_instruments_info(
                    category=self.category, symbol=symbol
                )
        except InvalidRequestError as e:
            raise RuntimeError(
                f"Инструмент {symbol} не найден для category={self.category}"
            ) from e
        lst = (r or {}).get("result", {}).get("list") or []
        if not lst:
            raise RuntimeError(f"Инструмент {symbol} не найден для category={self.category}")
        return lst[0]

    def instrument_filters(self, symbol: str) -> tuple[Decimal, Decimal]:
        """tick_size, qty_step"""
        info = self._instrument_info(symbol)
        pf = info.get("priceFilter") or {}
        lf = info.get("lotSizeFilter") or {}
        tick = Decimal(str(pf.get("tickSize") or "0.01"))
        step = Decimal(str(lf.get("qtyStep") or lf.get("basePrecision") or "0.001"))
        return tick, step

    def instrument_risk_info(self, symbol: str) -> InstrumentRiskInfo:
        info = self._instrument_info(symbol)
        pf = info.get("priceFilter") or {}
        lf = info.get("lotSizeFilter") or {}
        lev = info.get("leverageFilter") or {}
        tick = Decimal(str(pf.get("tickSize") or "0.01"))
        step = Decimal(str(lf.get("qtyStep") or lf.get("basePrecision") or "0.001"))
        min_qty = Decimal(str(lf.get("minOrderQty") or step))
        max_lev = int(float(lev.get("maxLeverage") or 100))
        min_lev = int(float(lev.get("minLeverage") or 1))
        return InstrumentRiskInfo(
            max_leverage=max_lev,
            min_leverage=min_lev,
            tick_size=tick,
            qty_step=step,
            min_order_qty=min_qty,
        )

    def get_unified_wallet_snapshot(self) -> UnifiedWalletSnapshot:
        """Баланс UNIFIED: cross, вся свободная маржа счёта."""
        with bybit_api_slot():
            r = self._http.get_wallet_balance(accountType="UNIFIED", coin="USDT")

        def _f(val: object) -> float:
            try:
                return float(val or 0)
            except (TypeError, ValueError):
                return 0.0

        accounts = (r or {}).get("result", {}).get("list") or []
        if not accounts:
            return UnifiedWalletSnapshot(0.0, 0.0, 0.0, 0.0, 0.0)

        acc = accounts[0]
        usdt_avail = 0.0
        for c in acc.get("coin") or []:
            if str(c.get("coin") or "").upper() == "USDT":
                for key in ("availableBalance", "availableToWithdraw", "walletBalance"):
                    raw = c.get(key)
                    if raw is not None and str(raw).strip() not in ("", "0"):
                        usdt_avail = _f(raw)
                        break

        total_avail = _f(acc.get("totalAvailableBalance"))
        if total_avail <= 0:
            total_avail = usdt_avail

        return UnifiedWalletSnapshot(
            total_equity=_f(acc.get("totalEquity")),
            total_available_balance=total_avail,
            total_wallet_balance=_f(acc.get("totalWalletBalance")),
            total_initial_margin=_f(acc.get("totalInitialMargin")),
            usdt_available=usdt_avail,
        )

    def get_usdt_available_balance(self) -> float:
        """Свободная маржа UNIFIED (cross): totalAvailableBalance."""
        return self.get_unified_wallet_snapshot().total_available_balance

    def symbol_uses_hedge_mode(self, symbol: str) -> bool:
        """Hedge: отдельные слоты Buy (1) и Sell (2); one-way — только 0."""
        with bybit_api_slot():
            r = self._http.get_positions(
                category="linear", symbol=symbol.upper(), limit=20
            )
        idxs = {
            int(p.get("positionIdx", 0))
            for p in (r or {}).get("result", {}).get("list") or []
        }
        return 1 in idxs and 2 in idxs

    def position_idx_for_side(self, side: str, symbol: str) -> int:
        side_norm = side.strip().capitalize()
        if side_norm not in ("Buy", "Sell"):
            raise ValueError(f"side must be Buy or Sell, got {side!r}")
        if self.symbol_uses_hedge_mode(symbol):
            return 1 if side_norm == "Buy" else 2
        return 0

    def get_linear_order(self, symbol: str, order_id: str) -> dict[str, Any] | None:
        """Активный или недавний ордер linear по orderId."""
        sym = symbol.upper()
        oid = order_id.strip()
        if not oid:
            return None
        with bybit_api_slot():
            r = self._http.get_open_orders(
                category="linear", symbol=sym, orderId=oid, limit=1
            )
        lst = (r or {}).get("result", {}).get("list") or []
        if lst:
            return lst[0]
        with bybit_api_slot():
            r = self._http.get_order_history(
                category="linear", symbol=sym, orderId=oid, limit=1
            )
        lst = (r or {}).get("result", {}).get("list") or []
        return lst[0] if lst else None

    def get_open_interest_series(
        self,
        symbol: str,
        *,
        interval: str,
        limit: int = 50,
        end_ms: int | None = None,
    ) -> list[tuple[int, float]]:
        """
        Open interest series for linear instruments.

        Returns list of (timestamp_ms, open_interest).
        """
        sym = symbol.upper()
        iv = interval.strip().lower()
        # Bybit expects: 5min / 15min / 30min / 1h / 4h / 1d (API may vary),
        # keep it defensive and fall back to 15min.
        if iv in ("5", "15", "30", "60", "240"):
            iv = f"{iv}min"
        elif iv in ("d", "1d", "day"):
            iv = "1d"
        elif iv in ("1h", "60min"):
            iv = "1h"
        if iv not in ("5min", "15min", "30min", "60min", "1h", "4h", "240min", "1d"):
            iv = "15min"

        params: dict[str, Any] = {
            "category": "linear",
            "symbol": sym,
            "intervalTime": iv,
            "limit": int(max(1, min(limit, 200))),
        }
        if end_ms is not None:
            params["endTime"] = int(end_ms)
        with bybit_api_slot():
            r = self._http.get_open_interest(**params)
        lst = (r or {}).get("result", {}).get("list") or []
        out: list[tuple[int, float]] = []
        for row in lst:
            try:
                ts = int(row.get("timestamp") or row.get("time") or 0)
            except (TypeError, ValueError):
                ts = 0
            try:
                oi = float(row.get("openInterest") or row.get("open_interest") or 0)
            except (TypeError, ValueError):
                oi = 0.0
            if ts > 0 and oi > 0:
                out.append((ts, oi))
        out.sort(key=lambda x: x[0])
        return out

    def get_funding_interval_hours(self, symbol: str) -> float:
        info = self._instrument_info(symbol)
        try:
            interval_min = float(info.get("fundingInterval") or 480)
        except (TypeError, ValueError):
            interval_min = 480.0
        return max(0.25, interval_min / 60.0)

    def get_funding_history_annualized(
        self,
        symbol: str,
        *,
        interval_hours: float,
        lookback_hours: int,
        end_ms: int | None = None,
    ) -> list[float]:
        """Годовые % funding за окно lookback_hours (хронологически)."""
        import math

        from app.market.funding_math import funding_rate_annual_percent

        sym = symbol.upper()
        iv_h = max(0.25, float(interval_hours))
        limit = min(200, max(2, math.ceil(int(lookback_hours) / iv_h)))
        params: dict[str, Any] = {
            "category": "linear",
            "symbol": sym,
            "limit": limit,
        }
        if end_ms is not None:
            params["endTime"] = int(end_ms)
        with bybit_api_slot():
            r = self._http.get_funding_rate_history(**params)
        lst = (r or {}).get("result", {}).get("list") or []
        if not lst:
            return []

        def _ts(row: dict) -> int:
            for k in ("fundingRateTimestamp", "fundingRateTime", "timestamp", "time"):
                v = row.get(k)
                if v is None or v == "":
                    continue
                try:
                    return int(v)
                except (TypeError, ValueError):
                    continue
            return 0

        lst = sorted(lst, key=_ts)
        out: list[float] = []
        for row in lst:
            raw = row.get("fundingRate")
            if raw in (None, ""):
                continue
            try:
                out.append(float(funding_rate_annual_percent(raw, iv_h)))
            except (TypeError, ValueError):
                continue
        return out

    def estimate_market_sell_slippage_pct(
        self,
        symbol: str,
        *,
        notional_usd: float,
        limit: int = 50,
    ) -> float | None:
        """
        Rough slippage estimate for Market Sell using orderbook bids depth.

        Returns slippage% vs best bid (positive = worse fill), or None if not enough depth.
        """
        sym = symbol.upper()
        usd = float(notional_usd)
        if usd <= 0:
            return None
        with bybit_api_slot():
            r = self._http.get_orderbook(
                category="linear",
                symbol=sym,
                limit=int(max(1, min(limit, 200))),
            )
        bids = (r or {}).get("result", {}).get("b") or (r or {}).get("result", {}).get("bids") or []
        if not bids:
            return None
        # bids rows are typically [price, size]
        try:
            best_bid = float(bids[0][0])
        except Exception:
            return None
        if best_bid <= 0:
            return None
        remaining = usd
        sold_base = 0.0
        proceeds = 0.0
        for row in bids:
            try:
                px = float(row[0])
                qty = float(row[1])
            except Exception:
                continue
            if px <= 0 or qty <= 0:
                continue
            level_value = px * qty
            take_value = min(remaining, level_value)
            take_qty = take_value / px
            sold_base += take_qty
            proceeds += take_value
            remaining -= take_value
            if remaining <= 1e-6:
                break
        if remaining > 1e-3 or sold_base <= 0:
            return None
        avg_px = proceeds / sold_base
        slippage = (best_bid - avg_px) / best_bid * 100.0
        return max(0.0, slippage)

    def set_symbol_leverage(self, symbol: str, leverage: int) -> None:
        s = get_settings()
        try:
            with bybit_api_slot():
                self._http.set_leverage(
                    category="linear",
                    symbol=symbol,
                    buyLeverage=str(leverage),
                    sellLeverage=str(leverage),
                    positionIdx=s.bybit_position_idx,
                )
        except InvalidRequestError as e:
            # 110043 — плечо уже выставлено на запрошенное значение
            if getattr(e, "status_code", None) == 110043:
                log.debug("set_leverage %s %sx: уже установлено", symbol, leverage)
                return
            raise

    def has_open_position(self, symbol: str) -> bool:
        if self.category == "spot":
            try:
                r = self._http.get_open_orders(category="spot", symbol=symbol, limit=50)
            except Exception:
                log.exception("spot get_open_orders")
                return False
            orders = (r or {}).get("result", {}).get("list") or []
            return len(orders) > 0
        side, qty = self.get_open_position_side_qty(symbol)
        return side is not None and float(qty or 0) > 0

    def get_open_position_side_qty(self, symbol: str) -> tuple[str | None, str]:
        """Сторона позиции и размер (qty) для linear/inverse; spot — не определяем (None, 0)."""
        if self.category == "spot":
            return (None, "0")
        with bybit_api_slot():
            r = self._http.get_positions(
                category=self.category, symbol=symbol, limit=20
            )
        best: dict[str, Any] | None = None
        best_sz = 0.0
        for p in (r or {}).get("result", {}).get("list") or []:
            try:
                sz = abs(float(p.get("size", 0) or 0))
            except (TypeError, ValueError):
                continue
            if sz > best_sz:
                best_sz = sz
                best = p
        if not best or best_sz == 0:
            return (None, "0")
        side = str(best.get("side") or "")
        if side not in ("Buy", "Sell"):
            return (None, "0")
        qty_raw = str(best.get("size", "0")).strip()
        try:
            q = abs(float(qty_raw))
        except ValueError:
            return (None, "0")
        _, step = self.instrument_filters(symbol)
        return (side, BybitRest.round_qty(str(q), step))

    def get_linear_position_snapshot(self, symbol: str) -> LinearPositionSnapshot | None:
        """Открытая linear-позиция: SL с биржи (ручные правки в терминале учитываются)."""
        if self.category != "linear":
            return None
        with bybit_api_slot():
            r = self._http.get_positions(
                category="linear", symbol=symbol, limit=20
            )
        best: dict[str, Any] | None = None
        best_sz = 0.0
        for p in (r or {}).get("result", {}).get("list") or []:
            try:
                sz = abs(float(p.get("size", 0) or 0))
            except (TypeError, ValueError):
                continue
            if sz > best_sz:
                best_sz = sz
                best = p
        if not best or best_sz == 0:
            return None
        side = str(best.get("side") or "")
        if side not in ("Buy", "Sell"):
            return None
        sl_raw = str(best.get("stopLoss") or "").strip()
        stop_loss: float | None = None
        if sl_raw and sl_raw not in ("0", "0.0"):
            try:
                stop_loss = float(sl_raw)
            except ValueError:
                stop_loss = None
        try:
            avg = float(best.get("avgPrice") or best.get("entryPrice") or 0)
        except (TypeError, ValueError):
            avg = 0.0
        try:
            mark = float(best.get("markPrice") or 0)
        except (TypeError, ValueError):
            mark = 0.0
        if mark <= 0:
            mark = self.last_price(symbol) or avg
        liq_raw = str(best.get("liqPrice") or "").strip()
        liq: float | None = None
        if liq_raw and liq_raw not in ("0", "0.0"):
            try:
                liq = float(liq_raw)
            except ValueError:
                liq = None
        lev_raw = best.get("leverage")
        leverage: float | None = None
        if lev_raw is not None:
            try:
                leverage = float(lev_raw)
            except (TypeError, ValueError):
                leverage = None
        _, step = self.instrument_filters(symbol)
        qty = BybitRest.round_qty(str(best_sz), step)
        return LinearPositionSnapshot(
            symbol=symbol.upper(),
            side=side,
            qty=qty,
            stop_loss=stop_loss,
            avg_price=avg,
            mark_price=mark,
            liquidation_price=liq,
            leverage=leverage,
        )

    def set_position_stop_loss(self, symbol: str, stop_loss_price: str) -> dict[str, Any]:
        """Обновить только SL позиции (Full mode)."""
        snap = self.get_linear_position_snapshot(symbol)
        side = snap.side if snap else "Buy"
        kwargs: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "stopLoss": stop_loss_price,
            "slTriggerBy": "LastPrice",
            "tpslMode": "Full",
            "positionIdx": self.position_idx_for_side(side, symbol),
        }
        with bybit_api_slot():
            return self._http.set_trading_stop(**kwargs)

    def place_reduce_only_market(self, symbol: str, side: str, qty: str) -> dict[str, Any]:
        """Закрытие/уменьшение позиции (linear/inverse)."""
        kwargs: dict[str, Any] = {
            "category": self.category,
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": qty,
            "reduceOnly": True,
        }
        if self.category in ("linear", "inverse"):
            kwargs["positionIdx"] = self.position_idx_for_side(side, symbol)
        return self._http.place_order(**kwargs)

    @staticmethod
    def round_to_tick(price: float, tick: Decimal) -> str:
        p = Decimal(str(price))
        if tick <= 0:
            return str(price)
        q = (p / tick).to_integral_value(rounding=ROUND_HALF_UP) * tick
        return format(q.normalize(), "f")

    @staticmethod
    def round_qty(qty: str, step: Decimal) -> str:
        q = Decimal(qty.strip())
        if step <= 0:
            return str(q)
        n = (q / step).to_integral_value(rounding=ROUND_HALF_UP) * step
        return format(n.normalize(), "f")

    def place_market_with_tp_sl(
        self,
        symbol: str,
        side: str,
        qty: str,
        take_profit_price: str,
        stop_loss_price: str | None,
    ) -> dict[str, Any]:
        s = get_settings()
        kwargs: dict[str, Any] = {
            "category": self.category,
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": qty,
            "takeProfit": take_profit_price,
            "tpTriggerBy": "LastPrice",
        }
        if stop_loss_price:
            kwargs["stopLoss"] = stop_loss_price
            kwargs["slTriggerBy"] = "LastPrice"

        if self.category == "spot":
            kwargs["isLeverage"] = 0
            kwargs["tpOrderType"] = "Market"
            if stop_loss_price:
                kwargs["slOrderType"] = "Market"
            mu = (s.bybit_spot_market_unit or "").strip()
            if mu:
                kwargs["marketUnit"] = mu
        elif self.category in ("linear", "inverse"):
            kwargs["tpslMode"] = "Full"
            kwargs["tpOrderType"] = "Market"
            if stop_loss_price:
                kwargs["slOrderType"] = "Market"
            kwargs["positionIdx"] = self.position_idx_for_side(side, symbol)

        return self._http.place_order(**kwargs)

    def place_market_with_sl(
        self,
        symbol: str,
        side: str,
        qty: str,
        stop_loss_price: str,
    ) -> dict[str, Any]:
        """Market без TP, только SL (linear)."""
        kwargs: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": qty,
            "stopLoss": stop_loss_price,
            "slTriggerBy": "LastPrice",
            "tpslMode": "Full",
            "slOrderType": "Market",
            "positionIdx": self.position_idx_for_side(side, symbol),
        }
        with bybit_api_slot():
            return self._http.place_order(**kwargs)

    def place_market_order(self, symbol: str, side: str, qty: str) -> dict[str, Any]:
        """Market-ордер (linear)."""
        kwargs: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": qty,
            "positionIdx": self.position_idx_for_side(side, symbol),
        }
        with bybit_api_slot():
            return self._http.place_order(**kwargs)

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        qty: str,
        price: str,
        *,
        time_in_force: str = "GTC",
    ) -> dict[str, Any]:
        """Limit-ордер (linear)."""
        kwargs: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": "Limit",
            "qty": qty,
            "price": price,
            "timeInForce": time_in_force,
            "positionIdx": self.position_idx_for_side(side, symbol),
        }
        with bybit_api_slot():
            return self._http.place_order(**kwargs)

    def qty_from_notional_usd(
        self, symbol: str, notional_usd: float, mark_price: float
    ) -> str:
        """Qty по номиналу позиции в $."""
        if mark_price <= 0 or notional_usd <= 0:
            raise ValueError("Некорректная цена или номинал")
        _, step = self.instrument_filters(symbol)
        raw_qty = notional_usd / mark_price
        return BybitRest.round_qty(str(raw_qty), step)
