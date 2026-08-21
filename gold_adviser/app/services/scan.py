"""XAU M1 body-anomaly scanner — редкий опрос под лаг Twelve Data (~55–70с)."""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db.session import session_scope
from app.market.candles import Candle, only_closed_m1
from app.market.provider import fetch_xau_candles
from app.repository.settings import alert_already_sent, save_alert
from app.services.anomaly import analyze_body_anomaly
from app.services.notify import notify_admins
from app.services.settings_cache import settings_cache

log = logging.getLogger(__name__)

_MAX_WAIT_AFTER_CLOSE = 150.0
_BACKOFF_429 = 65.0


def _fmt_price(v: float) -> str:
    return f"{v:.2f}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_rate_limited(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "too many requests" in msg


async def _process_closed_window(
    candles: list[Candle],
    provider: str,
    *,
    body_mult: float,
) -> str | None:
    result = analyze_body_anomaly(candles, body_mult=body_mult)
    if result is None:
        return None

    key = result.last.open_time_key
    async with session_scope() as session:
        if await alert_already_sent(session, key):
            log.debug("gold scan: already alerted %s", key)
            return key

    if not result.is_anomaly:
        log.debug(
            "gold ok provider=%s key=%s body=%.4f avg=%.4f ratio=%.2f",
            provider,
            key,
            result.body,
            result.avg_body,
            result.ratio,
        )
        return key

    last = result.last
    direction = "🟢 бычья" if last.close >= last.open else "🔴 медвежья"
    lag = (_utc_now() - (_aware(last.open_time) + timedelta(seconds=60))).total_seconds()
    src_note = {
        "bybit": "Bybit XAUUSDT perp",
        "twelvedata": "Twelve Data XAU/USD",
        "realmarket": "RealMarket XAUUSD",
    }.get(provider, provider)
    msg = (
        f"🚨 <b>Аномальная свеча XAU</b>\n"
        f"Источник: <code>{html.escape(src_note)}</code>\n"
        f"Время: <code>{html.escape(key)}</code>\n"
        f"Тип: {direction}\n"
        f"OHLC: {_fmt_price(last.open)} / {_fmt_price(last.high)} / "
        f"{_fmt_price(last.low)} / {_fmt_price(last.close)}\n"
        f"Тело: <b>{result.body:.2f}</b> · среднее: {result.avg_body:.2f} · "
        f"×{result.ratio:.2f} (порог ×{result.body_mult:g})\n"
        f"Окно: {result.lookback_used} M1\n"
        f"лаг после close: <b>{lag:.1f}с</b>"
    )

    async with session_scope() as session:
        if await alert_already_sent(session, key):
            return key
        await save_alert(
            session,
            candle_open_time=key,
            provider=provider,
            body=result.body,
            avg_body=result.avg_body,
            ratio=result.ratio,
            message=msg,
        )

    n = await notify_admins(msg)
    log.info(
        "gold anomaly alerted %s provider=%s lag=%.1fs sent=%s",
        key,
        provider,
        lag,
        n,
    )
    return key


async def run_gold_scan_tick() -> None:
    """Один проход (кнопка «Скан сейчас»)."""
    cfg = await settings_cache.get()
    if not cfg.enabled:
        log.debug("gold scan skipped — disabled")
        return

    lookback = max(5, min(int(cfg.lookback), 200))
    try:
        candles, provider = await fetch_xau_candles(limit=lookback)
    except Exception:
        log.exception("gold scan: fetch failed")
        return

    closed = only_closed_m1(candles)
    if len(closed) < 2:
        log.warning("gold scan: мало закрытых свечей (%s)", len(closed))
        return
    await _process_closed_window(closed, provider, body_mult=cfg.body_mult)


async def run_gold_fast_scan_loop() -> None:
    """
    Экономный цикл под лаг Twelve Data (~60с):
    - после закрытия M1 спим ~55с (бар ещё не появится — незачем жечь квоту);
    - дальше опрос раз в ~10с, пока не придёт новая закрытая свеча;
    - при 429 — пауза ~65с.
    Итого обычно 1–3 запроса/мин вместо ~30.
    """
    s = get_settings()
    after_close = float(s.scan_after_close_sec)
    poll_waiting = float(s.scan_poll_sec)
    log.info(
        "scan loop started (after_close=%.0fs poll=%.0fs) — quota-friendly",
        after_close,
        poll_waiting,
    )
    last_seen_key: str | None = None

    while True:
        try:
            cfg = await settings_cache.get()
            if not cfg.enabled:
                await asyncio.sleep(5.0)
                continue

            lookback = max(5, min(int(cfg.lookback), 200))
            try:
                candles, provider = await fetch_xau_candles(limit=lookback)
            except Exception as e:
                if _is_rate_limited(e):
                    log.warning("rate limit — sleep %.0fs", _BACKOFF_429)
                    await asyncio.sleep(_BACKOFF_429)
                else:
                    log.exception("gold scan: fetch failed")
                    await asyncio.sleep(poll_waiting)
                continue

            closed = only_closed_m1(candles)
            if len(closed) < 2:
                await asyncio.sleep(poll_waiting)
                continue

            last = closed[-1]
            key = last.open_time_key

            if key != last_seen_key:
                close_at = _aware(last.open_time) + timedelta(seconds=60)
                lag = (_utc_now() - close_at).total_seconds()
                if last_seen_key is None and lag > 45:
                    log.info("scan catch-up skip stale last=%s lag=%.0fs", key, lag)
                    last_seen_key = key
                else:
                    await _process_closed_window(
                        closed, provider, body_mult=cfg.body_mult
                    )
                    last_seen_key = key

            # следующая свеча: open=last+60, close=last+120
            next_close = _aware(last.open_time) + timedelta(seconds=120)
            wake = next_close + timedelta(seconds=after_close)
            now = _utc_now()
            if now < wake:
                delay = (wake - now).total_seconds()
                log.debug("scan sleep %.0fs until after expected close", delay)
                await asyncio.sleep(max(0.5, delay))
                continue

            waited = (now - next_close).total_seconds()
            if waited > _MAX_WAIT_AFTER_CLOSE:
                await asyncio.sleep(max(poll_waiting, 20.0))
            else:
                await asyncio.sleep(poll_waiting)

        except asyncio.CancelledError:
            log.info("scan loop cancelled")
            raise
        except Exception:
            log.exception("gold scan loop error")
            await asyncio.sleep(poll_waiting)
