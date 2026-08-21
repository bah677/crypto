from __future__ import annotations

from dataclasses import dataclass

from app.atr_pullback.intervals import interval_label
from app.db.models import AtrPullbackTaskRow

STATE_IDLE = "idle"
STATE_ARMED = "armed"
STATE_IN_POSITION = "in_position"


@dataclass(frozen=True)
class AtrPullbackTask:
    db_id: int | None
    symbol: str
    ema_fast: int
    ema_slow: int
    btf_interval: str
    mtf_interval: str
    alias: str
    trading_hours: list[dict[str, str]]
    enabled: bool
    auto_trade: bool
    position_usd: float
    leverage: int
    state: str
    armed_side: str | None
    armed_at_ms: int | None
    btf_cross_bar_open_ms: int | None
    cross_price: float | None
    last_evaluated_btf_bar_ms: int | None
    last_evaluated_mtf_bar_ms: int | None
    last_sl_update_ms: int | None

    def display_name(self) -> str:
        a = (self.alias or "").strip()
        if a:
            return f"{a} ({self.symbol})"
        return self.symbol

    def tf_pair_label(self) -> str:
        return f"{interval_label(self.btf_interval)}/{interval_label(self.mtf_interval)}"

    def key(self) -> str:
        return (
            f"{self.symbol}|{self.btf_interval}|{self.mtf_interval}|"
            f"{self.ema_fast}|{self.ema_slow}"
        )


def atr_pullback_task_from_row(row: AtrPullbackTaskRow) -> AtrPullbackTask:
    return AtrPullbackTask(
        db_id=row.id,
        symbol=row.symbol,
        ema_fast=row.ema_fast,
        ema_slow=row.ema_slow,
        btf_interval=row.btf_interval,
        mtf_interval=row.mtf_interval,
        alias=row.alias or "",
        trading_hours=row.trading_hours(),
        enabled=row.enabled,
        auto_trade=row.auto_trade,
        position_usd=float(row.position_usd),
        leverage=row.leverage,
        state=row.state or STATE_IDLE,
        armed_side=row.armed_side,
        armed_at_ms=row.armed_at_ms,
        btf_cross_bar_open_ms=row.btf_cross_bar_open_ms,
        cross_price=float(row.cross_price) if row.cross_price is not None else None,
        last_evaluated_btf_bar_ms=row.last_evaluated_btf_bar_ms,
        last_evaluated_mtf_bar_ms=row.last_evaluated_mtf_bar_ms,
        last_sl_update_ms=row.last_sl_update_ms,
    )
