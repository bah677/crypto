"""EMA 7/14/28 на недельках для pump-алертов и мастера ордеров."""

from __future__ import annotations

from dataclasses import dataclass

from app.bybit.rest import BybitRest, _interval_to_ms
from app.indicators.ema import ema_series

WEEKLY_EMA_PERIODS = (7, 14, 28)


@dataclass(frozen=True)
class WeeklyEmaSnapshot:
    close: float
    ema7: float | None
    ema14: float | None
    ema28: float | None

    def as_label_map(self) -> dict[str, float | None]:
        return {
            "7W": self.ema7,
            "14W": self.ema14,
            "28W": self.ema28,
        }

    def format_lines(self, *, price: float) -> list[str]:
        if self.ema7 is None and self.ema14 is None and self.ema28 is None:
            return ["EMA 1W: <i>недостаточно истории</i>"]

        def _fmt(v: float | None) -> str:
            if v is None:
                return "—"
            return f"<code>{v:.5g}</code>"

        parts = [
            f"7={_fmt(self.ema7)}",
            f"14={_fmt(self.ema14)}",
            f"28={_fmt(self.ema28)}",
        ]
        lines = [f"EMA 1W: {' · '.join(parts)}"]

        rel: list[str] = []
        for label, v in (("7", self.ema7), ("14", self.ema14), ("28", self.ema28)):
            if v is None:
                continue
            rel.append(f"{'↑' if price > v else '↓'}EMA{label}")
        if rel:
            lines.append("Позиция (цена импульса): " + " · ".join(rel))
        return lines


def format_ema_entry_label(ema_key: str) -> str:
    """Ключ мастера → подпись для сообщений (50 → EMA50 1D, 7W → EMA7 1W)."""
    key = (ema_key or "").strip().upper()
    if key.endswith("W"):
        period = key[:-1]
        return f"EMA{period} 1W"
    return f"EMA{key} 1D"


def compute_weekly_emas(
    client: BybitRest,
    symbol: str,
    *,
    as_of_ms: int | None = None,
) -> WeeklyEmaSnapshot | None:
    import time

    raw = client.get_kline_ohlcv(symbol, "W", limit=120, end_ms=as_of_ms)
    if not raw:
        return None
    step = _interval_to_ms("W")
    now_ms = as_of_ms if as_of_ms is not None else int(time.time() * 1000)
    closed = [b for b in raw if b[0] + step <= now_ms]
    if not closed:
        return None
    closes = [b[4] for b in closed]
    close = closes[-1]
    e7 = ema_series(closes, 7)
    e14 = ema_series(closes, 14)
    e28 = ema_series(closes, 28)
    return WeeklyEmaSnapshot(
        close=close,
        ema7=e7[-1] if e7 else None,
        ema14=e14[-1] if e14 else None,
        ema28=e28[-1] if e28 else None,
    )
