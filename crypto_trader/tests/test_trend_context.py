"""Тест-кейсы Trend Context по ТЗ (раздел 5)."""

from __future__ import annotations

import unittest

from app.pump_scan.params import PumpScanParams
from app.pump_scan.trend_context import (
    evaluate_trend_context,
    format_trend_alert_line,
    should_drop_by_downtrend_filter,
)


def _lookback_highs(n: int, ath_index: int, ath: float, flat: float = 100.0) -> list[float]:
    highs = [flat] * n
    highs[ath_index] = ath
    return highs


class TestTrendContextTZ(unittest.TestCase):
    def setUp(self) -> None:
        self.params = PumpScanParams()

    def test_1_full_bearish_stack_alert(self) -> None:
        """EMA50<EMA100<EMA200, цена ниже всех, просадка -60% за 20д → Ветка B."""
        params = self.params
        n = 90
        highs = _lookback_highs(n, n - 1 - 20, 250.0)
        close = 100.0  # -60% от 250
        trend = evaluate_trend_context(
            history_days=220,
            close_prev=close,
            ema50=110.0,
            ema100=120.0,
            ema200=130.0,
            daily_highs=highs,
            params=params,
        )
        assert trend is not None
        assert trend.data_status == "full"
        assert trend.is_downtrend_context is True
        assert trend.stack_bearish is True
        assert should_drop_by_downtrend_filter(trend, "filter") is False
        line = format_trend_alert_line(trend, params)
        assert line is not None and "🎯" in line

    def test_2_bullish_stack_dropped(self) -> None:
        """Бычий стек → Ветка C, алерт не отправляется."""
        params = self.params
        n = 90
        highs = _lookback_highs(n, n - 1 - 20, 250.0)
        trend = evaluate_trend_context(
            history_days=220,
            close_prev=100.0,
            ema50=130.0,
            ema100=120.0,
            ema200=110.0,
            daily_highs=highs,
            params=params,
        )
        assert trend is not None
        assert trend.stack_bearish is False
        assert trend.is_downtrend_context is False
        assert should_drop_by_downtrend_filter(trend, "filter") is True

    def test_3_reclaim_above_ema50_dropped(self) -> None:
        """EMA50<EMA100, но цена выше EMA50 → Ветка C."""
        params = self.params
        n = 90
        highs = _lookback_highs(n, n - 1 - 20, 250.0)
        trend = evaluate_trend_context(
            history_days=220,
            close_prev=115.0,
            ema50=110.0,
            ema100=120.0,
            ema200=130.0,
            daily_highs=highs,
            params=params,
        )
        assert trend is not None
        assert trend.price_below_emas is False
        assert trend.is_downtrend_context is False
        assert should_drop_by_downtrend_filter(trend, "filter") is True

    def test_4_mixed_ema_order_dropped(self) -> None:
        """EMA100<EMA50<EMA200 → stack_bearish=False."""
        params = self.params
        n = 90
        highs = _lookback_highs(n, n - 1 - 20, 250.0)
        trend = evaluate_trend_context(
            history_days=220,
            close_prev=100.0,
            ema50=120.0,
            ema100=110.0,
            ema200=130.0,
            daily_highs=highs,
            params=params,
        )
        assert trend is not None
        assert trend.stack_bearish is False
        assert trend.is_downtrend_context is False

    def test_5_insufficient_history_passes(self) -> None:
        """< 50 баров → Ветка A, фильтр не блокирует."""
        params = self.params
        highs = [100.0] * 30
        trend = evaluate_trend_context(
            history_days=30,
            close_prev=100.0,
            ema50=None,
            ema100=None,
            ema200=None,
            daily_highs=highs,
            params=params,
        )
        assert trend is not None
        assert trend.data_status == "insufficient_history"
        assert trend.not_applicable is True
        assert should_drop_by_downtrend_filter(trend, "filter") is False
        line = format_trend_alert_line(trend, params)
        assert line is not None and "ℹ️" in line

    def test_6_partial_history_alert(self) -> None:
        """150д, EMA50<EMA100, цена ниже, просадка достаточна → Ветка B + partial."""
        params = self.params
        n = 90
        highs = _lookback_highs(n, n - 1 - 20, 250.0)
        trend = evaluate_trend_context(
            history_days=150,
            close_prev=100.0,
            ema50=110.0,
            ema100=120.0,
            ema200=None,
            daily_highs=highs,
            params=params,
        )
        assert trend is not None
        assert trend.data_status == "partial"
        assert trend.is_downtrend_context is True
        line = format_trend_alert_line(trend, params)
        assert line is not None and "EMA200 недоступна" in line

    def test_7_partial_bullish_dropped(self) -> None:
        """150д, EMA50>EMA100 → Ветка C."""
        params = self.params
        n = 90
        highs = _lookback_highs(n, n - 1 - 20, 250.0)
        trend = evaluate_trend_context(
            history_days=150,
            close_prev=100.0,
            ema50=120.0,
            ema100=110.0,
            ema200=None,
            daily_highs=highs,
            params=params,
        )
        assert trend is not None
        assert trend.is_downtrend_context is False
        assert should_drop_by_downtrend_filter(trend, "filter") is True

    def test_8_young_partial_downtrend(self) -> None:
        """60д, ниже EMA50, ATH 20д назад, -55% → Ветка A2 🌱."""
        params = self.params
        n = 60
        highs = _lookback_highs(n, n - 1 - 20, 220.0)
        close = 220.0 * 0.45  # -55%
        trend = evaluate_trend_context(
            history_days=60,
            close_prev=close,
            ema50=close + 10.0,
            ema100=None,
            ema200=None,
            daily_highs=highs,
            params=params,
        )
        assert trend is not None
        assert trend.data_status == "young_partial"
        assert trend.is_downtrend_context is True
        line = format_trend_alert_line(trend, params)
        assert line is not None and "🌱" in line

    def test_9_young_at_ath_dropped(self) -> None:
        """55д на ATH — первый памп без просадки."""
        params = self.params
        n = 55
        highs = [100.0] * n
        highs[-1] = 200.0  # текущий бар = ATH
        trend = evaluate_trend_context(
            history_days=55,
            close_prev=200.0,
            ema50=210.0,
            ema100=None,
            ema200=None,
            daily_highs=highs,
            params=params,
        )
        assert trend is not None
        assert trend.data_status == "young_partial"
        assert trend.is_downtrend_context is False
        assert should_drop_by_downtrend_filter(trend, "filter") is True

    def test_10_young_recent_ath_dropped(self) -> None:
        """55д, ниже EMA50, ATH 3д назад (< young_coin_min_days_since_high=5)."""
        params = self.params
        n = 55
        highs = _lookback_highs(n, n - 1 - 3, 200.0)
        close = 90.0
        trend = evaluate_trend_context(
            history_days=55,
            close_prev=close,
            ema50=100.0,
            ema100=None,
            ema200=None,
            daily_highs=highs,
            params=params,
        )
        assert trend is not None
        assert trend.days_since_high == 3
        assert trend.is_downtrend_context is False
        assert should_drop_by_downtrend_filter(trend, "filter") is True

    def test_default_mode_is_filter(self) -> None:
        assert self.params.downtrend_mode == "filter"


if __name__ == "__main__":
    unittest.main()
