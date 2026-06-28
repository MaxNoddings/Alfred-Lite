"""Append-only trade log: the living competition record + Claude's reasoning.

Each run appends rows to trades.csv (committed back to the repo on the stateless
runner). Also exposes recent_trades() to feed the brain its own memory.

TODO Phase 4.
"""
from __future__ import annotations


def append(rows: list[dict]) -> None:
    """Append executed trades + reasoning to trades.csv."""
    raise NotImplementedError("append — Phase 4")


def recent_trades(n: int) -> list[dict]:
    """Return the last n logged trades — the brain's memory feed."""
    raise NotImplementedError("recent_trades — Phase 4")
