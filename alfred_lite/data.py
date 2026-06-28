"""Market data: OHLCV bars (and latest prices) for the watchlist.

yfinance for historical / last-close (weekend-friendly, no account needed).
Alpaca's data API can be swapped in for live intraday later behind the same shape.
"""
from __future__ import annotations

import datetime as dt
import logging

import yfinance as yf

log = logging.getLogger(__name__)

_OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def fetch_bars(tickers: list[str], lookback_days: int) -> dict:
    """Return {ticker: DataFrame[OHLCV]} of daily bars covering the lookback window.

    Tickers that fail or return no data are skipped (logged), never raised — one
    bad symbol shouldn't take down a whole run.
    """
    end = dt.date.today() + dt.timedelta(days=1)            # yfinance end is exclusive
    start = dt.date.today() - dt.timedelta(days=int(lookback_days * 1.6) + 10)

    out: dict = {}
    for ticker in tickers:
        try:
            df = yf.Ticker(ticker).history(
                start=start, end=end, interval="1d", auto_adjust=True
            )
        except Exception as exc:                            # noqa: BLE001 - log & skip
            log.warning("fetch failed for %s: %s", ticker, exc)
            continue
        if df is None or df.empty:
            log.warning("no data for %s", ticker)
            continue
        cols = [c for c in _OHLCV if c in df.columns]
        out[ticker] = df[cols].dropna()
    return out


def latest_prices(tickers: list[str]) -> dict[str, float]:
    """Return {ticker: most-recent close} for the given tickers (empty in → empty out)."""
    if not tickers:
        return {}
    bars = fetch_bars(list(tickers), lookback_days=7)
    return {t: float(df["Close"].iloc[-1]) for t, df in bars.items() if not df.empty}
