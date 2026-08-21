"""Формирование пула монет для Pump&Dump сканера."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, fields

from app.bybit.linear_pool import build_bybit_turnover_candidates
from app.bybit.linear_symbols import resolve_linear_symbol
from app.market.coingecko import (
    CoinDetailRow,
    fetch_top_gainers_1h,
    fetch_trending_coins,
)
from app.pump_scan.params import PumpScanParams

log = logging.getLogger(__name__)


@dataclass
class PoolCoin:
    symbol: str
    name: str
    source: str
    market_cap_usd: float | None = None
    volume_24h_usd: float | None = None
    turnover_24h_usd: float | None = None
    turnover_rank: int | None = None
    outside_top200: bool = False
    is_innovation: bool = False
    is_st: bool = False
    symbol_type: str = ""
    extreme_risk: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> PoolCoin:
        known = {f.name for f in fields(cls)}
        kw = {k: v for k, v in raw.items() if k in known}
        if "symbol" in kw:
            kw["symbol"] = str(kw["symbol"]).upper()
        return cls(**kw)

    def badge_tags(self) -> list[str]:
        tags: list[str] = []
        if self.outside_top200:
            tags.append("вне топ-200")
        if self.is_st:
            tags.append("ST")
        if self.is_innovation:
            tags.append("Innovation")
        elif self.symbol_type and self.symbol_type not in ("", "innovation"):
            tags.append(self.symbol_type)
        if self.extreme_risk:
            tags.append("⚠️")
        if self.source in ("trending", "gainer"):
            tags.append(self.source)
        return tags


def _merge_coin(store: dict[str, PoolCoin], coin: PoolCoin) -> None:
    key = coin.symbol.upper()
    prev = store.get(key)
    if prev is None:
        store[key] = coin
        return
    if prev.source == "bybit" and coin.source in ("trending", "gainer"):
        prev.extreme_risk = prev.extreme_risk or coin.extreme_risk
        return
    store[key] = coin


def _coin_from_bybit(
    inst,
    ticker,
    rank: int,
    *,
    params: PumpScanParams,
) -> PoolCoin:
    return PoolCoin(
        symbol=inst.symbol,
        name=inst.base_coin,
        source="bybit",
        turnover_24h_usd=ticker.turnover_24h_usd,
        volume_24h_usd=ticker.turnover_24h_usd,
        turnover_rank=rank,
        outside_top200=rank > params.top_turnover_rank,
        is_innovation=inst.is_innovation,
        is_st=inst.is_st,
        symbol_type=inst.symbol_type,
    )


def _coin_from_coingecko(
    inst_symbol: str,
    row: CoinDetailRow,
    source: str,
    *,
    params: PumpScanParams,
    inst=None,
) -> PoolCoin | None:
    extreme = False
    if row.market_cap_usd is not None and row.market_cap_usd < params.min_market_cap_usd:
        extreme = True
    if row.volume_24h_usd is not None and row.volume_24h_usd < params.min_volume_24h_usd:
        extreme = True
    if extreme and not params.allow_extreme_risk:
        return None
    return PoolCoin(
        symbol=inst_symbol,
        name=row.name,
        source=source,
        market_cap_usd=row.market_cap_usd,
        volume_24h_usd=row.volume_24h_usd,
        outside_top200=True,
        is_innovation=inst.is_innovation if inst else False,
        is_st=inst.is_st if inst else False,
        symbol_type=inst.symbol_type if inst else "",
        extreme_risk=extreme,
    )


def build_universe(params: PumpScanParams) -> list[PoolCoin]:
    from app.bybit.linear_symbols import fetch_linear_usdt_instruments

    instruments = fetch_linear_usdt_instruments()
    store: dict[str, PoolCoin] = {}

    bybit_rows = build_bybit_turnover_candidates(
        min_turnover_usd=params.min_bybit_turnover_usd,
        instruments=instruments,
    )
    for inst, ticker, rank in bybit_rows:
        _merge_coin(
            store,
            _coin_from_bybit(inst, ticker, rank, params=params),
        )

    if params.include_trending:
        for row in fetch_trending_coins():
            inst = resolve_linear_symbol(row.symbol, instruments)
            if inst is None:
                continue
            coin = _coin_from_coingecko(
                inst.symbol, row, "trending", params=params, inst=inst
            )
            if coin:
                _merge_coin(store, coin)

    if params.include_gainers:
        for row in fetch_top_gainers_1h(limit=50):
            inst = resolve_linear_symbol(row.symbol, instruments)
            if inst is None:
                continue
            coin = _coin_from_coingecko(
                inst.symbol, row, "gainer", params=params, inst=inst
            )
            if coin:
                _merge_coin(store, coin)

    coins = sorted(
        store.values(),
        key=lambda c: (
            c.turnover_rank is None,
            c.turnover_rank or 999_999,
            c.extreme_risk,
            c.symbol,
        ),
    )
    if len(coins) > params.max_pool_size:
        coins = coins[: params.max_pool_size]

    log.info(
        "Pump universe: %s монет (bybit=%s, вне топ-%s=%s, innovation=%s, ST=%s)",
        len(coins),
        sum(1 for c in coins if c.source == "bybit"),
        params.top_turnover_rank,
        sum(1 for c in coins if c.outside_top200),
        sum(1 for c in coins if c.is_innovation),
        sum(1 for c in coins if c.is_st),
    )
    return coins
