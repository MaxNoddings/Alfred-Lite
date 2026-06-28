"""Append-only trade log: the living competition record + Claude's reasoning.

append() writes each executed trade to the CSV; recent_trades() reads them back as
the brain's memory feed. The live (alpaca) log is committed back from the runner;
the sim log stays local (config.TRADES_CSV switches on the broker).
"""
from __future__ import annotations

import csv
import pathlib

from . import config

_FIELDS = [
    "timestamp", "ticker", "action", "side", "notional", "price", "qty",
    "confidence", "reasoning", "market_note", "equity", "cash",
]


def append(rows: list[dict]) -> None:
    """Append executed-trade rows to the trades CSV (writing a header if new)."""
    if not rows:
        return
    path = pathlib.Path(config.TRADES_CSV)
    is_new = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def recent_trades(n: int) -> list[dict]:
    """Return the last n logged trades — the brain's memory feed."""
    path = pathlib.Path(config.TRADES_CSV)
    if not path.exists():
        return []
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-n:]
