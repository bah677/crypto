from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class SlAnomCloseParams:
    # 1m свечи по Bybit
    interval: str = "1"
    lookback_bars: int = 30
    body_multiplier: float = 3.0
    wick_max_ratio: float = 0.10
    next_small_divisor: float = 5.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "SlAnomCloseParams":
        if not raw:
            return cls()
        # мягкая валидация: только известные поля
        allowed = set(asdict(cls()).keys())
        kw = {k: v for k, v in raw.items() if k in allowed}
        return cls(**kw)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

