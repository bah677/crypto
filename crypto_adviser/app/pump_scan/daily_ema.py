"""EMA 50/100/200 на дневках для pump-алертов."""

from __future__ import annotations

from dataclasses import dataclass

from app.bybit.rest import BybitRest, _interval_to_ms
from app.indicators.ema import ema_series


@dataclass(frozen=True)
class DailyEmaSnapshot:
    close: float
    ema50: float | None
    ema100: float | None
    ema200: float | None

    def format_lines(self, *, price: float) -> list[str]:
        """price — цена импульса в момент алерта (для ↑/↓ vs EMA 1D)."""
        if self.ema50 is None and self.ema100 is None and self.ema200 is None:
            return ["EMA 1D: <i>недостаточно истории</i>"]

        def _fmt(v: float | None) -> str:
            if v is None:
                return "—"
            return f"<code>{v:.5g}</code>"

        parts = [
            f"50={_fmt(self.ema50)}",
            f"100={_fmt(self.ema100)}",
            f"200={_fmt(self.ema200)}",
        ]
        lines = [f"EMA 1D: {' · '.join(parts)}"]

        rel: list[str] = []
        for label, v in (("50", self.ema50), ("100", self.ema100), ("200", self.ema200)):
            if v is None:
                continue
            rel.append(f"{'↑' if price > v else '↓'}EMA{label}")
        if rel:
            lines.append(f"Позиция (цена импульса): " + " · ".join(rel))
        return lines


def compute_daily_emas(
    client: BybitRest,
    symbol: str,
    *,
    as_of_ms: int | None = None,
) -> DailyEmaSnapshot | None:
    import time

    raw = client.get_kline_ohlcv(symbol, "D", limit=250, end_ms=as_of_ms)
    if not raw:
        return None
    step = _interval_to_ms("D")
    now_ms = as_of_ms if as_of_ms is not None else int(time.time() * 1000)
    closed = [b for b in raw if b[0] + step <= now_ms]
    if not closed:
        return None
    closes = [b[4] for b in closed]
    close = closes[-1]
    e50 = ema_series(closes, 50)
    e100 = ema_series(closes, 100)
    e200 = ema_series(closes, 200)
    return DailyEmaSnapshot(
        close=close,
        ema50=e50[-1] if e50 else None,
        ema100=e100[-1] if e100 else None,
        ema200=e200[-1] if e200 else None,
    )
