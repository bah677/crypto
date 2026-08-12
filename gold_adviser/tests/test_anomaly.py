from __future__ import annotations

import unittest

from app.market.candles import Candle
from app.services.anomaly import analyze_body_anomaly
from datetime import datetime, timezone


def _c(i: int, o: float, c: float) -> Candle:
    return Candle(
        open_time=datetime(2026, 1, 1, 12, i, tzinfo=timezone.utc),
        open=o,
        high=max(o, c) + 0.1,
        low=min(o, c) - 0.1,
        close=c,
    )


class AnomalyTests(unittest.TestCase):
    def test_detects_large_body(self) -> None:
        candles = [_c(i, 2000.0, 2000.5) for i in range(10)]
        candles.append(_c(10, 2000.0, 2003.0))  # body 3.0 vs avg 0.5 → ×6
        r = analyze_body_anomaly(candles, body_mult=2.0)
        assert r is not None
        self.assertTrue(r.is_anomaly)
        self.assertGreaterEqual(r.ratio, 2.0)

    def test_no_anomaly_when_normal(self) -> None:
        candles = [_c(i, 2000.0, 2000.4) for i in range(10)]
        candles.append(_c(10, 2000.0, 2000.5))
        r = analyze_body_anomaly(candles, body_mult=2.0)
        assert r is not None
        self.assertFalse(r.is_anomaly)


if __name__ == "__main__":
    unittest.main()
