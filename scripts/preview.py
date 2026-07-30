"""Dev preview: fetch live bars for the watchlist and print the signal table.

    python scripts/preview.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from alfred_lite import config, data, signals  # noqa: E402


def main() -> None:
    print(f"fetching {len(config.WATCHLIST)} tickers ({config.LOOKBACK_DAYS}d lookback)...")
    bars = data.fetch_bars(config.WATCHLIST, config.LOOKBACK_DAYS)
    rows = signals.compute_signals(bars)
    print(f"got bars for {len(bars)} / {len(config.WATCHLIST)} tickers\n")
    regime = signals.market_regime(bars, rows)
    print(f"regime   : {regime['note']}")
    print(f"gross cap: {regime['gross_cap']:.0%} of equity\n")
    print(signals.format_table(rows))


if __name__ == "__main__":
    main()
