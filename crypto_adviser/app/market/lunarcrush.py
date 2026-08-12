"""LunarCrush API v4 — социальные метрики по монете."""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from app.config import get_settings

log = logging.getLogger(__name__)

_API_BASE = "https://lunarcrush.com/api4"
_NUM_PREFIX = re.compile(r"^(\d+)([A-Z0-9]+)$")

# topic -> (monotonic_ts, snapshot)
_baseline_cache: dict[str, tuple[float, float]] = {}
_snapshot_cache: dict[str, tuple[float, SocialSnapshot]] = {}
_CACHE_TTL_S = 3600.0


@dataclass(frozen=True)
class SocialSnapshot:
    topic: str
    galaxy_score: float | None
    sentiment: float | None
    interactions: float | None
    social_dominance: float | None
    contributors: int | None
    spike_ratio: float | None = None

    def alert_line(self) -> str | None:
        if self.galaxy_score is None and self.interactions is None:
            return None
        parts: list[str] = []
        if self.galaxy_score is not None:
            parts.append(f"Galaxy <b>{self.galaxy_score:.0f}</b>")
        if self.sentiment is not None:
            parts.append(f"sentiment <b>{self.sentiment:.0f}</b>")
        if self.spike_ratio is not None and self.spike_ratio >= 1.5:
            parts.append(f"social ×<b>{self.spike_ratio:.1f}</b>")
        if not parts:
            return None
        return "📣 LunarCrush: " + " · ".join(parts)


def symbol_to_topic(symbol: str) -> str:
    """ROAMUSDT → roam, 1000PEPEUSDT → pepe."""
    base = symbol.upper().removesuffix("USDT").removesuffix("PERP")
    m = _NUM_PREFIX.match(base)
    if m:
        base = m.group(2)
    return base.lower()


def _fetch_json(path: str) -> dict:
    s = get_settings()
    key = (s.lunarcrush_api_key or "").strip()
    if not key:
        raise RuntimeError("LUNARCRUSH_API_KEY не задан")
    url = f"{_API_BASE}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": "traiding-bot-ema/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        raise RuntimeError(f"LunarCrush HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"LunarCrush недоступен: {e.reason}") from e
    if not isinstance(payload, dict):
        raise RuntimeError("LunarCrush: неожиданный ответ")
    if payload.get("error"):
        raise RuntimeError(str(payload.get("error")))
    return payload


def _num(raw: object) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _int(raw: object) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def fetch_topic_snapshot(topic: str) -> SocialSnapshot | None:
    """Текущие соц-метрики по тикеру (topic = roam, btc, …)."""
    topic = topic.lower().strip()
    if not topic:
        return None

    now = time.monotonic()
    cached = _snapshot_cache.get(topic)
    if cached and (now - cached[0]) < _CACHE_TTL_S:
        return cached[1]

    try:
        data = _fetch_json(f"/public/topic/{topic}/v1")
    except RuntimeError as e:
        log.debug("LunarCrush %s: %s", topic, e)
        return None

    row = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(row, dict):
        return None

    interactions = _num(
        row.get("interactions")
        or row.get("interactions_24h")
        or row.get("social_volume")
        or row.get("num_posts")
    )
    spike: float | None = None
    if interactions is not None and interactions > 0:
        prev = _baseline_cache.get(topic)
        if prev and prev[1] > 0:
            spike = interactions / prev[1]
        _baseline_cache[topic] = (now, interactions)

    snap = SocialSnapshot(
        topic=topic,
        galaxy_score=_num(row.get("galaxy_score")),
        sentiment=_num(row.get("sentiment") or row.get("average_sentiment")),
        interactions=interactions,
        social_dominance=_num(row.get("social_dominance")),
        contributors=_int(row.get("num_contributors")),
        spike_ratio=spike,
    )
    _snapshot_cache[topic] = (now, snap)
    return snap


def fetch_symbol_social(symbol: str) -> SocialSnapshot | None:
    s = get_settings()
    if not s.lunarcrush_ready:
        return None
    return fetch_topic_snapshot(symbol_to_topic(symbol))
