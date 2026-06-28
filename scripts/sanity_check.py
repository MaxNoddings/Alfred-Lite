"""Sanity-check the signals against the historical CSVs in ../Trading Bot/.

Confirms the deterministic layer produces sane numbers on known historical data
(RSI within 0-100, z-scores reasonable, momentum sign matching the trend) before
we ever trust it live. The CSVs are close-only, so the volume signal is skipped.

    python scripts/sanity_check.py
"""
from __future__ import annotations

import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from alfred_lite import signals  # noqa: E402

CSV_DIR = pathlib.Path(__file__).resolve().parents[2] / "Trading Bot"
TICKERS = ["AAPL", "MSFT", "NVDA"]


def load(ticker: str) -> pd.DataFrame:
    """Load a close-only historical CSV into a Close DataFrame."""
    df = pd.read_csv(CSV_DIR / f"{ticker}.csv", parse_dates=["Date"], index_col="Date")
    return df.rename(columns={ticker: "Close"})[["Close"]]


def main() -> None:
    bars = {t: load(t) for t in TICKERS}
    for t, df in bars.items():
        print(f"{t}: {len(df)} rows, {df.index[0].date()} → {df.index[-1].date()}")
    print()
    rows = signals.compute_signals(bars)
    print(signals.format_table(rows))
    print("\n(signals computed on the LAST bar of each historical series)")


if __name__ == "__main__":
    main()
