"""Pump&Dump scanner: universe refresh + multi-TF detection alerts."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.bybit.priority import background_request_scope, end_background_tick, try_begin_background_tick
from app.bybit.rest import BybitRest
from app.config import get_settings
from app.db.session import session_scope
from app.pump_scan.daily_chart import render_pump_alert_chart_png
from app.pump_scan.daily_ema import compute_daily_emas
from app.pump_scan.weekly_ema import compute_weekly_emas
from app.pump_scan.detect import (
    ScanHit,
    _hit_quality_score,
    detect_symbol_hits,
    fast_intervals,
    format_scan_alert,
    pump_alert_keyboard,
    slow_intervals,
)
from app.pump_scan.market_context import enrich_hits_market_context, format_market_context_lines
from app.pump_scan.pump_strength import classify_pump_strength, pump_fire_prefix
from app.pump_scan.params import PumpScanParams
from app.pump_scan.tvh import format_tvh_alert_lines
from app.pump_scan.universe import PoolCoin, build_universe
from app.repository.pump_scan import get_pump_config, update_pump_pool
from app.repository.pump_outcomes import create_pump_outcome
from app.services.admin_notify import notify_dump_channel, notify_pump_channel

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

_POOL_LIST_PAGE = 40

_last_alert_mono: dict[str, float] = {}


def _cooldown_key(hit: ScanHit) -> str:
    return f"{hit.symbol.upper()}:{hit.direction}"


def _cooldown_ok(hit: ScanHit, cooldown_min: int) -> bool:
    last = _last_alert_mono.get(_cooldown_key(hit), 0.0)
    return (time.monotonic() - last) >= cooldown_min * 60


def _mark_alerted(hit: ScanHit) -> None:
    _last_alert_mono[_cooldown_key(hit)] = time.monotonic()


def _pool_stale(row, params: PumpScanParams) -> bool:
    if row.pool_updated_at is None:
        return True
    age_h = (datetime.now(tz=row.pool_updated_at.tzinfo) - row.pool_updated_at).total_seconds() / 3600
    return age_h >= params.universe_refresh_hours


def _refresh_universe_sync(params: PumpScanParams) -> list[PoolCoin]:
    with background_request_scope():
        return build_universe(params)


def _scan_pool_sync(
    pool: list[PoolCoin],
    params: PumpScanParams,
    intervals: list[str],
    *,
    force: bool = False,
    as_of_ms: int | None = None,
    as_of_label: str | None = None,
) -> list[ScanHit]:
    hits: list[ScanHit] = []
    client = BybitRest(category="linear")
    with background_request_scope():
        for coin in pool:
            try:
                coin_hits = detect_symbol_hits(
                    client,
                    coin,
                    params,
                    intervals,
                    as_of_ms=as_of_ms,
                    as_of_label=as_of_label,
                )
            except Exception:
                log.exception("Pump scan: сбой %s", coin.symbol)
                continue
            for hit in coin_hits:
                if not force and not _cooldown_ok(hit, params.alert_cooldown_min):
                    continue
                hits.append(hit)
    return hits


def parse_manual_scan_time(text: str, *, tz: ZoneInfo = MSK) -> datetime:
    """Дата/время ручного исторического скана: yyyy-mm-dd hh:mm (MSK)."""
    raw = (text or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=tz)
        except ValueError:
            continue
    raise ValueError("Формат: <code>yyyy-mm-dd hh:mm</code> (MSK), напр. <code>2026-06-16 12:00</code>")


def _manual_intervals(params: PumpScanParams) -> list[str]:
    fast = fast_intervals(params)
    slow = [iv for iv in slow_intervals(params) if iv not in fast]
    return fast + slow


async def _wait_background_tick(name: str, *, timeout_s: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await asyncio.to_thread(try_begin_background_tick, name):
            return True
        await asyncio.sleep(0.5)
    return False


def _pump_hits_only(hits: list[ScanHit]) -> list[ScanHit]:
    return [h for h in hits if h.direction == "pump"]


async def build_pump_impulse_alert_bundle(
    hit: ScanHit,
    *,
    as_of_ms: int | None = None,
    test_prefix: bool = False,
) -> tuple[str, bytes | None]:
    """Текст алерта + PNG (1D+5m) для pump-импульса."""
    enriched = await asyncio.to_thread(
        enrich_hits_market_context, [hit], as_of_ms=as_of_ms
    )
    hit = enriched[0] if enriched else hit
    client = BybitRest(category="linear")

    params = PumpScanParams()
    try:
        async with session_scope() as session:
            row = await get_pump_config(session)
            params = row.params()
    except Exception:
        log.exception("Pump alert: load params failed")

    lines = format_scan_alert(hit, params).split("\n")
    try:
        if params.orderbook_check_enabled:
            slip = await asyncio.to_thread(
                client.estimate_market_sell_slippage_pct,
                hit.symbol,
                notional_usd=float(params.orderbook_check_usd),
            )
            if slip is not None and slip > float(params.orderbook_max_slippage_pct):
                lines.append(
                    f"⚠️ Стакан тонкий: ожидаемое проскальзывание ~<b>{slip:.1f}%</b> "
                    f"на ${float(params.orderbook_check_usd):.0f} market"
                )
    except Exception:
        log.exception("Orderbook check failed %s", hit.symbol)
    try:
        emas = await asyncio.to_thread(
            compute_daily_emas, client, hit.symbol, as_of_ms=as_of_ms
        )
        if emas is not None:
            strength = classify_pump_strength(hit, hit.close, emas)
            fires = pump_fire_prefix(strength)
            if lines:
                lines[0] = re.sub(r"^🔥+\s*", f"{fires} ", lines[0], count=1)
            lines.extend(emas.format_lines(price=hit.close))
        weekly = await asyncio.to_thread(
            compute_weekly_emas, client, hit.symbol, as_of_ms=as_of_ms
        )
        if weekly is not None:
            lines.extend(weekly.format_lines(price=hit.close))
    except Exception:
        log.exception("Pump impulse: EMA 1D %s", hit.symbol)

    msg = "\n".join(lines)
    if test_prefix:
        msg = (
            "🧪 <b>ТЕСТ</b> · актуальный pump-алерт\n"
            f"<code>{hit.symbol}</code> · {hit.interval} · +{hit.price_change_pct:.1f}%\n\n"
            + msg
        )

    chart_png: bytes | None = None
    try:
        chart_png = await asyncio.to_thread(
            render_pump_alert_chart_png,
            client,
            hit.symbol,
            as_of_ms=as_of_ms,
            impulse_price=hit.close,
        )
    except Exception:
        log.exception("Pump impulse: графики %s", hit.symbol)

    return msg, chart_png


async def find_best_current_pump_hit(symbol: str | None = None) -> ScanHit | None:
    """Лучший текущий pump-хит в пуле (или по символу), без cooldown."""
    async with session_scope() as session:
        row = await get_pump_config(session)
        params = row.params()
        pool = row.pool_coins()

    intervals = _manual_intervals(params)
    if not intervals:
        return None

    if symbol:
        sym = symbol.upper()
        if not sym.endswith("USDT"):
            sym = f"{sym}USDT"
        pool = [c for c in pool if c.symbol.upper() == sym]
        if not pool:
            pool = [PoolCoin(symbol=sym, name=sym, source="manual")]

    if not pool:
        return None

    hits = await asyncio.to_thread(
        _scan_pool_sync, pool, params, intervals, force=True
    )
    pump_hits = _pump_hits_only(hits)
    if not pump_hits:
        return None
    return max(pump_hits, key=_hit_quality_score)


async def _send_deepseek_followup(
    hit: ScanHit,
    alert_text: str,
    chat_id: int,
    message_id: int | None,
) -> None:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.config import get_settings
    from app.db.session import session_scope
    from app.pump_scan.entry_watch_plan import default_watch_plan, hit_suggests_early_entry
    from app.repository.pump_entry_watch import upsert_entry_watch_suggestion
    from app.services.admin_notify import _send_message
    from app.services.pump_deepseek import (
        analyze_pump_hit_structured_async,
        format_deepseek_reply,
    )

    settings = get_settings()
    if not settings.deepseek_ready:
        return
    try:
        analysis = await analyze_pump_hit_structured_async(hit, alert_text)
        if not analysis:
            return
        text = format_deepseek_reply(analysis.text, analysis=analysis)
        await _send_message(
            chat_id,
            text,
            reply_to_message_id=message_id,
        )
        # Сохраняем plan для кнопки «Следить до входа»
        if message_id is not None:
            now = datetime.now(tz=ZoneInfo("Europe/Moscow"))
            plan = analysis.watch_plan or default_watch_plan()
            async with session_scope() as session:
                await upsert_entry_watch_suggestion(
                    session,
                    symbol=hit.symbol,
                    source_chat_id=chat_id,
                    source_message_id=message_id,
                    impulse_price=float(hit.close),
                    impulse_interval=str(hit.interval),
                    entry_timing=analysis.entry_timing,
                    watch_if_early=bool(
                        analysis.watch_if_early
                        or analysis.entry_timing == "early"
                        or hit_suggests_early_entry(hit)
                    ),
                    watch_plan=plan,
                    alert_text=alert_text,
                    analysis_excerpt=analysis.text[:4000],
                    expires_at=now + timedelta(hours=int(plan.get("ttl_hours") or 24)),
                )
                await session.commit()
    except Exception:
        log.exception("DeepSeek followup failed %s", hit.symbol)


async def send_pump_impulse_alerts(
    hits: list[ScanHit],
    *,
    as_of_ms: int | None = None,
) -> None:
    """Алерт сразу при обнаружении pump: детекция + EMA 50/100/200 на 1D → топик pump."""
    pump_hits = _pump_hits_only(hits)
    if not pump_hits:
        return

    for hit in pump_hits:
        msg, chart_png = await build_pump_impulse_alert_bundle(hit, as_of_ms=as_of_ms)
        try:
            chat_id, msg_id = await notify_pump_channel(
                msg,
                reply_markup=pump_alert_keyboard(
                    hit.symbol,
                    offer_entry_watch=True,
                ),
                photo=chart_png,
            )
            asyncio.create_task(_send_deepseek_followup(hit, msg, chat_id, msg_id))
            try:
                async with session_scope() as session:
                    row = await get_pump_config(session)
                    params = row.params()
                    if params.outcome_logging_enabled:
                        client = BybitRest(category="linear")
                        emas = await asyncio.to_thread(
                            compute_daily_emas, client, hit.symbol, as_of_ms=as_of_ms
                        )
                        features = {
                            "trend": hit.trend.__dict__ if hit.trend else None,
                            "oi": hit.oi.__dict__ if hit.oi else None,
                            "climax": hit.climax.__dict__ if hit.climax else None,
                            "funding_roc": hit.funding_roc.__dict__ if hit.funding_roc else None,
                            "funding_oi": hit.funding_oi.__dict__ if hit.funding_oi else None,
                            "isolation": hit.isolation.__dict__ if hit.isolation else None,
                            "distance": hit.distance.__dict__ if hit.distance else None,
                            "score_mult": hit.score_mult,
                        }
                        await create_pump_outcome(
                            session,
                            symbol=hit.symbol,
                            direction=hit.direction,
                            interval=hit.interval,
                            move_kind=hit.move_kind,
                            window_bars=hit.window_bars,
                            entry_price=float(hit.close),
                            score=float(_hit_quality_score(hit)),
                            features=features,
                            ema50_1d=emas.ema50 if emas else None,
                            ema100_1d=emas.ema100 if emas else None,
                            ema200_1d=emas.ema200 if emas else None,
                            horizon_hours=int(params.outcome_check_horizon_hours),
                        )
                        await session.commit()
            except Exception:
                log.exception("Outcome logging failed %s", hit.symbol)
            log.info(
                "Pump impulse alert %s %s %s +%.1f%% chart=%s",
                hit.symbol,
                hit.interval,
                hit.move_kind,
                hit.price_change_pct,
                "yes" if chart_png else "no",
            )
        except Exception:
            log.exception("Pump impulse: не отправили %s", hit.symbol)


async def send_tvh_alerts(
    hit: ScanHit,
    candidates: list,
    *,
    as_of_ms: int | None = None,
) -> None:
    from app.pump_scan.detect import _risk_tag_lines

    enriched = await asyncio.to_thread(enrich_hits_market_context, [hit], as_of_ms=as_of_ms)
    hit = enriched[0] if enriched else hit

    for tvh in candidates:
        lines = format_tvh_alert_lines(
            symbol=hit.symbol,
            impulse_direction=hit.direction,
            source_interval=hit.interval,
            impulse_pct=hit.price_change_pct,
            impulse_rvol=hit.rvol,
            tvh=tvh,
        )
        lines.extend(format_market_context_lines(hit.market))
        lines.extend(_risk_tag_lines(hit))
        if hit.scan_as_of_msk:
            lines.append(f"<i>📅 Исторический срез: {hit.scan_as_of_msk}</i>")
        msg = "\n".join(lines)
        notify = notify_dump_channel if hit.direction == "dump" else notify_pump_channel
        try:
            await notify(msg, reply_markup=pump_alert_keyboard(hit.symbol))
            log.info(
                "TVH alert %s %s %s score=%s scenario=%s",
                hit.direction,
                hit.symbol,
                tvh.entry_interval,
                tvh.score,
                tvh.scenario,
            )
        except Exception:
            log.exception("TVH: не отправили алерт %s", hit.symbol)


async def _process_scan_hits(
    hits: list[ScanHit],
    params: PumpScanParams,
    *,
    update_cooldown: bool = True,
    as_of_ms: int | None = None,
    historical: bool = False,
) -> list[ScanHit]:
    """Pump: только алерт импульса (EMA 1D + сила 🔥). ТВХ отключена."""
    pump_hits = _pump_hits_only(hits)
    if not pump_hits:
        return []

    fresh = [
        h
        for h in pump_hits
        if update_cooldown or _cooldown_ok(h, params.alert_cooldown_min)
    ]
    if not fresh:
        return []

    await send_pump_impulse_alerts(fresh, as_of_ms=as_of_ms)
    if update_cooldown:
        for hit in fresh:
            _mark_alerted(hit)
    return fresh


async def _send_hits(
    hits: list[ScanHit],
    *,
    update_cooldown: bool = True,
    as_of_ms: int | None = None,
    historical: bool = False,
) -> list[ScanHit]:
    async with session_scope() as session:
        row = await get_pump_config(session)
        params = row.params()
    return await _process_scan_hits(
        hits,
        params,
        update_cooldown=update_cooldown,
        as_of_ms=as_of_ms,
        historical=historical,
    )


async def run_pump_universe_refresh(*, force: bool = False) -> int:
    s = get_settings()
    if not s.pump_scan_enabled:
        return 0

    async with session_scope() as session:
        row = await get_pump_config(session)
        params = row.params()
        if not force and not _pool_stale(row, params):
            return len(row.pool_coins())

    if not await asyncio.to_thread(try_begin_background_tick, "pump_universe"):
        return 0

    try:
        coins = await asyncio.to_thread(_refresh_universe_sync, params)
        async with session_scope() as session:
            await update_pump_pool(session, coins)
        log.info("Pump universe refresh: %s монет", len(coins))
        return len(coins)
    except Exception:
        log.exception("Pump universe refresh failed")
        return 0
    finally:
        await asyncio.to_thread(end_background_tick)


async def run_pump_scan_tick(*, force: bool = False) -> list[ScanHit]:
    """Быстрые TF: 5m, 15m, 30m, 1h."""
    return await _run_scan(intervals_fn=fast_intervals, force=force, tick_name="pump_scan")


async def run_pump_slow_tf_scan(*, force: bool = False) -> list[ScanHit]:
    """Медленные TF: 4h, 1D — раз в час."""
    return await _run_scan(intervals_fn=slow_intervals, force=force, tick_name="pump_slow_tf")


async def _run_scan(
    *,
    intervals_fn,
    force: bool,
    tick_name: str,
) -> list[ScanHit]:
    s = get_settings()
    if not s.pump_scan_enabled:
        return []

    async with session_scope() as session:
        row = await get_pump_config(session)
        if not row.enabled and not force:
            return []
        params = row.params()
        pool = row.pool_coins()
        pool_stale = _pool_stale(row, params)
        intervals = intervals_fn(params)

    if not intervals:
        return []

    if not pool or pool_stale:
        await run_pump_universe_refresh(force=force)
        async with session_scope() as session:
            row = await get_pump_config(session)
            pool = row.pool_coins()

    if not pool:
        log.debug("Pump scan: пул пуст")
        return []

    if not await asyncio.to_thread(try_begin_background_tick, tick_name):
        return []

    try:
        hits = await asyncio.to_thread(_scan_pool_sync, pool, params, intervals, force=force)
    finally:
        await asyncio.to_thread(end_background_tick)

    await _send_hits(hits)
    return hits


async def run_pump_manual_scan(*, as_of: datetime | None = None) -> list[ScanHit]:
    """Ручной скан: все TF, без cooldown. as_of — исторический срез (MSK)."""
    s = get_settings()
    if not s.pump_scan_enabled:
        return []

    as_of_ms: int | None = None
    as_of_label: str | None = None
    if as_of is not None:
        as_of = as_of.astimezone(MSK)
        now = datetime.now(tz=MSK)
        if as_of >= now:
            raise ValueError("Время среза должно быть в прошлом (MSK)")
        as_of_ms = int(as_of.timestamp() * 1000)
        as_of_label = as_of.strftime("%Y-%m-%d %H:%M MSK")

    async with session_scope() as session:
        row = await get_pump_config(session)
        params = row.params()
        pool = row.pool_coins()
        pool_stale = _pool_stale(row, params)
        intervals = _manual_intervals(params)

    if not intervals:
        return []

    if not pool or pool_stale:
        await run_pump_universe_refresh(force=True)
        async with session_scope() as session:
            row = await get_pump_config(session)
            pool = row.pool_coins()

    if not pool:
        return []

    if not await _wait_background_tick("pump_manual"):
        log.warning("Pump manual scan: не дождались фонового слота")
        return []

    try:
        hits = await asyncio.to_thread(
            _scan_pool_sync,
            pool,
            params,
            intervals,
            force=True,
            as_of_ms=as_of_ms,
            as_of_label=as_of_label,
        )
    finally:
        await asyncio.to_thread(end_background_tick)

    processed = await _send_hits(
        hits,
        update_cooldown=False,
        as_of_ms=as_of_ms,
        historical=as_of_ms is not None,
    )
    if as_of_label:
        log.info(
            "Pump manual historical scan @ %s: %s impulse(s), %s TVH alert(s)",
            as_of_label,
            len(hits),
            len(processed),
        )
    return processed


def format_pump_status(row) -> str:
    params = row.params()
    pool = row.pool_coins()
    en = "вкл" if row.enabled else "выкл"
    pool_ts = "—"
    if row.pool_updated_at:
        pool_ts = row.pool_updated_at.astimezone(MSK).strftime("%Y-%m-%d %H:%M MSK")
    outside = sum(1 for c in pool if c.outside_top200)
    innov = sum(1 for c in pool if c.is_innovation)
    st_n = sum(1 for c in pool if c.is_st)
    lc = "вкл" if get_settings().lunarcrush_ready and params.lunarcrush_in_alerts else "выкл"
    s = get_settings()
    pump_tid = s.telegram_alerts_topic_pump or "—"
    return (
        "<b>Pump&amp;Dump scanner</b>\n"
        f"Мониторинг: <b>{en}</b> · пул <b>{len(pool)}</b>\n"
        f"вне топ-{params.top_turnover_rank}: <b>{outside}</b> · "
        f"Innovation: <b>{innov}</b> · ST: <b>{st_n}</b>\n"
        f"Пул обновлён: {pool_ts}\n"
        f"TF быстрый: <code>{params.scan_intervals_fast}</code>\n"
        f"TF медленный: <code>{params.scan_intervals_slow}</code>\n"
        f"Dump: <b>{'вкл (≤1h)' if params.dump_detection_enabled else 'выкл'}</b> · "
        f"LunarCrush: <b>{lc}</b>\n"
        f"Скан каждые <b>{params.scan_interval_min}</b> мин · cooldown "
        f"<b>{params.alert_cooldown_min}</b> мин\n"
        f"Стратегия: pump + EMA 1D + сила 🔥 → топик <b>{pump_tid}</b>\n"
        f"ТВХ / шорт-фейд: <b>выкл</b>"
    )


def format_pool_list_page(
    coins: list[PoolCoin],
    page: int,
    *,
    pool_updated_at: datetime | None = None,
) -> tuple[str, int, int]:
    """Текст страницы списка пула, номер страницы (0-based), всего страниц."""
    total = len(coins)
    pages = max(1, (total + _POOL_LIST_PAGE - 1) // _POOL_LIST_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = coins[page * _POOL_LIST_PAGE : (page + 1) * _POOL_LIST_PAGE]

    lines = ["<b>Пул монет</b>"]
    if total:
        lines.append(f"Всего <b>{total}</b> · стр. <b>{page + 1}/{pages}</b>")
    else:
        lines.append("Пул пуст")
    if pool_updated_at:
        ts = pool_updated_at.astimezone(MSK).strftime("%Y-%m-%d %H:%M MSK")
        lines.append(f"Обновлён: {ts}")
    lines.append("")

    if not chunk:
        lines.append("<i>Нажмите «🌐 Обновить пул» на главном экране.</i>")
    else:
        for i, coin in enumerate(chunk, start=page * _POOL_LIST_PAGE + 1):
            tags = coin.badge_tags()
            rank = f" #{coin.turnover_rank}" if coin.turnover_rank else ""
            tag = f" · {', '.join(tags)}" if tags else ""
            lines.append(f"{i}.{rank} <code>{coin.symbol}</code>{tag}")

    return "\n".join(lines), page, pages
