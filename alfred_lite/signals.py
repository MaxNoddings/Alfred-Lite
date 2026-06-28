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


def _flags(rsi: float, zscore: float, momentum_pct: float, vol_ratio) -> list[str]:
    flags: list[str] = []
    if rsi < 30:
        flags.append("oversold")
    elif rsi > 70:
        flags.append("overbought")
    if zscore <= -2:
        flags.append("stretched_low")
    elif zscore >= 2:
        flags.append("stretched_high")
    flags.append("momentum_up" if momentum_pct > 0 else "momentum_down")
    if vol_ratio is not None and vol_ratio > 1.5:
        flags.append("high_volume")
    return flags


def compute_signals(bars: dict) -> list[dict]:
    """One signal row per ticker. Skips tickers with too little history."""
    rows: list[dict] = []
    for ticker, df in bars.items():
        if df is None or len(df) < config.SMA_SLOW + 1:
            continue
        close = df["Close"].astype(float)
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2])

        rsi = float(_rsi(close, config.RSI_PERIOD).iloc[-1])

        mean = close.rolling(config.ZSCORE_WINDOW).mean().iloc[-1]
        std = close.rolling(config.ZSCORE_WINDOW).std().iloc[-1]
        zscore = float((last - mean) / std) if std and not np.isnan(std) else 0.0

        sma_fast = close.rolling(config.SMA_FAST).mean().iloc[-1]
        sma_slow = close.rolling(config.SMA_SLOW).mean().iloc[-1]
        momentum_pct = float((sma_fast - sma_slow) / sma_slow * 100) if sma_slow else 0.0

        daily_return = float((last / prev - 1) * 100) if prev else 0.0

        vol_ratio = None
        if "Volume" in df.columns:
            vol = df["Volume"].astype(float)
            avg_vol = vol.rolling(20).mean().iloc[-1]
            if avg_vol and not np.isnan(avg_vol):
                vol_ratio = float(vol.iloc[-1] / avg_vol)

        rows.append(
            {
                "ticker": ticker,
                "close": round(last, 2),
                "rsi_14": round(rsi, 1),
                "zscore_20": round(zscore, 2),
                "momentum_pct": round(momentum_pct, 2),
                "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
                "daily_return_pct": round(daily_return, 2),
                "flags": _flags(rsi, zscore, momentum_pct, vol_ratio),
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
    for r in rows:
        vol = f"{r['vol_ratio']:.1f}" if r["vol_ratio"] is not None else "-"
        lines.append(
            f"{r['ticker']:<7}{r['close']:>9.2f}{r['rsi_14']:>5.0f}{r['zscore_20']:>8.2f}"
            f"{r['momentum_pct']:>8.2f}{vol:>6}{r['daily_return_pct']:>7.2f}"
            f"  {', '.join(r['flags'])}"
        )
    return "\n".join(lines)
