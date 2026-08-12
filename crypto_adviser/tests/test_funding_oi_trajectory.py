"""Тесты Funding + OI Trajectory (ТЗ, раздел 8)."""

from __future__ import annotations

import unittest

from app.pump_scan.funding_oi_trajectory import (
    build_trajectory_alert_line,
    classify_funding_trajectory,
    classify_oi_trend,
    evaluate_funding_oi_trajectory,
    funding_confidence_from_interval,
    funding_oi_score_multiplier,
)
from app.pump_scan.params import PumpScanParams


class FundingOiTrajectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.params = PumpScanParams()

    def test_case1_peak_reversing_falling(self) -> None:
        series = [-1400.0, -1200.0, -900.0, -700.0, -600.0]
        state = classify_funding_trajectory(
            series,
            extreme_threshold_pct=1000.0,
            recovery_min_periods=2,
            noise_tolerance_pct=20.0,
            normalized_threshold_pct=100.0,
        )
        self.assertEqual(state, "peak_reversing")
        mult = funding_oi_score_multiplier(state, "falling", self.params)
        self.assertAlmostEqual(mult, 1.6)
        ctx = evaluate_funding_oi_trajectory(
            funding_series=series,
            oi_series=[100.0, 95.0, 90.0],
            funding_interval_hours=1.0,
            params=self.params,
        )
        assert ctx is not None
        self.assertIn("🟢", ctx.alert_line or "")

    def test_case2_extending_at_minimum(self) -> None:
        series = [-1200.0, -1300.0, -1500.0]
        state = classify_funding_trajectory(
            series,
            extreme_threshold_pct=1000.0,
            recovery_min_periods=2,
            noise_tolerance_pct=20.0,
            normalized_threshold_pct=100.0,
        )
        self.assertEqual(state, "extending")
        mult = funding_oi_score_multiplier(state, "falling", self.params)
        self.assertAlmostEqual(mult, 1.0)

    def test_case3_extending_rising_worst(self) -> None:
        series = [-1200.0, -1400.0, -1500.0]
        state = classify_funding_trajectory(
            series,
            extreme_threshold_pct=1000.0,
            recovery_min_periods=2,
            noise_tolerance_pct=20.0,
            normalized_threshold_pct=100.0,
        )
        self.assertEqual(state, "extending")
        mult = funding_oi_score_multiplier(state, "rising", self.params)
        self.assertAlmostEqual(mult, 0.4)
        line = build_trajectory_alert_line(
            funding_state=state,
            oi_trend="rising",
            funding_now=-1500.0,
            funding_min=-1500.0,
            funding_interval_hours=1.0,
            funding_confidence="high",
        )
        self.assertIn("🔴", line or "")

    def test_case4_normalized(self) -> None:
        series = [-1200.0, -800.0, -200.0, -40.0]
        state = classify_funding_trajectory(
            series,
            extreme_threshold_pct=1000.0,
            recovery_min_periods=2,
            noise_tolerance_pct=20.0,
            normalized_threshold_pct=100.0,
        )
        self.assertEqual(state, "normalized")
        mult = funding_oi_score_multiplier(state, "flat", self.params)
        self.assertAlmostEqual(mult, 0.7)

    def test_case5_no_extreme(self) -> None:
        series = [-500.0, -550.0, -600.0]
        state = classify_funding_trajectory(
            series,
            extreme_threshold_pct=1000.0,
            recovery_min_periods=2,
            noise_tolerance_pct=20.0,
            normalized_threshold_pct=100.0,
        )
        self.assertEqual(state, "no_extreme")
        ctx = evaluate_funding_oi_trajectory(
            funding_series=series,
            oi_series=[100.0, 98.0],
            funding_interval_hours=1.0,
            params=self.params,
        )
        assert ctx is not None
        self.assertIsNotNone(ctx.alert_line)
        self.assertIn("сигнала на вход нет", ctx.alert_line or "")
        self.assertIn("экстремума фандинга нет", ctx.alert_line or "")
        self.assertAlmostEqual(ctx.score_multiplier, 1.0)

    def test_unknown_always_has_line(self) -> None:
        ctx = evaluate_funding_oi_trajectory(
            funding_series=None,
            oi_series=None,
            funding_interval_hours=None,
            params=self.params,
        )
        assert ctx is not None
        self.assertEqual(ctx.funding_trajectory_state, "unknown")
        self.assertIn("данные недоступны", ctx.alert_line or "")

    def test_case6_recovery_not_confirmed(self) -> None:
        series = [-1300.0, -700.0]
        state = classify_funding_trajectory(
            series,
            extreme_threshold_pct=1000.0,
            recovery_min_periods=2,
            noise_tolerance_pct=20.0,
            normalized_threshold_pct=100.0,
        )
        self.assertEqual(state, "extending")

    def test_case7_low_confidence_suffix(self) -> None:
        self.assertEqual(funding_confidence_from_interval(8.0), "low")
        line = build_trajectory_alert_line(
            funding_state="peak_reversing",
            oi_trend="falling",
            funding_now=-600.0,
            funding_min=-1400.0,
            funding_interval_hours=8.0,
            funding_confidence="low",
        )
        self.assertIn("интервал фандинга 8ч", line or "")
        self.assertIn("low", line or "")

    def test_oi_trend_classification(self) -> None:
        self.assertEqual(classify_oi_trend([100.0, 95.0], flat_threshold_pct=2.0), "falling")
        self.assertEqual(classify_oi_trend([100.0, 103.0], flat_threshold_pct=2.0), "rising")
        self.assertEqual(classify_oi_trend([100.0, 101.0], flat_threshold_pct=2.0), "flat")


if __name__ == "__main__":
    unittest.main()
