"""Топ монет по капитализации (CoinGecko, публичный API)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Не считаем «альтами» для скана funding.
_EXCLUDED_SYMBOLS = frozenset(
    {
        "BTC",
        "ETH",
        "USDT",
        "USDC",
        "USDE",
        "DAI",
        "FDUSD",
        "TUSD",
        "USDD",
        "PYUSD",
        "USDS",
        "USD0",
        "FRAX",
        "LUSD",
        "CRVUSD",
        "GUSD",
        "USDP",
        "SUSDE",
        "USDS",
        "USDT0",
    }
)

_EXCLUDED_IDS = frozenset(
    {
        "bitcoin",
        "ethereum",
        "tether",
        "usd-coin",
        "dai",
        "first-digital-usd",
        "ethena-usde",
        "usdd",
        "paypal-usd",
    }
)


@dataclass(frozen=True)
class CoinMarketRow:
    coingecko_id: str
    symbol: str
    name: str
    market_cap_rank: int


@dataclass(frozen=True)
class CoinDetailRow:
    coingecko_id: str
    symbol: str
    name: str
    market_cap_usd: float | None
    volume_24h_usd: float | None
    price_change_pct_1h: float | None
    price_change_pct_24h: float | None
    market_cap_rank: int | None
    age_days: int | None = None


def _fetch_json(url: str, *, timeout: int = 45) -> object:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "traiding-bot-ema/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"CoinGecko HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"CoinGecko недоступен: {e.reason}") from e


def _row_from_markets_payload(row: dict) -> CoinDetailRow | None:
    sym = str(row.get("symbol", "")).upper()
    cid = str(row.get("id", ""))
    if not sym or not cid:
        return None
    rank_raw = row.get("market_cap_rank")
    rank = int(rank_raw) if rank_raw is not None else None
    mcap = row.get("market_cap")
    vol = row.get("total_volume")
    pc1 = row.get("price_change_percentage_1h_in_currency")
    pc24 = row.get("price_change_percentage_24h")
    return CoinDetailRow(
        coingecko_id=cid,
        symbol=sym,
        name=str(row.get("name") or sym),
        market_cap_usd=float(mcap) if mcap is not None else None,
        volume_24h_usd=float(vol) if vol is not None else None,
        price_change_pct_1h=float(pc1) if pc1 is not None else None,
        price_change_pct_24h=float(pc24) if pc24 is not None else None,
        market_cap_rank=rank,
    )


def fetch_markets_rich(
    *,
    per_page: int = 250,
    page: int = 1,
    order: str = "market_cap_desc",
) -> list[CoinDetailRow]:
    url = (
        "https://api.coingecko.com/api/v3/coins/markets"
        f"?vs_currency=usd&order={order}&per_page={per_page}&page={page}"
        "&sparkline=false&price_change_percentage=1h,24h"
    )
    payload = _fetch_json(url)
    if not isinstance(payload, list):
        raise RuntimeError("CoinGecko markets: неожиданный ответ")
    out: list[CoinDetailRow] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        parsed = _row_from_markets_payload(row)
        if parsed is not None:
            out.append(parsed)
    return out


def fetch_trending_coins() -> list[CoinDetailRow]:
    payload = _fetch_json("https://api.coingecko.com/api/v3/search/trending")
    if not isinstance(payload, dict):
        raise RuntimeError("CoinGecko trending: неожиданный ответ")
    coins = payload.get("coins") or []
    out: list[CoinDetailRow] = []
    for item in coins:
        if not isinstance(item, dict):
            continue
        coin = item.get("item") or item
        if not isinstance(coin, dict):
            continue
        sym = str(coin.get("symbol", "")).upper()
        cid = str(coin.get("id", ""))
        if not sym or not cid:
            continue
        rank_raw = coin.get("market_cap_rank")
        rank = int(rank_raw) if rank_raw is not None else None
        out.append(
            CoinDetailRow(
                coingecko_id=cid,
                symbol=sym,
                name=str(coin.get("name") or sym),
                market_cap_usd=None,
                volume_24h_usd=None,
                price_change_pct_1h=None,
                price_change_pct_24h=None,
                market_cap_rank=rank,
            )
        )
    log.info("CoinGecko trending: %s монет", len(out))
    return out


def fetch_top_gainers_1h(*, limit: int = 50) -> list[CoinDetailRow]:
    rows = fetch_markets_rich(
        per_page=min(250, max(limit * 2, 50)),
        page=1,
        order="price_change_percentage_1h_desc",
    )
    out: list[CoinDetailRow] = []
    for row in rows:
        if row.symbol in _EXCLUDED_SYMBOLS or row.coingecko_id in _EXCLUDED_IDS:
            continue
        out.append(row)
        if len(out) >= limit:
            break
    log.info("CoinGecko gainers 1h: %s монет", len(out))
    return out


def fetch_top_altcoins(*, limit: int = 100) -> list[CoinMarketRow]:
    """
    Топ монет по cap с CoinGecko, без BTC/ETH и стейблов.
    Берём с запасом (до 250), пока не наберём limit альтов.
    """
    per_page = min(250, max(limit * 2, 100))
    payload = _fetch_json(
        "https://api.coingecko.com/api/v3/coins/markets"
        f"?vs_currency=usd&order=market_cap_desc&per_page={per_page}&page=1"
    )
    if not isinstance(payload, list):
        raise RuntimeError("CoinGecko: неожиданный ответ")

    out: list[CoinMarketRow] = []
    for row in payload:
        sym = str(row.get("symbol", "")).upper()
        cid = str(row.get("id", ""))
        rank = int(row.get("market_cap_rank") or 0)
        if not sym or rank <= 0:
            continue
        if sym in _EXCLUDED_SYMBOLS or cid in _EXCLUDED_IDS:
            continue
        out.append(
            CoinMarketRow(
                coingecko_id=cid,
                symbol=sym,
                name=str(row.get("name") or sym),
                market_cap_rank=rank,
            )
        )
        if len(out) >= limit:
            break

    log.info("CoinGecko: отобрано %s альтов (запрошено %s)", len(out), limit)
    return out
