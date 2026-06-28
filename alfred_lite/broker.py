"""Broker abstraction: one interface, two implementations.

    SimBroker    — local, offline dev/test harness (state in state.json).
    AlpacaBroker — live paper trading via alpaca-py (state held server-side).

Alfred's trading logic depends only on the `Broker` protocol, so we build and
test on SimBroker this weekend and flip to AlpacaBroker with one env var.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from . import config, data

log = logging.getLogger(__name__)


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


# ── position math ─────────────────────────────────────────────────────────────
def _apply_fill(qty: float, avg: float, delta: float, price: float) -> tuple[float, float]:
    """Return (new_qty, new_avg_price) after filling `delta` shares at `price`.

    Handles opening, adding, reducing, closing, and flipping through zero. `delta`
    is signed: positive = buy, negative = sell/short.
    """
    new_qty = qty + delta
    same_direction = qty == 0 or (qty > 0) == (delta > 0)

    if same_direction:                                   # opening or adding
        new_avg = 0.0 if new_qty == 0 else (
            (avg * abs(qty) + price * abs(delta)) / abs(new_qty)
        )
    elif abs(delta) <= abs(qty):                         # reducing / closing
        new_avg = avg if new_qty != 0 else 0.0
    else:                                                # flipping through zero
        new_avg = price
    return new_qty, new_avg


# ── SimBroker ─────────────────────────────────────────────────────────────────
class SimBroker:
    """Offline simulator — the weekend workbench.

    Idealized fills at the latest close, state persisted to a JSON file. Alpaca is
    the real accounting engine; this exists to exercise the loop with instant,
    deterministic feedback (no market, no account needed).
    """

    def __init__(
        self,
        state_path: str = "state.json",
        starting_cash: float = config.STARTING_CASH,
        fresh: bool = False,
    ) -> None:
        self.state_path = Path(state_path)
        if fresh or not self.state_path.exists():
            self.cash = float(starting_cash)
            self.positions: dict[str, dict] = {}          # ticker -> {qty, avg_price}
            self._save()
        else:
            state = json.loads(self.state_path.read_text())
            self.cash = float(state["cash"])
            self.positions = state.get("positions", {})

    def _save(self) -> None:
        self.state_path.write_text(
            json.dumps({"cash": self.cash, "positions": self.positions}, indent=2)
        )

    def _price(self, ticker: str) -> float:
        price = data.latest_prices([ticker]).get(ticker)
        if price is None:
            raise RuntimeError(f"no price available for {ticker}")
        return price

    def snapshot(self) -> Portfolio:
        prices = data.latest_prices(list(self.positions))
        positions: dict[str, Position] = {}
        market_total = 0.0
        for ticker, p in self.positions.items():
            qty, avg = float(p["qty"]), float(p["avg_price"])
            price = prices.get(ticker, avg)               # fall back to cost if unpriced
            mv = qty * price
            market_total += mv
            positions[ticker] = Position(
                ticker=ticker,
                qty=round(qty, 4),
                avg_price=round(avg, 4),
                market_value=round(mv, 2),
                unrealized_pl=round(qty * (price - avg), 2),
            )
        equity = self.cash + market_total
        return Portfolio(
            cash=round(self.cash, 2),
            equity=round(equity, 2),
            buying_power=round(self.cash, 2),             # idealized: cash only
            positions=positions,
        )

    def submit(self, ticker: str, side: str, notional: float) -> None:
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        price = self._price(ticker)
        delta = (notional / price) if side == "buy" else -(notional / price)

        pos = self.positions.get(ticker, {"qty": 0.0, "avg_price": 0.0})
        new_qty, new_avg = _apply_fill(float(pos["qty"]), float(pos["avg_price"]), delta, price)
        self.cash -= delta * price                        # buy spends cash; sell/short credits it

        if abs(new_qty) < 1e-9:
            self.positions.pop(ticker, None)
        else:
            self.positions[ticker] = {"qty": new_qty, "avg_price": new_avg}
        self._save()
        log.info("SIM %s %s $%.2f @ %.2f -> qty %.4f", side, ticker, notional, price, new_qty)

    def is_market_open(self) -> bool:
        return True                                       # sim always "open" for testing

    def is_shortable(self, ticker: str) -> bool:
        return True                                       # sim assumes everything is shortable


# ── AlpacaBroker ──────────────────────────────────────────────────────────────
class AlpacaBroker:
    """Live paper trading via alpaca-py (lazy-imports alpaca). TODO Phase 5."""

    def __init__(self) -> None:
        raise NotImplementedError("AlpacaBroker — Phase 5")


# ── factory + formatting ──────────────────────────────────────────────────────
def make_broker(name: str | None = None) -> Broker:
    """Return the broker selected by config/env ('sim' | 'alpaca')."""
    name = (name or config.BROKER).lower()
    if name == "sim":
        return SimBroker()
    if name == "alpaca":
        return AlpacaBroker()
    raise ValueError(f"unknown broker: {name!r} (expected 'sim' or 'alpaca')")


def format_portfolio(p: Portfolio) -> str:
    """Pretty, aligned portfolio summary for previews and logs."""
    lines = [
        f"equity ${p.equity:,.2f}   cash ${p.cash:,.2f}   buying power ${p.buying_power:,.2f}",
    ]
    if not p.positions:
        lines.append("  (no open positions)")
        return "\n".join(lines)
    lines.append(f"  {'ticker':<7}{'qty':>10}{'avg':>10}{'mkt_val':>12}{'unrl_pl':>11}")
    for pos in p.positions.values():
        lines.append(
            f"  {pos.ticker:<7}{pos.qty:>10.4f}{pos.avg_price:>10.2f}"
            f"{pos.market_value:>12.2f}{pos.unrealized_pl:>11.2f}"
        )
    return "\n".join(lines)
