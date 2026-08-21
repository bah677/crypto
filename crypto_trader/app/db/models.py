from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TradingTask(Base):
    __tablename__ = "trading_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    ema_fast: Mapped[int] = mapped_column(Integer)
    ema_slow: Mapped[int] = mapped_column(Integer)
    # Интервал Bybit v5: 1,3,5,15,30,60,... (минуты как строка)
    kline_interval: Mapped[str] = mapped_column(String(8))
    delta_ticks: Mapped[int] = mapped_column(Integer)
    take_profit_ticks: Mapped[int] = mapped_column(Integer)
    stop_loss_ticks: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    # Размер в «лотах» / qty для Bybit (строка с точкой как разделителем)
    order_qty: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # bybit_v5 — только REST API, тикер без подстановок; mt5 — TradFi/терминал (исполнение отдельно)
    trading_channel: Mapped[str] = mapped_column(
        String(16), default="bybit_v5", server_default=text("'bybit_v5'")
    )
    # JSON: [{"start":"09:00","end":"18:00"}, ...] по МСК
    trading_hours_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # openTime (мс) последней уже обработанной закрытой свечи — чтобы не дублировать сигнал на каждой секунде
    last_evaluated_bar_open_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, default=None
    )

    levels: Mapped[list[TaskLevel]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )

    def trading_hours(self) -> list[dict[str, str]]:
        raw: Any = json.loads(self.trading_hours_json or "[]")
        if not isinstance(raw, list):
            return []
        return [x for x in raw if isinstance(x, dict)]

    def set_trading_hours(self, windows: list[dict[str, str]]) -> None:
        self.trading_hours_json = json.dumps(windows, ensure_ascii=False)


class TaskLevel(Base):
    __tablename__ = "task_levels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("trading_tasks.id", ondelete="CASCADE"))
    price: Mapped[str] = mapped_column(String(32))

    task: Mapped[TradingTask] = relationship(back_populates="levels")


