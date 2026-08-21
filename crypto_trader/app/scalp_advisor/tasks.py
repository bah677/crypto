from __future__ import annotations

from dataclasses import dataclass

from app.scalp_advisor.strategy_params import ScalpStrategyParams
from app.db.models import ScalpAdvisorTaskRow

TRADE_IDLE = "idle"
TRADE_OPEN = "open"


@dataclass(frozen=True)
class ScalpAdvisorTask:
    db_id: int | None
    symbol: str
    alias: str
    levels: list[float]
    trading_hours: list[dict[str, str]]
    enabled: bool
    strategy: ScalpStrategyParams
    last_evaluated_m1_bar_ms: int | None
    trade_state: str
    trade_side: str | None
    entry_price: float | None
    entry_ms: int | None
    initial_sl: float | None
    trade_sl: float | None
    trade_tp1: float | None
    trade_tp2: float | None
    tp1_hit: bool
    tp2_hit: bool
    last_reported_sl: float | None
    last_m5_sl_bar_ms: int | None

    def display_name(self) -> str:
        a = (self.alias or "").strip()
        if a:
            return f"{a} ({self.symbol})"
        return self.symbol

    def in_trade(self) -> bool:
        return self.trade_state == TRADE_OPEN


def scalp_task_from_row(row: ScalpAdvisorTaskRow) -> ScalpAdvisorTask:
    return ScalpAdvisorTask(
        db_id=row.id,
        symbol=row.symbol,
        alias=row.alias or "",
        levels=row.level_prices(),
        trading_hours=row.trading_hours(),
        enabled=row.enabled,
        strategy=row.strategy_params(),
        last_evaluated_m1_bar_ms=row.last_evaluated_m1_bar_ms,
        trade_state=row.trade_state or TRADE_IDLE,
        trade_side=row.trade_side,
        entry_price=row.entry_price,
        entry_ms=row.entry_ms,
        initial_sl=row.initial_sl,
        trade_sl=row.trade_sl,
        trade_tp1=row.trade_tp1,
        trade_tp2=row.trade_tp2,
        tp1_hit=bool(row.tp1_hit),
        tp2_hit=bool(row.tp2_hit),
        last_reported_sl=row.last_reported_sl,
        last_m5_sl_bar_ms=row.last_m5_sl_bar_ms,
    )


def format_scalp_entry_message(task: ScalpAdvisorTask, sig) -> str:
    from app.scalp_advisor.logic import ScalpSignal

    s: ScalpSignal = sig
    emoji = "🟢" if s.side == "Buy" else "🔴"
    side_ru = "LONG" if s.side == "Buy" else "SHORT"
    lines = [
        f"{emoji} <b>Scalp · ОТКРЫТИЕ · {side_ru}</b> · {task.display_name()}",
        f"M5: кросс EMA20/50 ({s.m5_cross_age_bars} св.) · откат {s.m5_pullback_atr:.2f} ATR",
        f"M1: {s.pattern} · ADX={s.adx_m1:.1f} · запас TP1={s.room_r1_atr:.2f} ATR",
        f"Entry: <b>{s.entry:g}</b> · SL: {s.sl:g} ({s.sl_pct:.3f}%)",
        f"TP1: {s.tp1:g} (50%) · TP2: {s.tp2:g} (50%)",
    ]
    return "\n".join(lines)


def format_scalp_sl_update(
    task: ScalpAdvisorTask,
    old_sl: float | None,
    new_sl: float,
    *,
    tp1_hit: bool,
    tp2_hit: bool,
) -> str:
    old_s = f"{old_sl:g}" if old_sl is not None else "—"
    flags = []
    if tp1_hit:
        flags.append("TP1✓")
    if tp2_hit:
        flags.append("TP2✓")
    flag_s = f" · {' · '.join(flags)}" if flags else ""
    side = "LONG" if task.trade_side == "Buy" else "SHORT"
    return (
        f"<b>Scalp · SL</b> · {side} · {task.display_name()}{flag_s}\n"
        f"{old_s} → <b>{new_sl:g}</b> · entry {task.entry_price:g}"
    )


def format_scalp_close(task: ScalpAdvisorTask, ev) -> str:
    from app.scalp_advisor.trade_manage import CloseEvent

    e: CloseEvent = ev
    side = "LONG" if task.trade_side == "Buy" else "SHORT"
    sign = "+" if e.pnl_r >= 0 else ""
    return (
        f"<b>Scalp · СДЕЛКА ЗАКРЫТА</b> · {side} · {task.display_name()}\n"
        f"Причина: {e.reason} · exit {e.exit_price:g}\n"
        f"Entry {task.entry_price:g} · результат <b>{sign}{e.pnl_r:.2f}R</b> "
        f"(1R = |entry − SL| = {abs((task.entry_price or 0) - (task.initial_sl or 0)):g})"
    )
