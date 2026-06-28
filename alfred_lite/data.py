"""Market data: OHLCV bars for the watchlist.

yfinance for historical / last-close (weekend-friendly, no account needed);
Alpaca's data API can be swapped in for live intraday later.

TODO Phase 1.
"""
from __future__ import annotations


def fetch_bars(tickers: list[str], lookback_days: int) -> dict:
    """Return {ticker: DataFrame[OHLCV]} for the lookback window."""
    raise NotImplementedError("fetch_bars — Phase 1")
