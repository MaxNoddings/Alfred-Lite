"""Deterministic signal funnel: RSI, mean-reversion z-score, momentum, volume.

Pure functions over price history — fast, free, reproducible, and BACKTESTABLE.
Works on full OHLCV (live) or close-only frames (historical CSVs); the volume
signal is simply skipped when no Volume column is present.
"""
from __future__ import annotations

import numpy as np

from . import config


def _rsi(close, period: int):
    """Wilder's RSI over a close series."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _num(x):
    """Float or None — NaN/missing collapses to None so blanks read as 'unavailable'."""
    if x is None:
        return None
    x = float(x)
    return None if np.isnan(x) else x


def _flags(rsi, zscore, momentum_pct, vol_ratio) -> list[str]:
    """Notable conditions. Each check is skipped when its signal is unavailable (None)."""
    flags: list[str] = []
    if rsi is not None:
        if rsi < 30:
            flags.append("oversold")
        elif rsi > 70:
            flags.append("overbought")
    if zscore is not None:
        if zscore <= -2:
            flags.append("stretched_low")
        elif zscore >= 2:
            flags.append("stretched_high")
    if momentum_pct is not None:
        flags.append("momentum_up" if momentum_pct > 0 else "momentum_down")
    if vol_ratio is not None and vol_ratio > 1.5:
        flags.append("high_volume")
    return flags


def compute_signals(bars: dict) -> list[dict]:
    """One signal row per ticker.

    Thin-history names (recent IPOs like SPCX, or fresh movers) are kept, not dropped:
    each signal needing more history than is available is left blank (None) and the row
    is tagged `thin_history` so the brain knows the technicals aren't reliable yet.
    """
    rows: list[dict] = []
    for ticker, df in bars.items():
        if df is None or len(df) < 2:                  # need at least a prior close
            continue
        close = df["Close"].astype(float)
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        n = len(df)
        thin = n < config.SMA_SLOW + 1                 # short history -> unreliable technicals

        rsi = _num(_rsi(close, config.RSI_PERIOD).iloc[-1]) if n > config.RSI_PERIOD else None

        zscore = None
        if n >= config.ZSCORE_WINDOW:
            mean = close.rolling(config.ZSCORE_WINDOW).mean().iloc[-1]
            std = close.rolling(config.ZSCORE_WINDOW).std().iloc[-1]
            zscore = _num((last - mean) / std) if std and not np.isnan(std) else None

        momentum_pct = None
        if n >= config.SMA_SLOW:
            sma_fast = close.rolling(config.SMA_FAST).mean().iloc[-1]
            sma_slow = close.rolling(config.SMA_SLOW).mean().iloc[-1]
            momentum_pct = _num((sma_fast - sma_slow) / sma_slow * 100) if sma_slow else None

        daily_return = float((last / prev - 1) * 100) if prev else 0.0

        vol_ratio = None
        if "Volume" in df.columns and n >= 20:
            vol = df["Volume"].astype(float)
            avg_vol = vol.rolling(20).mean().iloc[-1]
            if avg_vol and not np.isnan(avg_vol):
                vol_ratio = _num(vol.iloc[-1] / avg_vol)

        flags = _flags(rsi, zscore, momentum_pct, vol_ratio)
        if thin:
            flags.append("thin_history")

        rows.append(
            {
                "ticker": ticker,
                "close": round(last, 2),
                "rsi_14": round(rsi, 1) if rsi is not None else None,
                "zscore_20": round(zscore, 2) if zscore is not None else None,
                "momentum_pct": round(momentum_pct, 2) if momentum_pct is not None else None,
                "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
                "daily_return_pct": round(daily_return, 2),
                "flags": flags,
            }
        )
    return rows


def format_table(rows: list[dict]) -> str:
    """Pretty, aligned table of signal rows for previews and debug logs."""
    if not rows:
        return "(no signals — not enough data?)"
    header = (
        f"{'ticker':<7}{'close':>9}{'rsi':>5}{'zscore':>8}"
        f"{'mom%':>8}{'vol':>6}{'day%':>7}  flags"
    )
    lines = [header, "-" * (len(header) + 8)]

    def f(val, width, prec):
        return f"{val:>{width}.{prec}f}" if val is not None else f"{'-':>{width}}"

    for r in rows:
        lines.append(
            f"{r['ticker']:<7}{r['close']:>9.2f}{f(r['rsi_14'], 5, 0)}{f(r['zscore_20'], 8, 2)}"
            f"{f(r['momentum_pct'], 8, 2)}{f(r['vol_ratio'], 6, 1)}{f(r['daily_return_pct'], 7, 2)}"
            f"  {', '.join(r['flags'])}"
        )
    return "\n".join(lines)
