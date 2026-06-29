"""Dynamic trading universe: a stable core + the day's biggest movers + held names.

The fixed CORE (config.WATCHLIST) anchors Alfred — market ETFs, a few megacaps, SPCX.
Alpaca's screener adds today's biggest gainers/losers (most-actives as backfill) so
Alfred acts on what is actually moving, not a static list. Held positions are always
included so we never lose sight of something we own.

Degrades safely: if the screener is unavailable (sim mode, no keys, network, or the
data plan lacks it) it falls back to core + held — a run never dies here.
"""
from __future__ import annotations

import logging

from . import config

log = logging.getLogger(__name__)


def _screener_movers() -> list[str]:
    """Top gainers + losers (most-actives as backfill) from Alpaca, price-filtered."""
    from alpaca.data.enums import MostActivesBy
    from alpaca.data.historical.screener import ScreenerClient
    from alpaca.data.requests import MarketMoversRequest, MostActivesRequest

    client = ScreenerClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
    top = max(config.MAX_MOVERS, 20)
    picks: list[str] = []
    seen: set[str] = set()

    def _add(symbol: str, price) -> None:
        s = (symbol or "").upper()
        if s and s not in seen and (price is None or price >= config.MOVER_MIN_PRICE):
            seen.add(s)
            picks.append(s)

    # Interleave gainers and losers so we surface both long and short candidates.
    movers = client.get_market_movers(MarketMoversRequest(top=top))
    gainers, losers = list(movers.gainers), list(movers.losers)
    for i in range(max(len(gainers), len(losers))):
        if i < len(gainers):
            _add(gainers[i].symbol, gainers[i].price)
        if i < len(losers):
            _add(losers[i].symbol, losers[i].price)
        if len(picks) >= config.MAX_MOVERS:
            break

    # Backfill with most-actives only if movers came up short after filtering.
    if len(picks) < config.MAX_MOVERS:
        actives = client.get_most_actives(
            MostActivesRequest(by=MostActivesBy.VOLUME, top=top)
        )
        for a in actives.most_actives:
            _add(a.symbol, None)               # actives have no price field; trust liquidity
            if len(picks) >= config.MAX_MOVERS:
                break

    return picks[: config.MAX_MOVERS]


def build(held_tickers: list[str] | None = None) -> list[str]:
    """Return the run's universe: core + movers + held, deduped (core first, held last)."""
    universe: list[str] = []
    seen: set[str] = set()

    def _extend(names) -> None:
        for n in names:
            u = (n or "").upper()
            if u and u not in seen:
                seen.add(u)
                universe.append(u)

    _extend(config.WATCHLIST)                  # core anchor — always present

    if config.USE_DYNAMIC_UNIVERSE and config.BROKER == "alpaca" and config.ALPACA_API_KEY:
        try:
            movers = _screener_movers()
            log.info("universe: +%d movers from screener", len(movers))
            _extend(movers)
        except Exception as exc:               # noqa: BLE001 - never let the screener kill a run
            log.warning("screener unavailable — core + held only: %s", exc)

    _extend(held_tickers or [])                # always keep what we own
    log.info("universe: %d names", len(universe))
    return universe
