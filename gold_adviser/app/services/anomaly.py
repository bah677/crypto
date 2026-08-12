from __future__ import annotations

from dataclasses import dataclass

from app.market.candles import Candle


@dataclass(frozen=True)
class AnomalyResult:
    is_anomaly: bool
    last: Candle
    body: float
    avg_body: float
    ratio: float
    lookback_used: int
    body_mult: float


def analyze_body_anomaly(
    candles: list[Candle],
    *,
    body_mult: float,
) -> AnomalyResult | None:
    """
    Сравнивает тело последней (закрытой) свечи со средним телом предыдущих.
    candles — хронологически ascending, минимум 2 штуки.
    """
    if len(candles) < 2:
        return None
    last = candles[-1]
    others = candles[:-1]
    bodies = [c.body for c in others]
    avg = sum(bodies) / len(bodies) if bodies else 0.0
    body = last.body
    ratio = (body / avg) if avg > 1e-12 else 0.0
    return AnomalyResult(
        is_anomaly=avg > 1e-12 and body >= float(body_mult) * avg,
        last=last,
        body=body,
        avg_body=avg,
        ratio=ratio,
        lookback_used=len(candles),
        body_mult=float(body_mult),
    )
