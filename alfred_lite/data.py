"""Market data: OHLCV bars (and latest prices) for the watchlist.

yfinance for historical daily bars (weekend-friendly, no account needed); Alpaca's
real-time last trade layered on top when keys are available. yfinance's "today" can
lag violently on gapping/halted movers — exactly the names the dynamic universe
surfaces — so decisions and logs must never trust it for the current price.
"""
from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import yfinance as yf

from . import config

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


def alpaca_live_prices(tickers: list[str]) -> dict[str, float]:
    """Real-time last-trade price per ticker from Alpaca's data API (one batch call).

    Returns {} when keys are missing or the call fails — callers fall back to
    yfinance closes, which is exactly the old behavior.
    """
    if not tickers or not (config.ALPACA_API_KEY and config.ALPACA_SECRET_KEY):
        return {}
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest

        client = StockHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
        trades = client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=list(tickers))
        )
        return {sym: float(t.price) for sym, t in trades.items() if t.price}
    except Exception as exc:                            # noqa: BLE001 - degrade, don't die
        log.warning("alpaca live prices unavailable (%s) — falling back to yfinance", exc)
        return {}


def overlay_live_prices(bars: dict) -> dict:
    """Stamp Alpaca's real-time price onto each ticker's LAST daily bar.

    If yfinance already has a partial bar for today, its Close is overwritten with the
    live trade price; if its last bar is stale (yesterday or older), a synthetic bar
    for today is appended (Volume NaN → the volume signal simply reads unavailable).
    Signals, the penny floor, the brain, and the log then all see the true price.
    """
    live = alpaca_live_prices(list(bars))
    if not live:
        return bars
    for ticker, df in bars.items():
        price = live.get(ticker)
        if price is None or df is None or df.empty:
            continue
        tz = df.index.tz
        today = dt.datetime.now(tz).date() if tz is not None else dt.date.today()
        if df.index[-1].date() >= today:                 # partial bar exists — correct it
            df.iloc[-1, df.columns.get_loc("Close")] = price
        else:                                            # stale — append today's bar
            row = {c: price for c in df.columns if c != "Volume"}
            if "Volume" in df.columns:
                row["Volume"] = np.nan
            ts = dt.datetime.combine(today, dt.time(0, 0), tzinfo=tz) if tz else today
            df.loc[ts] = row
    return bars


def latest_prices(tickers: list[str]) -> dict[str, float]:
    """Return {ticker: current price} — Alpaca real-time first, yfinance close fallback."""
    if not tickers:
        return {}
    prices = alpaca_live_prices(list(tickers))
    missing = [t for t in tickers if t not in prices]
    if missing:
        bars = fetch_bars(missing, lookback_days=7)
        for t, df in bars.items():
            if not df.empty:
                prices[t] = float(df["Close"].iloc[-1])
    return prices
