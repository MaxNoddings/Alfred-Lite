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


def _unknown_regime(why: str) -> dict:
    return {
        "label": "unknown",
        "raw_label": "unknown",
        "confirmed": True,
        "high_vol": False,
        "gross_cap": min(config.REGIME_GROSS_CAP["unknown"], config.MAX_GROSS_EXPOSURE),
        "price": None,
        "sma_fast": None,
        "sma_slow": None,
        "realized_vol": None,
        "breadth": None,
        "note": f"regime unknown ({why}) — trading without a regime view",
    }


def _trend_label(price, sma_fast, sma_slow) -> str:
    """bull / chop / bear from price vs the two moving averages.

    With no 200-day available the primary trend is unknowable, so it falls back to the
    intermediate one rather than inventing a bearish reading from missing data.
    """
    above_fast = price > sma_fast
    above_slow = price > sma_slow if sma_slow is not None else above_fast   # _num() -> None on NaN
    if above_fast and above_slow:
        return "bull"
    if not above_fast and not above_slow:
        return "bear"
    return "chop"


def _confirmed_label(labels: list[str], k: int) -> str:
    """The most recent label that sustained a k-session run — the hysteresis filter.

    A raw label that flickers for a day or two never takes effect; Alfred keeps acting
    on the last regime that actually held. Stateless and deterministic: derived purely
    from the price series, so it needs no sidecar and stays backtestable.
    """
    if not labels:
        return "unknown"
    confirmed, run_label, run_len = labels[0], labels[0], 1
    for i, lab in enumerate(labels):
        if i:
            if lab == run_label:
                run_len += 1
            else:
                run_label, run_len = lab, 1
        if run_len >= k:
            confirmed = run_label
    return confirmed


def market_regime(bars: dict, rows: list[dict] | None = None) -> dict:
    """Classify the overall tape: bull / chop / bear, plus a high-volatility flag.

    Pure arithmetic on the benchmark's daily bars — no model call, no cost, runs every
    trading run. Three inputs:

      trend    — benchmark close vs its 50- and 200-day SMA (intermediate + primary)
      vol      — 20-day realized volatility, annualized (is the tape violent?)
      breadth  — share of the universe with positive momentum (is the move broad?)

    Returns the label plus `gross_cap`, the ceiling the executor applies to total
    exposure. Degrades to "unknown" (no throttle) whenever history is too short —
    a missing benchmark must never silently flatten the book.
    """
    df = bars.get(config.REGIME_BENCHMARK)
    if df is None or df.empty:
        return _unknown_regime(f"no {config.REGIME_BENCHMARK} bars")
    close = df["Close"].astype(float)
    if len(close) < config.REGIME_SMA_FAST + 1:
        return _unknown_regime(f"only {len(close)} bars of {config.REGIME_BENCHMARK}")

    price = float(close.iloc[-1])
    sma_fast = _num(close.rolling(config.REGIME_SMA_FAST).mean().iloc[-1])
    sma_slow = (
        _num(close.rolling(config.REGIME_SMA_SLOW).mean().iloc[-1])
        if len(close) >= config.REGIME_SMA_SLOW
        else None
    )
    if sma_fast is None:
        return _unknown_regime("benchmark SMA unavailable")

    rets = close.pct_change().dropna()
    realized_vol = (
        _num(float(rets.tail(config.REGIME_VOL_WINDOW).std()) * np.sqrt(252))
        if len(rets) >= config.REGIME_VOL_WINDOW
        else None
    )

    # Breadth over the names we actually looked at this run (benchmark excluded —
    # it is the thing being measured, not a vote).
    trending = [
        r for r in (rows or [])
        if r.get("momentum_pct") is not None and r["ticker"] != config.REGIME_BENCHMARK
    ]
    breadth = (
        sum(1 for r in trending if r["momentum_pct"] > 0) / len(trending)
        if trending else None
    )

    # Trend label over a trailing window, then hysteresis: Alfred acts on the last
    # label that HELD for REGIME_CONFIRM_DAYS, not on today's raw reading. The trend is
    # the flippy part (price hugging its 50-day), so that is what gets damped.
    fast_series = close.rolling(config.REGIME_SMA_FAST).mean()
    slow_series = (
        close.rolling(config.REGIME_SMA_SLOW).mean()
        if len(close) >= config.REGIME_SMA_SLOW
        else None
    )
    window = min(config.REGIME_LABEL_WINDOW, len(close) - config.REGIME_SMA_FAST)
    history: list[str] = []
    for i in range(len(close) - max(window, 1), len(close)):
        f = _num(fast_series.iloc[i])
        if f is None:
            continue
        s = _num(slow_series.iloc[i]) if slow_series is not None else None
        history.append(_trend_label(float(close.iloc[i]), f, s))

    raw_label = history[-1] if history else _trend_label(price, sma_fast, sma_slow)
    label = _confirmed_label(history, config.REGIME_CONFIRM_DAYS) if history else raw_label

    # Breadth is a same-day veto, not part of the damped trend: a tape can be
    # technically above its averages while almost nothing participates, and that is
    # not a bull market to lean into.
    if label == "bull" and breadth is not None and breadth < config.REGIME_BREADTH_MIN:
        label = "chop"

    high_vol = realized_vol is not None and realized_vol >= config.HIGH_VOL_ANNUALIZED
    gross_cap = config.REGIME_GROSS_CAP.get(label, config.MAX_GROSS_EXPOSURE)
    if high_vol:
        gross_cap *= config.HIGH_VOL_GROSS_MULT
    gross_cap = min(gross_cap, config.MAX_GROSS_EXPOSURE)

    bits = [
        f"{config.REGIME_BENCHMARK} {price:.2f}",
        f"{'above' if price > sma_fast else 'below'} 50d ({sma_fast:.2f})",
        f"{'above' if price > sma_slow else 'below'} 200d ({sma_slow:.2f})" if sma_slow
        else "200d n/a",
    ]
    if realized_vol is not None:
        bits.append(f"20d vol {realized_vol:.0%}{' — HIGH' if high_vol else ''}")
    if breadth is not None:
        bits.append(f"breadth {breadth:.0%} trending up")
    if raw_label != label:
        bits.append(
            f"today reads {raw_label} but has not held {config.REGIME_CONFIRM_DAYS} "
            f"sessions — staying {label}"
        )

    return {
        "label": label,
        "raw_label": raw_label,
        "confirmed": raw_label == label,
        "high_vol": high_vol,
        "gross_cap": round(gross_cap, 4),
        "price": round(price, 2),
        "sma_fast": round(sma_fast, 2),
        "sma_slow": round(sma_slow, 2) if sma_slow is not None else None,
        "realized_vol": round(realized_vol, 4) if realized_vol is not None else None,
        "breadth": round(breadth, 3) if breadth is not None else None,
        "note": f"{label.upper()}{' / HIGH VOL' if high_vol else ''}: " + "; ".join(bits),
    }


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
