"""OHLC chart for XAU M1 — стиль как в crypto_adviser pump charts."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from app.market.candles import Candle

MSK = ZoneInfo("Europe/Moscow")

_THEME = {
    "fig": "#1a1a2e",
    "ax": "#16213e",
    "grid": "#888888",
    "spine": "#444444",
    "tick": "#b0b0b0",
    "title": "#e8e8e8",
    "up": "#26a69a",
    "down": "#ef5350",
    "avg": "#ffb74d",
    "last": "#ffeb3b",
}


def _candle_width(dates: list[datetime], *, fraction: float = 0.72) -> float:
    if len(dates) < 2:
        return (1 / (24 * 60)) * fraction
    nums = [mdates.date2num(d) for d in dates]
    spacings = [nums[i + 1] - nums[i] for i in range(len(nums) - 1) if nums[i + 1] > nums[i]]
    if not spacings:
        return (1 / (24 * 60)) * fraction
    spacings.sort()
    step = spacings[len(spacings) // 2]
    return max(step * fraction, step * 0.3)


def render_m1_candles_png(
    candles: list[Candle],
    *,
    title: str = "XAU/USD · M1",
    highlight_last: bool = True,
    avg_body: float | None = None,
) -> bytes:
    if len(candles) < 2:
        raise ValueError("нужно минимум 2 свечи")

    dates: list[datetime] = []
    for c in candles:
        dt = c.open_time
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dates.append(dt.astimezone(MSK))

    width = _candle_width(dates)
    fig, ax = plt.subplots(figsize=(11, 5.2), dpi=140)
    fig.patch.set_facecolor(_THEME["fig"])
    ax.set_facecolor(_THEME["ax"])

    for i, c in enumerate(candles):
        x = mdates.date2num(dates[i])
        up = c.close >= c.open
        color = _THEME["up"] if up else _THEME["down"]
        if highlight_last and i == len(candles) - 1:
            color = _THEME["last"]
        ax.plot([x, x], [c.low, c.high], color=color, linewidth=0.9, solid_capstyle="round")
        body_bottom = min(c.open, c.close)
        body_h = max(abs(c.close - c.open), (c.high - c.low) * 0.02 if c.high != c.low else c.open * 0.0002)
        ax.add_patch(
            Rectangle(
                (x - width / 2, body_bottom),
                width,
                body_h,
                facecolor=color,
                edgecolor=color,
            )
        )

    # среднее тело вокруг close последней (визуальный ориентир размера)
    if avg_body is not None and avg_body > 0:
        last = candles[-1]
        mid = (last.open + last.close) / 2.0
        ax.axhline(mid + avg_body / 2, color=_THEME["avg"], linestyle="--", linewidth=0.9, alpha=0.7, label=f"avg body {avg_body:.2f}")
        ax.axhline(mid - avg_body / 2, color=_THEME["avg"], linestyle="--", linewidth=0.9, alpha=0.7)

    last = candles[-1]
    ax.set_title(
        f"{title} · {len(candles)} св · last {last.open_time_key}",
        color=_THEME["title"],
        fontsize=12,
        pad=10,
    )
    ax.tick_params(colors=_THEME["tick"], labelsize=8)
    ax.grid(True, alpha=0.15, color=_THEME["grid"])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=MSK))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=8))
    for spine in ax.spines.values():
        spine.set_color(_THEME["spine"])
    if avg_body is not None and avg_body > 0:
        ax.legend(loc="upper left", fontsize=8, facecolor=_THEME["fig"], edgecolor=_THEME["spine"], labelcolor=_THEME["tick"])

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()
