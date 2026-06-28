"""Phase 5 check: connect to the Alpaca paper account (READ-ONLY — no trades).

Validates the connection end to end — account balance, market clock, and shortable
checks — before we let Alfred trade live. Run after adding ALPACA_SECRET_KEY to .env.

    python scripts/alpaca_check.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from alfred_lite import broker as bk  # noqa: E402
from alfred_lite import config  # noqa: E402


def main() -> None:
    b = bk.AlpacaBroker()

    print(f"market open: {b.is_market_open()}")
    print("\n=== ACCOUNT ===")
    print(bk.format_portfolio(b.snapshot()))

    print("\n=== SHORTABLE CHECK (first 5 watchlist names) ===")
    for ticker in config.WATCHLIST[:5]:
        print(f"  {ticker:<6} shortable={b.is_shortable(ticker)}")


if __name__ == "__main__":
    main()
