"""Broker abstraction: one interface, two implementations.

    SimBroker    — local, offline dev/test harness (state in state.json).
    AlpacaBroker — live paper trading via alpaca-py (state held server-side).

Alfred's trading logic depends only on the `Broker` protocol, so we build and
test on SimBroker this weekend and flip to AlpacaBroker with one env var.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Position:
    ticker: str
    qty: float            # shares held; negative = short
    avg_price: float
    market_value: float
    unrealized_pl: float


@dataclass
class Portfolio:
    cash: float
    equity: float
    buying_power: float
    positions: dict[str, Position] = field(default_factory=dict)


class Broker(Protocol):
    """The only surface Alfred's trading logic depends on."""

    def snapshot(self) -> Portfolio:
        """Current state: cash, equity, buying power, open positions (with P&L)."""

    def submit(self, ticker: str, side: str, notional: float) -> None:
        """Market order for `notional` dollars. side = 'buy' | 'sell'.

        Fractional shares via dollar amount. A 'sell' beyond a held position
        opens/extends a short — gated by `is_shortable` + available buying power.
        """

    def is_market_open(self) -> bool:
        """True if the US equity market is currently open."""

    def is_shortable(self, ticker: str) -> bool:
        """True if the asset may be *legally* shorted right now (borrowable + permitted)."""


class SimBroker:
    """Offline simulator — the weekend workbench. TODO Phase 2."""

    def __init__(self) -> None:
        raise NotImplementedError("SimBroker — Phase 2")


class AlpacaBroker:
    """Live paper trading via alpaca-py (lazy-imports alpaca). TODO Phase 5."""

    def __init__(self) -> None:
        raise NotImplementedError("AlpacaBroker — Phase 5")


def make_broker(name: str | None = None) -> Broker:
    """Return the broker selected by config/env ('sim' | 'alpaca'). TODO Phase 2."""
    raise NotImplementedError("make_broker — Phase 2")
