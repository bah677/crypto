"""Minute scan at :SS — XAU body anomaly detector."""

from __future__ import annotations

import html
import logging

from app.db.session import session_scope
from app.market.provider import fetch_xau_candles
from app.repository.settings import alert_already_sent, save_alert
from app.services.anomaly import analyze_body_anomaly
from app.services.notify import notify_admins
from app.services.settings_cache import settings_cache

log = logging.getLogger(__name__)


def _fmt_price(v: float) -> str:
    return f"{v:.2f}"


async def run_gold_scan_tick() -> None:
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

    result = analyze_body_anomaly(candles, body_mult=cfg.body_mult)
    if result is None:
        return

    key = result.last.open_time_key
    async with session_scope() as session:
        if await alert_already_sent(session, key):
            log.debug("gold scan: already alerted %s", key)
            return

    if not result.is_anomaly:
        log.debug(
            "gold ok provider=%s key=%s body=%.4f avg=%.4f ratio=%.2f",
            provider,
            key,
            result.body,
            result.avg_body,
            result.ratio,
        )
        return

    last = result.last
    direction = "🟢 бычья" if last.close >= last.open else "🔴 медвежья"
    msg = (
        f"🚨 <b>Аномальная свеча XAU/USD</b>\n"
        f"Время: <code>{html.escape(key)}</code>\n"
        f"Тип: {direction}\n"
        f"OHLC: {_fmt_price(last.open)} / {_fmt_price(last.high)} / "
        f"{_fmt_price(last.low)} / {_fmt_price(last.close)}\n"
        f"Тело: <b>{result.body:.2f}</b> · среднее: {result.avg_body:.2f} · "
        f"×{result.ratio:.2f} (порог ×{result.body_mult:g})\n"
        f"Окно: {result.lookback_used} M1 · источник: <code>{html.escape(provider)}</code>"
    )

    async with session_scope() as session:
        # double-check race
        if await alert_already_sent(session, key):
            return
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
    log.info("gold anomaly alerted %s provider=%s sent=%s", key, provider, n)
