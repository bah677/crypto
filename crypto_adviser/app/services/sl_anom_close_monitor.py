"""Стратегия закрытия позиции по рынку при аномальном минутном теле.

Логика:
- Для Long (Buy): ищем зелёную 1m-свечу с телом >= body_multiplier * avg_body(prev lookback_bars)
  и верхним фитилем <= wick_max_ratio * body.
- Следующая свеча: если красная (слив в обратную сторону) ИЛИ зелёная, но тело <= anomaly_body / next_small_divisor
  => закрываем long по рынку.

Для Short (Sell) — зеркально (цвета и фитиль).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.bybit.priority import end_background_tick, try_begin_background_tick
from app.bybit.rest import BybitRest, _interval_to_ms
from app.config import get_settings
from app.db.session import session_scope
from app.repository.sl_anom_close_master import get_sl_anom_close_params
from app.repository.sl_anom_close_rules import (
    disable_sl_anom_close_rule,
    fetch_enabled_sl_anom_close_rules,
    update_sl_anom_close_cursor,
)
from app.services.admin_notify import notify_sl_follow_channel
from app.services.sl_anom_close_params import SlAnomCloseParams

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


def _body(open_px: float, close_px: float) -> float:
    return abs(close_px - open_px)


def _upper_wick(open_px: float, high: float, close_px: float) -> float:
    return high - max(open_px, close_px)


def _lower_wick(open_px: float, low: float, close_px: float) -> float:
    return min(open_px, close_px) - low


def _is_green(o: float, c: float) -> bool:
    return c >= o


def _is_red(o: float, c: float) -> bool:
    return c < o


@dataclass(frozen=True)
class _CandleInfo:
    open_px: float
    high: float
    low: float
    close_px: float
    body: float

    @property
    def green(self) -> bool:
        return _is_green(self.open_px, self.close_px)

    @property
    def red(self) -> bool:
        return _is_red(self.open_px, self.close_px)

    def upper_wick_ratio(self) -> float:
        if self.body <= 0:
            return 1.0
        return _upper_wick(self.open_px, self.high, self.close_px) / self.body

    def lower_wick_ratio(self) -> float:
        if self.body <= 0:
            return 1.0
        return _lower_wick(self.open_px, self.low, self.close_px) / self.body


def _detect_anomaly_long(
    cur: _CandleInfo, avg_prev_body: float, params: SlAnomCloseParams
) -> bool:
    if not cur.green:
        return False
    if avg_prev_body <= 0:
        return False
    if cur.body < params.body_multiplier * avg_prev_body:
        return False
    if cur.upper_wick_ratio() > params.wick_max_ratio:
        return False
    return True


def _detect_anomaly_short(
    cur: _CandleInfo, avg_prev_body: float, params: SlAnomCloseParams
) -> bool:
    if not cur.red:
        return False
    if avg_prev_body <= 0:
        return False
    if cur.body < params.body_multiplier * avg_prev_body:
        return False
    if cur.lower_wick_ratio() > params.wick_max_ratio:
        return False
    return True


def _confirm_next_long(
    nxt: _CandleInfo, anomaly_body: float, params: SlAnomCloseParams
) -> bool:
    # Следующая свеча: либо красная (слив), либо зелёная но тело <= anomaly/5
    if nxt.red:
        return True
    if nxt.green:
        return nxt.body <= (anomaly_body / params.next_small_divisor)
    return False


def _confirm_next_short(
    nxt: _CandleInfo, anomaly_body: float, params: SlAnomCloseParams
) -> bool:
    if nxt.green:
        return True
    if nxt.red:
        return nxt.body <= (anomaly_body / params.next_small_divisor)
    return False


def _process_rule_sync(
    rule,
    task_params: SlAnomCloseParams,
) -> tuple[list[str], int | None, int | None, float | None, bool]:
    """Обработка одного правила.

    Returns: (reports, new_last_open_ms, pending_bar_open_ms, pending_body, disable_rule)
    """
    reports: list[str] = []
    client = BybitRest(category="linear")
    pos = client.get_linear_position_snapshot(rule.symbol)
    if pos is None:
        return ([f"⚠️ <code>{rule.symbol}</code>: позиция закрыта — авто-закрытие выключено"], None, None, None, True)
    if pos.side != rule.position_side:
        return (
            [
                f"⚠️ <code>{rule.symbol}</code>: сторона {pos.side}, ожидали {rule.position_side} — авто-закрытие выключено"
            ],
            None,
            None,
            None,
            True,
        )

    interval = task_params.interval
    need = max(task_params.lookback_bars + 3, 60)
    bars = client.closed_ohlc_bars_with_ts(rule.symbol, interval, limit=need)
    if not bars:
        return ([f"⚠️ <code>{rule.symbol}</code>: нет свечей для стратегии"], None, None, None, False)

    last_open_ms = bars[-1][0]
    if rule.last_processed_bar_open_ms == last_open_ms:
        return ([], None, rule.pending_anomaly_bar_open_ms, rule.pending_anomaly_body, False)

    # Текущая свеча (закрытая)
    _, o, h, l, c = bars[-1]
    cur = _CandleInfo(open_px=o, high=h, low=l, close_px=c, body=_body(o, c))

    pending = rule.pending_anomaly_bar_open_ms is not None and rule.pending_anomaly_body is not None
    pending_body: float | None = rule.pending_anomaly_body if pending else None

    new_pending_open: int | None = rule.pending_anomaly_bar_open_ms
    new_pending_body: float | None = rule.pending_anomaly_body

    if pending:
        # Проверяем подтверждение на текущей свече
        # Подтверждение валидно только на "следующей" свече относительно аномалии.
        step_ms = _interval_to_ms(interval)
        if rule.pending_anomaly_bar_open_ms is not None and last_open_ms != rule.pending_anomaly_bar_open_ms + step_ms:
            return ([], last_open_ms, None, None, False)

        anomaly_body = float(pending_body or 0.0)
        if anomaly_body > 0:
            if rule.position_side == "Buy":
                ok = _confirm_next_long(cur, anomaly_body, task_params)
            else:
                ok = _confirm_next_short(cur, anomaly_body, task_params)
            if ok:
                close_side = "Sell" if pos.side == "Buy" else "Buy"
                qty = pos.qty
                try:
                    # reduceOnly market, закрываем текущую позицию
                    client.place_reduce_only_market(rule.symbol, close_side, qty)
                    reports.append(
                        f"✅ <code>{rule.symbol}</code>: подтверждение аномалии — закрыли по рынку ({rule.position_side})"
                    )
                    disable_rule = True
                except Exception as e:
                    reports.append(
                        f"⚠️ <code>{rule.symbol}</code>: не удалось закрыть по рынку: {e}"
                    )
                    disable_rule = False

                # После текущего (следующего) бара сбрасываем pending, чтобы не повторять
                new_pending_open = None
                new_pending_body = None
                return (reports, last_open_ms, new_pending_open, new_pending_body, disable_rule)

        # Не подтвердилось — сбрасываем ожидание
        new_pending_open = None
        new_pending_body = None
        return (reports, last_open_ms, new_pending_open, new_pending_body, False)

    # Нет pending: проверяем, не появилась ли аномалия
    if len(bars) < task_params.lookback_bars + 1:
        return ([], None, rule.pending_anomaly_bar_open_ms, rule.pending_anomaly_body, False)

    prev_bodies: list[float] = []
    for b in bars[-1 - task_params.lookback_bars : -1]:
        _, po, ph, pl, pc = b
        prev_bodies.append(_body(po, pc))
    avg_prev = sum(prev_bodies) / len(prev_bodies) if prev_bodies else 0.0

    if rule.position_side == "Buy":
        ok = _detect_anomaly_long(cur, avg_prev, task_params)
    else:
        ok = _detect_anomaly_short(cur, avg_prev, task_params)

    if ok:
        new_pending_open = last_open_ms
        new_pending_body = cur.body
        reports.append(
            f"🔎 <code>{rule.symbol}</code>: поймали аномальное тело на 1m, ждём подтверждение (body={cur.body:.6g}, avg={avg_prev:.6g})"
        )

    return (reports, last_open_ms, new_pending_open, new_pending_body, False)


async def run_sl_anom_close_tick() -> None:
    s = get_settings()
    if not s.sl_anom_close_monitor_enabled:
        return

    if not await asyncio.to_thread(try_begin_background_tick, "sl_anom_close"):
        log.info("AnomClose: пропуск тика — занят советчик или другой фон")
        return

    try:
        async with session_scope() as session:
            params = await get_sl_anom_close_params(session)
            from app.repository.sl_anom_close_master import get_sl_anom_close_master

            master = await get_sl_anom_close_master(session)
            if not master.enabled:
                return

            rules = await fetch_enabled_sl_anom_close_rules(session)

        if not rules:
            log.info("AnomClose: нет активных правил — включите командой /sl_anom_follow")
            return

        all_reports: list[str] = []
        for rule in rules:
            # sync обработка внутри to_thread, чтобы не блокировать loop из-за Bybit API
            reports, new_last_open, pending_open, pending_body, disable = await asyncio.to_thread(
                _process_rule_sync, rule, params
            )
            if reports:
                all_reports.extend(reports)

            if new_last_open is not None:
                async with session_scope() as session:
                    await update_sl_anom_close_cursor(
                        session,
                        rule.id,
                        last_processed_bar_open_ms=new_last_open,
                        pending_anomaly_bar_open_ms=pending_open,
                        pending_anomaly_body=pending_body,
                    )

            if disable:
                async with session_scope() as session:
                    await disable_sl_anom_close_rule(session, rule.symbol)

        if not all_reports:
            log.info("AnomClose: тик без событий")
            return

        now_s = datetime.now(tz=MSK).strftime("%H:%M MSK")
        body = f"<b>Автозакрытие по аномальной 1m свече</b> · {now_s}\n\n" + "\n\n".join(all_reports[:12])
        await notify_sl_follow_channel(body)
        log.info("AnomClose: отчёт (%s событий)", len(all_reports))
    except Exception:
        log.exception("AnomClose: сбой тика")
    finally:
        await asyncio.to_thread(end_background_tick)

