"""Тесты entry-watch plan / parser."""

from __future__ import annotations

import unittest

from app.pump_scan.entry_watch_plan import (
    classify_squeeze_phase,
    default_watch_plan,
    evaluate_watch_plan,
    format_metrics_ru,
    format_phase_transition_ru,
    normalize_watch_plan,
    parse_deepseek_watch_block,
    phase_title_ru,
)


class EntryWatchPlanTests(unittest.TestCase):
    def test_default_triggers_on_peak_reversing_falling(self) -> None:
        plan = default_watch_plan()
        metrics = {
            "funding_trajectory_state": "peak_reversing",
            "oi_trend": "falling",
            "price_vs_impulse_high_pct": 5.0,
        }
        ev = evaluate_watch_plan(plan, metrics)
        self.assertTrue(ev.triggered)
        self.assertFalse(ev.invalidated)

    def test_default_plan_has_no_hard_invalidation(self) -> None:
        plan = default_watch_plan()
        self.assertEqual(plan["invalidate_if"], [])

    def test_extending_not_triggered(self) -> None:
        plan = default_watch_plan()
        metrics = {
            "funding_trajectory_state": "extending",
            "oi_trend": "falling",
            "price_vs_impulse_high_pct": 2.0,
        }
        ev = evaluate_watch_plan(plan, metrics)
        self.assertFalse(ev.triggered)

    def test_normalize_drops_unknown_metrics(self) -> None:
        raw = {
            "ttl_hours": 12,
            "all_of": [
                {"metric": "funding_trajectory_state", "op": "eq", "value": "peak_reversing"},
                {"metric": "made_up_metric", "op": "eq", "value": 1},
            ],
            "any_of": [],
            "invalidate_if": [],
        }
        plan = normalize_watch_plan(raw)
        self.assertEqual(plan["ttl_hours"], 72)
        self.assertEqual(len(plan["all_of"]), 1)

    def test_parse_deepseek_json_fence(self) -> None:
        raw = (
            "Сетап похож на продолжение, рано шортить.\n\n"
            "```json\n"
            '{"entry_timing":"early","watch_if_early":true,'
            '"reason_short":"extending",'
            '"watch_plan":{"ttl_hours":18,"all_of":['
            '{"metric":"funding_trajectory_state","op":"eq","value":"peak_reversing"}],'
            '"any_of":[],"invalidate_if":['
            '{"metric":"price_vs_impulse_high_pct","op":"gte","value":22}]}}\n'
            "```"
        )
        text, parsed = parse_deepseek_watch_block(raw)
        self.assertIn("рано", text.lower())
        self.assertNotIn("```", text)
        assert parsed is not None
        self.assertEqual(parsed.entry_timing, "early")
        self.assertTrue(parsed.watch_if_early)
        self.assertEqual(parsed.watch_plan["ttl_hours"], 72)
        self.assertEqual(parsed.watch_plan["invalidate_if"], [])

    def test_classify_deep_squeeze_phase(self) -> None:
        metrics = {
            "funding_trajectory_state": "extending",
            "funding_now": -1400.0,
            "oi_trend": "rising",
            "dist_atr_nearest": 0.8,
        }
        self.assertEqual(classify_squeeze_phase(metrics), "at_resistance")

    def test_classify_entry_ready_phase(self) -> None:
        metrics = {
            "funding_trajectory_state": "peak_reversing",
            "funding_now": -700.0,
            "oi_trend": "falling",
            "dist_atr_nearest": 0.7,
        }
        self.assertEqual(classify_squeeze_phase(metrics), "entry_ready")

    def test_phase_transition_text(self) -> None:
        text = format_phase_transition_ru(
            prev_phase="squeeze_building",
            new_phase="squeeze_deep",
            metrics={
                "funding_now": -1200.0,
                "funding_trajectory_state": "extending",
                "oi_trend": "rising",
                "price_vs_impulse_high_pct": 12.5,
            },
        )
        self.assertIn("Смена фазы", text)
        self.assertIn("сквиз разгоняется", text)
        self.assertIn("глубокий сквиз", text)
        self.assertIn("💡", text)
        self.assertIn("фандинг", text)

    def test_phase_title_ru(self) -> None:
        self.assertEqual(phase_title_ru("at_resistance"), "У сильного уровня")

    def test_format_metrics_ru_trader_snapshot(self) -> None:
        snap = format_metrics_ru(
            {
                "funding_now": -850.0,
                "funding_trajectory_state": "extending",
                "oi_trend": "rising",
                "dist_atr_ema200": 1.2,
                "price_vs_impulse_high_pct": 8.0,
            }
        )
        self.assertIn("фандинг -850% год.", snap)
        self.assertIn("над EMA200", snap)
        self.assertIn("от импульса", snap)


if __name__ == "__main__":
    unittest.main()