class AdvisorTaskRow(Base):
    """Задание советчика (EMA-сигналы без ордеров)."""

    __tablename__ = "advisor_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    ema_fast: Mapped[int] = mapped_column(Integer)
    ema_slow: Mapped[int] = mapped_column(Integer)
    kline_interval: Mapped[str] = mapped_column(String(8))
    # spot | linear — где найден тикер на Bybit (свечи/цена)
    bybit_category: Mapped[str] = mapped_column(
        String(16), default="linear", server_default=text("'linear'")
    )
    # Подпись в сигналах вместо тикера: «Gold (XAUTUSDT)»
    alias: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    trading_hours_json: Mapped[str] = mapped_column(Text, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_evaluated_bar_open_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def trading_hours(self) -> list[dict[str, str]]:
        raw: Any = json.loads(self.trading_hours_json or "[]")
        if not isinstance(raw, list):
            return []
        return [x for x in raw if isinstance(x, dict)]

    def set_trading_hours(self, windows: list[dict[str, str]]) -> None:
        self.trading_hours_json = json.dumps(windows, ensure_ascii=False)

    @property
    def task_key(self) -> str:
        return (
            f"{self.symbol}|{self.bybit_category}|{self.kline_interval}|"
            f"{self.ema_fast}|{self.ema_slow}"
        )


class BotAlertsFlags(Base):
    """Вкл/выкл автоотправки алертов из Telegram (id=1)."""

    __tablename__ = "bot_alerts_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ema_sl_reports: Mapped[bool] = mapped_column(Boolean, default=True)
    price_spike_reports: Mapped[bool] = mapped_column(Boolean, default=True)
    funding_reports: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SlFollowRow(Base):
    """Автоследование SL на Bybit для открытой linear-позиции."""

    __tablename__ = "sl_follow"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    position_side: Mapped[str] = mapped_column(String(8))  # Buy | Sell
    advisor_task_id: Mapped[int] = mapped_column(
        ForeignKey("advisor_tasks.id", ondelete="CASCADE")
    )
    # base — ТФ задания; junior — младший (МТФ)
    sl_tf_mode: Mapped[str] = mapped_column(String(8))
    allow_sl_widen: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_processed_bar_open_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SlAnomCloseMasterRow(Base):
    """Мастер настроек стратегии закрытия по аномальному минутному телу."""

    __tablename__ = "sl_anom_close_master"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SlAnomCloseRuleRow(Base):
    """Отдельная стратегия: при аномальном теле минутки закрыть позицию по рынку."""

    __tablename__ = "sl_anom_close_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    position_side: Mapped[str] = mapped_column(String(8))  # Buy | Sell
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Последняя обработанная закрытая 1m свеча (openTime_ms)
    last_processed_bar_open_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )

    # Когда поймали аномалию на предыдущей свече — ждём подтверждение на следующей
    pending_anomaly_bar_open_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    pending_anomaly_body: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AtrPullbackTaskRow(Base):
    """ATR Pullback: двухшаговый вход EMA+ATR, опциональная автоторговля linear."""

    __tablename__ = "atr_pullback_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    ema_fast: Mapped[int] = mapped_column(Integer)
    ema_slow: Mapped[int] = mapped_column(Integer)
    btf_interval: Mapped[str] = mapped_column(String(8))
    mtf_interval: Mapped[str] = mapped_column(String(8))
    alias: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    trading_hours_json: Mapped[str] = mapped_column(Text, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_trade: Mapped[bool] = mapped_column(Boolean, default=False)
    position_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"))
    leverage: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    state: Mapped[str] = mapped_column(
        String(16), default="idle", server_default=text("'idle'")
    )
    armed_side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    armed_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    btf_cross_bar_open_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cross_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_evaluated_btf_bar_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    last_evaluated_mtf_bar_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    last_sl_update_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def trading_hours(self) -> list[dict[str, str]]:
        raw: Any = json.loads(self.trading_hours_json or "[]")
        if not isinstance(raw, list):
            return []
        return [x for x in raw if isinstance(x, dict)]

    def set_trading_hours(self, windows: list[dict[str, str]]) -> None:
        self.trading_hours_json = json.dumps(windows, ensure_ascii=False)

    @property
    def task_key(self) -> str:
        return (
            f"{self.symbol}|{self.btf_interval}|{self.mtf_interval}|"
            f"{self.ema_fast}|{self.ema_slow}"
        )


class ScalpAdvisorTaskRow(Base):
    """Скальп M5/M1: сигналы без ордеров."""

    __tablename__ = "scalp_advisor_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    alias: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    levels_json: Mapped[str] = mapped_column(Text, default="[]")
    trading_hours_json: Mapped[str] = mapped_column(Text, default="[]")
    strategy_json: Mapped[str] = mapped_column(Text, default="{}")
    trail_hint: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    trade_state: Mapped[str] = mapped_column(
        String(16), default="idle", server_default=text("'idle'")
    )
    trade_side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    trade_sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    initial_sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_tp1: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_tp2: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp1_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    tp2_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    last_reported_sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_m5_sl_bar_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    notional_usd: Mapped[float] = mapped_column(Float, default=1000.0, server_default=text("1000"))
    last_evaluated_m1_bar_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def trading_hours(self) -> list[dict[str, str]]:
        raw: Any = json.loads(self.trading_hours_json or "[]")
        if not isinstance(raw, list):
            return []
        return [x for x in raw if isinstance(x, dict)]

    def set_trading_hours(self, windows: list[dict[str, str]]) -> None:
        self.trading_hours_json = json.dumps(windows, ensure_ascii=False)

    def level_prices(self) -> list[float]:
        raw: Any = json.loads(self.levels_json or "[]")
        if not isinstance(raw, list):
            return []
        out: list[float] = []
        for x in raw:
            try:
                out.append(float(x))
            except (TypeError, ValueError):
                continue
        return sorted(set(out))

    def set_levels(self, prices: list[float]) -> None:
        self.levels_json = json.dumps(prices, ensure_ascii=False)

    def strategy_params(self):
        from app.scalp_advisor.strategy_params import ScalpStrategyParams

        raw: Any = json.loads(self.strategy_json or "{}")
        if not isinstance(raw, dict):
            return ScalpStrategyParams()
        return ScalpStrategyParams.from_dict(raw)

    def set_strategy_params(self, params) -> None:
        self.strategy_json = json.dumps(params.to_dict(), ensure_ascii=False)


class PriceWatchRow(Base):
    """Ручной мониторинг скачков цены (linear), даже без открытой позиции."""

    __tablename__ = "price_watch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    alias: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def display_name(self) -> str:
        a = (self.alias or "").strip()
        if a:
            return f"{a} ({self.symbol})"
        return self.symbol


class PumpScanConfigRow(Base):
    """Singleton-конфиг Pump&Dump сканера (id=1)."""

    __tablename__ = "pump_scan_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    pool_json: Mapped[str] = mapped_column(Text, default="[]")
    pool_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def params(self):
        from app.pump_scan.params import PumpScanParams

        raw: Any = json.loads(self.config_json or "{}")
        if not isinstance(raw, dict):
            return PumpScanParams()
        return PumpScanParams.from_dict(raw)

    def set_params(self, params) -> None:
        self.config_json = json.dumps(params.to_dict(), ensure_ascii=False)

    def pool_coins(self) -> list:
        from app.pump_scan.universe import PoolCoin

        raw: Any = json.loads(self.pool_json or "[]")
        if not isinstance(raw, list):
            return []
        out: list[PoolCoin] = []
        for item in raw:
            if isinstance(item, dict):
                out.append(PoolCoin.from_dict(item))
        return out

    def set_pool(self, coins: list) -> None:
        self.pool_json = json.dumps([c.to_dict() for c in coins], ensure_ascii=False)


class PumpAlertOutcomeRow(Base):
    """Outcome-лог: что было после pump-алерта (для калибровки стратегии)."""

    __tablename__ = "pump_alert_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8), default="pump")
    interval: Mapped[str] = mapped_column(String(8))
    move_kind: Mapped[str] = mapped_column(String(16), default="spike")
    window_bars: Mapped[int] = mapped_column(Integer, default=1)
    alerted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    entry_price: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    features_json: Mapped[str] = mapped_column(Text, default="{}")
    ema50_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema100_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema200_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    horizon_hours: Mapped[int] = mapped_column(Integer, default=48)
    evaluated: Mapped[bool] = mapped_column(Boolean, default=False)
    mfe_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    reached_ema50: Mapped[bool] = mapped_column(Boolean, default=False)
    reached_ema100: Mapped[bool] = mapped_column(Boolean, default=False)
    reached_ema200: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def features(self) -> dict[str, Any]:
        raw: Any = json.loads(self.features_json or "{}")
        return raw if isinstance(raw, dict) else {}

    def set_features(self, data: dict[str, Any]) -> None:
        self.features_json = json.dumps(data, ensure_ascii=False)


class AdminRow(Base):
    """Telegram user id с доступом к боту (команды, FSM, кнопки ордеров)."""

    __tablename__ = "admins"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    note: Mapped[str] = mapped_column(String(128), default="", server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PumpTvhWatchRow(Base):
    """Вотчлист: ждём ТВХ после импульса pump/dump на младшем TF."""

    __tablename__ = "pump_tvh_watch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    impulse_direction: Mapped[str] = mapped_column(String(8))  # pump | dump
    source_interval: Mapped[str] = mapped_column(String(8))
    entry_interval: Mapped[str] = mapped_column(String(8))
    hit_json: Mapped[str] = mapped_column(Text, default="{}")
    impulse_low: Mapped[float] = mapped_column(Float)
    impulse_high: Mapped[float] = mapped_column(Float)
    impulse_bar_open_ms: Mapped[int] = mapped_column(BigInteger)
    alerted_short: Mapped[bool] = mapped_column(Boolean, default=False)
    alerted_long: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def hit_dict(self) -> dict[str, Any]:
        raw: Any = json.loads(self.hit_json or "{}")
        return raw if isinstance(raw, dict) else {}

    def set_hit_dict(self, data: dict[str, Any]) -> None:
        self.hit_json = json.dumps(data, ensure_ascii=False)


class BotOrderWatchRow(Base):
    """Отслеживание ордеров бота: статус на Bybit → reply в Telegram."""

    __tablename__ = "bot_order_watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    bybit_order_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    order_type: Mapped[str] = mapped_column(String(16))
    qty: Mapped[str] = mapped_column(String(32))
    price: Mapped[str] = mapped_column(String(32), default="")
    order_status: Mapped[str] = mapped_column(String(32), default="New")
    cum_exec_qty: Mapped[str] = mapped_column(String(32), default="0")
    avg_price: Mapped[str] = mapped_column(String(32), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    source: Mapped[str] = mapped_column(String(32), default="pump")
    miss_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PumpEmaAlarmRow(Base):
    """Будильник: пересечение цены и EMA 1D/1W."""

    __tablename__ = "pump_ema_alarms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    ema_key: Mapped[str] = mapped_column(String(8))
    direction: Mapped[str] = mapped_column(String(8))  # up | down | both
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    last_ema_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    trigger_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PumpEntryWatchSuggestionRow(Base):
    """План слежения от DeepSeek, привязанный к сообщению алерта."""

    __tablename__ = "pump_entry_watch_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    source_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    source_message_id: Mapped[int] = mapped_column(Integer, index=True)
    impulse_price: Mapped[float] = mapped_column(Float)
    impulse_interval: Mapped[str] = mapped_column(String(8), default="15")
    entry_timing: Mapped[str] = mapped_column(String(16), default="unknown")
    watch_if_early: Mapped[bool] = mapped_column(Boolean, default=False)
    watch_plan_json: Mapped[str] = mapped_column(Text, default="{}")
    alert_text: Mapped[str] = mapped_column(Text, default="")
    analysis_excerpt: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PumpEntryWatchRow(Base):
    """Активное слежение до окна входа (Funding+OI + LLM)."""

    __tablename__ = "pump_entry_watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    impulse_price: Mapped[float] = mapped_column(Float)
    impulse_interval: Mapped[str] = mapped_column(String(8), default="15")
    alert_text: Mapped[str] = mapped_column(Text, default="")
    # Первое заключение DeepSeek (на момент алерта) — для преемственности при re-eval
    initial_analysis: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    initial_entry_timing: Mapped[str] = mapped_column(
        String(16), default="unknown", server_default=text("'unknown'")
    )
    # История повторных заключений: [{at, entry_ok, timing, text}, ...]
    analysis_history_json: Mapped[str] = mapped_column(
        Text, default="[]", server_default=text("'[]'")
    )
    watch_plan_json: Mapped[str] = mapped_column(Text, default="{}")
    baseline_metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    last_metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    high_watermark_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_phase: Mapped[str] = mapped_column(
        String(32), default="squeeze_building", server_default=text("'squeeze_building'")
    )
    phase_history_json: Mapped[str] = mapped_column(
        Text, default="[]", server_default=text("'[]'")
    )
    last_phase_notified: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # active | done | invalidated | expired | cancelled
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    llm_eval_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    plan_adjusted: Mapped[bool] = mapped_column(Boolean, default=False)
    completion_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_llm_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
