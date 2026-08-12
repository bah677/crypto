"""Настраиваемые параметры Pump&Dump сканера."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass
class PumpScanParams:
    # Вселенная монет (Bybit linear + CoinGecko hype)
    min_bybit_turnover_usd: float = 1_000_000.0
    top_turnover_rank: int = 200
    min_market_cap_usd: float = 5_000_000.0
    min_volume_24h_usd: float = 1_000_000.0
    min_coin_age_days: int = 7
    include_trending: bool = True
    include_gainers: bool = True
    max_pool_size: int = 400
    allow_extreme_risk: bool = True

    # Детекция: резкий памп (1 свеча) — глобальные фильтры
    rvol_lookback: int = 20
    rvol_sustain_bars: int = 1
    max_upper_wick_body_ratio: float = 0.5
    max_lower_wick_body_ratio: float = 0.5
    dump_filter_bars: int = 3
    dump_filter_pct: float = -20.0

    # Таймфреймы (Bybit: 5,15,30,60,240,D)
    scan_intervals_fast: str = "5,15,30,60"
    scan_intervals_slow: str = "240,D"

    # Плавный памп / дамп
    smooth_pump_enabled: bool = True
    smooth_dump_enabled: bool = True
    dump_detection_enabled: bool = False

    # LunarCrush (ключ в .env)
    lunarcrush_in_alerts: bool = True
    lunarcrush_spike_ratio: float = 2.0

    # Устаревшие — миграция из старых конфигов
    kline_interval: str = "5"
    rvol_threshold: float = 3.0
    price_change_pct: float = 5.0
    min_green_red_vol_ratio: float = 2.0
    smooth_pump_bars: int = 6
    smooth_pump_pct: float = 10.0
    smooth_pump_rvol: float = 2.0
    smooth_pump_min_green_ratio: float = 1.2

    # Расписание
    universe_refresh_hours: int = 1
    scan_interval_min: int = 5
    alert_cooldown_min: int = 60

    # ТВХ: вотчлист после импульса, алерт только при готовой точке входа
    tvh_watch_ttl_min: int = 180
    tvh_min_score: int = 45
    tvh_ema_fast: int = 9
    tvh_ema_slow: int = 21
    tvh_min_retrace_fade: float = 0.08
    tvh_pullback_min: float = 0.18
    tvh_pullback_max: float = 0.58
    tvh_swing_lookback: int = 6
    tvh_one_shot_watch: bool = False

    # --- TZ: Pump-in-Downtrend (fade) modules ---
    # 2) Trend Context (1D downtrend)
    trend_context_enabled: bool = True
    trend_context_lookback_days: int = 90
    downtrend_min_drawdown_pct: float = -45.0
    downtrend_min_days_since_high: int = 14
    min_bars_for_ema50: int = 50
    min_bars_for_ema100: int = 100
    min_bars_for_ema200: int = 200
    young_coin_min_days_since_high: int = 5
    downtrend_mode: str = "filter"  # filter | boost | tag_only
    downtrend_score_multiplier: float = 1.5

    # 3) Open Interest analysis
    oi_analysis_enabled: bool = True
    oi_window_bars: int = 6
    oi_squeeze_max_chg_pct: float = 3.0
    oi_new_money_min_chg_pct: float = 15.0
    oi_new_money_score_penalty: float = 0.5
    oi_squeeze_score_bonus: float = 1.3
    oi_new_money_hard_block: bool = False

    # 4) Volume climax / divergence
    volume_climax_enabled: bool = True
    climax_bars: int = 3
    climax_volume_ratio: float = 1.3
    climax_price_decay_ratio: float = 0.7
    climax_wick_ratio_threshold: float = 0.35
    climax_score_bonus: float = 1.4
    climax_score_bonus_weak: float = 1.15

    # 5) Funding rate of change (ROC)
    funding_roc_enabled: bool = True
    funding_lookback_periods: int = 3
    funding_spike_threshold_pct: float = 300.0  # annual %-points

    # 5b) Funding + OI Trajectory (композитный индикатор)
    funding_trajectory_enabled: bool = True
    funding_history_lookback_hours: int = 48
    funding_extreme_threshold_pct: float = 1000.0
    funding_recovery_min_periods: int = 2
    funding_noise_tolerance_pct: float = 20.0
    funding_normalized_threshold_pct: float = 100.0
    oi_history_lookback_hours: int = 48
    oi_history_interval: str = "1h"
    oi_trend_flat_threshold_pct: float = 2.0
    funding_oi_score_bonus_best: float = 1.6
    funding_oi_score_penalty_worst: float = 0.4
    funding_oi_score_penalty_late: float = 0.7

    # 6) Market isolation vs BTC
    market_isolation_enabled: bool = True
    isolation_btc_chg_threshold: float = 1.5
    isolation_min_btc_chg: float = 0.3
    isolation_score_bonus: float = 1.2

    # 7) Distance-to-EMA / ATR metric
    distance_to_ema_enabled: bool = True
    atr_period_1d: int = 14
    distance_near_threshold_atr: float = 1.0

    # 9.1) Alert outcome logging/eval
    outcome_logging_enabled: bool = False
    outcome_check_horizon_hours: int = 48

    # 9.2) ATR-based stop & sizing helper
    atr_stop_sizing_enabled: bool = False
    stop_atr_multiplier: float = 1.0
    fixed_risk_usd: float = 25.0

    # 9.3) Orderbook slippage check (market button)
    orderbook_check_enabled: bool = False
    orderbook_check_usd: float = 5000.0
    orderbook_max_slippage_pct: float = 1.5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> PumpScanParams:
        if not raw:
            return cls()
        kw: dict[str, Any] = {}
        valid = {f.name for f in fields(cls)}
        for k, v in raw.items():
            if k in valid:
                kw[k] = v
        # устаревшие поля из старых конфигов
        kw.pop("dump_filter_hours", None)
        kw.pop("downtrend_require_ema_stack", None)
        if "dump_filter_bars" not in kw:
            kw.setdefault("dump_filter_bars", 3)
        return cls(**kw)


FIELD_LABELS: dict[str, str] = {
    "min_bybit_turnover_usd": "Мин. оборот Bybit 24h, $",
    "top_turnover_rank": "Граница топа по обороту",
    "min_market_cap_usd": "CG: мин. cap, $",
    "min_volume_24h_usd": "CG: мин. vol 24h, $",
    "min_coin_age_days": "CG: мин. возраст, дней",
    "include_trending": "CoinGecko Trending",
    "include_gainers": "CoinGecko Top Gainers 1h",
    "max_pool_size": "Макс. размер пула",
    "allow_extreme_risk": "Хайп ниже порога CG",
    "scan_intervals_fast": "TF быстрый скан",
    "scan_intervals_slow": "TF медленный скан",
    "dump_detection_enabled": "Детекция дампа (только TF ≤ 1h)",
    "smooth_dump_enabled": "Плавный дамп",
    "max_lower_wick_body_ratio": "Макс. нижняя тень / тело",
    "lunarcrush_in_alerts": "LunarCrush в алертах",
    "lunarcrush_spike_ratio": "LunarCrush social ×",
    "rvol_lookback": "RVOL: lookback свечей",
    "rvol_sustain_bars": "RVOL подряд свечей",
    "max_upper_wick_body_ratio": "Макс. верхняя тень / тело",
    "dump_filter_bars": "Фильтр pump: окно, баров",
    "dump_filter_pct": "Фильтр pump: порог минуса, %",
    "smooth_pump_enabled": "Плавный памп: вкл",
    "universe_refresh_hours": "Обновление пула, ч",
    "scan_interval_min": "Интервал скана, мин",
    "alert_cooldown_min": "Cooldown алерта, мин",
    "tvh_watch_ttl_min": "ТВХ: TTL вотчлиста, мин",
    "tvh_min_score": "ТВХ: мин. score",
    "tvh_ema_fast": "ТВХ: EMA fast",
    "tvh_ema_slow": "ТВХ: EMA slow",
    "tvh_min_retrace_fade": "ТВХ: мин. откат фейда",
    "tvh_pullback_min": "ТВХ: откат продолж., мин",
    "tvh_pullback_max": "ТВХ: откат продолж., макс",
    "tvh_swing_lookback": "ТВХ: swing lookback",
    "tvh_one_shot_watch": "ТВХ: один алерт и снять",
    # TZ: new modules
    "trend_context_enabled": "TrendContext: вкл",
    "trend_context_lookback_days": "TrendContext: lookback дней",
    "downtrend_min_drawdown_pct": "Downtrend: мин. просадка от хая, %",
    "downtrend_min_days_since_high": "Downtrend: мин. дней с хая",
    "min_bars_for_ema50": "Trend: мин. баров для EMA50",
    "min_bars_for_ema100": "Trend: мин. баров для EMA100",
    "min_bars_for_ema200": "Trend: мин. баров для EMA200",
    "young_coin_min_days_since_high": "Trend: мин. дней с ATH (молодые)",
    "downtrend_mode": "Downtrend: режим (filter/boost/tag_only)",
    "downtrend_score_multiplier": "Downtrend: множитель score",
    "oi_analysis_enabled": "OI: анализ вкл",
    "oi_window_bars": "OI: окно, баров",
    "oi_squeeze_max_chg_pct": "OI: squeeze максимум %,",
    "oi_new_money_min_chg_pct": "OI: new money минимум %,",
    "oi_new_money_score_penalty": "OI: штраф score (new money)",
    "oi_squeeze_score_bonus": "OI: бонус score (squeeze)",
    "oi_new_money_hard_block": "OI: hard block new money",
    "volume_climax_enabled": "Climax: анализ вкл",
    "climax_bars": "Climax: окно, баров",
    "climax_volume_ratio": "Climax: объём × к prev",
    "climax_price_decay_ratio": "Climax: движение × к prev",
    "climax_wick_ratio_threshold": "Climax: верхняя тень доля",
    "climax_score_bonus": "Climax: бонус score (strong)",
    "climax_score_bonus_weak": "Climax: бонус score (weak)",
    "funding_roc_enabled": "Funding ROC: вкл",
    "funding_lookback_periods": "Funding ROC: периодов назад",
    "funding_spike_threshold_pct": "Funding ROC: порог, п.п.",
    "funding_trajectory_enabled": "Funding+OI Trajectory: вкл",
    "funding_history_lookback_hours": "Trajectory: funding lookback, ч",
    "funding_extreme_threshold_pct": "Trajectory: порог экстремума, %",
    "funding_recovery_min_periods": "Trajectory: мин. периодов разворота",
    "funding_noise_tolerance_pct": "Trajectory: шум, п.п.",
    "funding_normalized_threshold_pct": "Trajectory: порог нормализации, %",
    "oi_history_lookback_hours": "Trajectory: OI lookback, ч",
    "oi_history_interval": "Trajectory: интервал OI",
    "oi_trend_flat_threshold_pct": "Trajectory: OI flat порог, %",
    "funding_oi_score_bonus_best": "Trajectory: бонус score (лучший)",
    "funding_oi_score_penalty_worst": "Trajectory: штраф score (худший)",
    "funding_oi_score_penalty_late": "Trajectory: штраф score (поздно)",
    "market_isolation_enabled": "Isolation: вкл",
    "isolation_btc_chg_threshold": "Isolation: BTC порог, %",
    "isolation_min_btc_chg": "Isolation: min BTC %,",
    "isolation_score_bonus": "Isolation: бонус score",
    "distance_to_ema_enabled": "Distance-to-EMA: вкл",
    "atr_period_1d": "ATR(1D): период",
    "distance_near_threshold_atr": "Distance-to-EMA: near, ATR",
    "outcome_logging_enabled": "Outcome logging: вкл",
    "outcome_check_horizon_hours": "Outcome: горизонт, ч",
    "atr_stop_sizing_enabled": "ATR stop/sizing: вкл",
    "stop_atr_multiplier": "ATR stop: множитель",
    "fixed_risk_usd": "Sizing: риск на сделку, $",
    "orderbook_check_enabled": "Orderbook check: вкл",
    "orderbook_check_usd": "Orderbook: объём проверки, $",
    "orderbook_max_slippage_pct": "Orderbook: max slippage, %",
}

EDIT_GROUPS: dict[str, list[str]] = {
    "universe": [
        "min_bybit_turnover_usd",
        "top_turnover_rank",
        "max_pool_size",
        "include_trending",
        "include_gainers",
        "allow_extreme_risk",
        "min_market_cap_usd",
        "min_volume_24h_usd",
    ],
    "detect": [
        "scan_intervals_fast",
        "scan_intervals_slow",
        "dump_detection_enabled",
        "smooth_pump_enabled",
        "smooth_dump_enabled",
        "lunarcrush_in_alerts",
        "lunarcrush_spike_ratio",
        "rvol_lookback",
        "max_upper_wick_body_ratio",
        "max_lower_wick_body_ratio",
        "dump_filter_bars",
        "dump_filter_pct",
    ],
    "schedule": [
        "universe_refresh_hours",
        "scan_interval_min",
        "alert_cooldown_min",
    ],
    "tvh": [
        "tvh_watch_ttl_min",
        "tvh_min_score",
        "tvh_ema_fast",
        "tvh_ema_slow",
        "tvh_min_retrace_fade",
        "tvh_pullback_min",
        "tvh_pullback_max",
        "tvh_swing_lookback",
        "tvh_one_shot_watch",
    ],
    "downtrend": [
        "trend_context_enabled",
        "trend_context_lookback_days",
        "downtrend_min_drawdown_pct",
        "downtrend_min_days_since_high",
        "min_bars_for_ema50",
        "min_bars_for_ema100",
        "min_bars_for_ema200",
        "young_coin_min_days_since_high",
        "downtrend_mode",
        "downtrend_score_multiplier",
    ],
    "oi": [
        "oi_analysis_enabled",
        "oi_window_bars",
        "oi_squeeze_max_chg_pct",
        "oi_new_money_min_chg_pct",
        "oi_new_money_score_penalty",
        "oi_squeeze_score_bonus",
        "oi_new_money_hard_block",
    ],
    "climax": [
        "volume_climax_enabled",
        "climax_bars",
        "climax_volume_ratio",
        "climax_price_decay_ratio",
        "climax_wick_ratio_threshold",
        "climax_score_bonus",
        "climax_score_bonus_weak",
    ],
    "funding_roc": [
        "funding_roc_enabled",
        "funding_lookback_periods",
        "funding_spike_threshold_pct",
    ],
    "funding_oi": [
        "funding_trajectory_enabled",
        "funding_history_lookback_hours",
        "funding_extreme_threshold_pct",
        "funding_recovery_min_periods",
        "funding_noise_tolerance_pct",
        "funding_normalized_threshold_pct",
        "oi_history_lookback_hours",
        "oi_history_interval",
        "oi_trend_flat_threshold_pct",
        "funding_oi_score_bonus_best",
        "funding_oi_score_penalty_worst",
        "funding_oi_score_penalty_late",
    ],
    "isolation": [
        "market_isolation_enabled",
        "isolation_btc_chg_threshold",
        "isolation_min_btc_chg",
        "isolation_score_bonus",
    ],
    "distance": [
        "distance_to_ema_enabled",
        "atr_period_1d",
        "distance_near_threshold_atr",
    ],
    "outcomes": [
        "outcome_logging_enabled",
        "outcome_check_horizon_hours",
    ],
    "risk_sizing": [
        "atr_stop_sizing_enabled",
        "stop_atr_multiplier",
        "fixed_risk_usd",
    ],
    "orderbook": [
        "orderbook_check_enabled",
        "orderbook_check_usd",
        "orderbook_max_slippage_pct",
    ],
}

GROUP_LABELS: dict[str, str] = {
    "universe": "🌐 Вселенная монет",
    "detect": "🔥 Pump / Dump / TF",
    "schedule": "⏱ Расписание",
    "tvh": "🎯 ТВХ (вотчлист)",
    "downtrend": "📉 Pump-in-Downtrend",
    "oi": "📊 Open Interest",
    "climax": "⚡ Volume Climax",
    "funding_roc": "📈 Funding ROC",
    "funding_oi": "🧭 Funding + OI Trajectory",
    "isolation": "🧊 Market Isolation",
    "distance": "📍 Distance-to-EMA / ATR",
    "outcomes": "🧪 Outcome logging",
    "risk_sizing": "🧮 ATR stop & sizing",
    "orderbook": "📚 Orderbook slippage",
}

STR_FIELDS = frozenset(
    {
        "scan_intervals_fast",
        "scan_intervals_slow",
        "downtrend_mode",
        "oi_history_interval",
    }
)

BOOL_FIELDS = frozenset(
    {
        "include_trending",
        "include_gainers",
        "allow_extreme_risk",
        "smooth_pump_enabled",
        "smooth_dump_enabled",
        "dump_detection_enabled",
        "lunarcrush_in_alerts",
        "tvh_one_shot_watch",
        "trend_context_enabled",
        "oi_analysis_enabled",
        "oi_new_money_hard_block",
        "volume_climax_enabled",
        "funding_roc_enabled",
        "funding_trajectory_enabled",
        "market_isolation_enabled",
        "distance_to_ema_enabled",
        "outcome_logging_enabled",
        "atr_stop_sizing_enabled",
        "orderbook_check_enabled",
    }
)
INT_FIELDS = frozenset(
    {
        "top_turnover_rank",
        "min_coin_age_days",
        "max_pool_size",
        "rvol_lookback",
        "rvol_sustain_bars",
        "universe_refresh_hours",
        "scan_interval_min",
        "alert_cooldown_min",
        "dump_filter_bars",
        "tvh_watch_ttl_min",
        "tvh_min_score",
        "tvh_ema_fast",
        "tvh_ema_slow",
        "tvh_swing_lookback",
        "trend_context_lookback_days",
        "downtrend_min_days_since_high",
        "min_bars_for_ema50",
        "min_bars_for_ema100",
        "min_bars_for_ema200",
        "young_coin_min_days_since_high",
        "oi_window_bars",
        "climax_bars",
        "funding_lookback_periods",
        "funding_history_lookback_hours",
        "funding_recovery_min_periods",
        "oi_history_lookback_hours",
        "atr_period_1d",
        "outcome_check_horizon_hours",
    }
)
FLOAT_FIELDS = frozenset(
    {
        "min_bybit_turnover_usd",
        "min_market_cap_usd",
        "min_volume_24h_usd",
        "dump_filter_pct",
        "max_upper_wick_body_ratio",
        "max_lower_wick_body_ratio",
        "lunarcrush_spike_ratio",
        "tvh_min_retrace_fade",
        "tvh_pullback_min",
        "tvh_pullback_max",
        "downtrend_min_drawdown_pct",
        "downtrend_score_multiplier",
        "oi_squeeze_max_chg_pct",
        "oi_new_money_min_chg_pct",
        "oi_new_money_score_penalty",
        "oi_squeeze_score_bonus",
        "climax_volume_ratio",
        "climax_price_decay_ratio",
        "climax_wick_ratio_threshold",
        "climax_score_bonus",
        "climax_score_bonus_weak",
        "funding_spike_threshold_pct",
        "funding_extreme_threshold_pct",
        "funding_noise_tolerance_pct",
        "funding_normalized_threshold_pct",
        "oi_trend_flat_threshold_pct",
        "funding_oi_score_bonus_best",
        "funding_oi_score_penalty_worst",
        "funding_oi_score_penalty_late",
        "isolation_btc_chg_threshold",
        "isolation_min_btc_chg",
        "isolation_score_bonus",
        "distance_near_threshold_atr",
        "stop_atr_multiplier",
        "fixed_risk_usd",
        "orderbook_check_usd",
        "orderbook_max_slippage_pct",
    }
)


def parse_field_value(field: str, text: str) -> Any:
    raw = text.strip().replace(",", ".")
    if field in BOOL_FIELDS:
        low = raw.lower()
        if low in ("1", "true", "да", "yes", "on", "вкл"):
            return True
        if low in ("0", "false", "нет", "no", "off", "выкл"):
            return False
        raise ValueError("Введите да/нет или 1/0")
    if field in INT_FIELDS:
        val = int(float(raw))
        if field == "tvh_min_score" and not (0 <= val <= 100):
            raise ValueError("Score должен быть от 0 до 100")
        if field in ("tvh_ema_fast", "tvh_ema_slow") and val < 2:
            raise ValueError("Период EMA должен быть ≥ 2")
        if val < 0:
            raise ValueError("Число должно быть ≥ 0")
        return val
    if field in STR_FIELDS:
        if field == "downtrend_mode":
            v = raw.strip().lower()
            if v not in ("filter", "boost", "tag_only"):
                raise ValueError("Введите: filter / boost / tag_only")
            return v
        return raw
    if field in FLOAT_FIELDS:
        val = float(raw)
        if field in ("tvh_pullback_min", "tvh_pullback_max", "tvh_min_retrace_fade"):
            if val > 1.0 and val <= 100.0:
                val = val / 100.0
            if not (0.0 < val < 1.0):
                raise ValueError("Доля от 0 до 1 (или проценты, напр. 22)")
        return val
    raise ValueError(f"Неизвестное поле: {field}")


def field_prompt(field: str, current: Any) -> str:
    label = FIELD_LABELS.get(field, field)
    disp = current
    if isinstance(current, float) and field.startswith("tvh_") and field != "tvh_min_score":
        if 0 < current < 1:
            disp = f"{current * 100:.0f}% ({current})"
    lines = [f"<b>{label}</b>", f"Сейчас: <code>{disp}</code>", ""]
    if field in BOOL_FIELDS:
        lines.append("<b>Что ввести:</b> <code>да</code> / <code>нет</code>")
    elif field in STR_FIELDS:
        lines.append("<b>Что ввести:</b> через запятую, напр. <code>5,15,30,60</code> или <code>240,D</code>")
    elif field in INT_FIELDS:
        lines.append("<b>Что ввести:</b> целое число")
    else:
        lines.append("<b>Что ввести:</b> число (можно с точкой)")
    hints = {
        "min_bybit_turnover_usd": "Базовый пул: все linear USDT на Bybit с оборотом ≥ порога.",
        "top_turnover_rank": "Монеты ниже этого ранга по обороту — метка «вне топ-200».",
        "scan_intervals_fast": "Скан каждые N мин: 5m, 15m, 30m, 1h.",
        "scan_intervals_slow": "Скан раз в час: 4h и 1D (меньше запросов к API).",
        "dump_detection_enabled": "Только 5m–1h; на 4h/1D — только pump.",
        "lunarcrush_spike_ratio": "Показывать social × только если выше порога.",
        "dump_filter_bars": "Не сигналить pump, если за N баров на этом TF уже был сильный минус.",
        "dump_filter_pct": "Порог минуса для фильтра (напр. −20).",
        "tvh_watch_ttl_min": "Сколько минут держать монету в вотчлисте после импульса. При новых экстремумах TTL продлевается.",
        "tvh_min_score": "Алерт в топик только если качество ТВХ ≥ порога (0–100).",
        "tvh_ema_fast": "EMA на младшем TF для отбоя и перелома.",
        "tvh_ema_slow": "EMA slow на младшем TF для фильтра тренда.",
        "tvh_min_retrace_fade": "Мин. откат от экстремума для фейда (0.08 = 8%, быстрый pump-dump).",
        "tvh_pullback_min": "Мин. откат для лонга/шорта на продолжение (0.18 = 18%).",
        "tvh_pullback_max": "Макс. откат для продолжения (глубже — слабость тренда).",
        "tvh_swing_lookback": "Свечей для локального swing (6 — быстрее на 1m/5m).",
        "tvh_one_shot_watch": "После первого алерта снять монету с вотчлиста.",
    }
    if field in hints:
        lines.append(f"<b>На что влияет:</b> {hints[field]}")
    return "\n".join(lines)
