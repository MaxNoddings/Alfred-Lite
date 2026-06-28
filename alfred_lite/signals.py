"""Deterministic signal funnel: RSI, mean-reversion z-score, momentum, volume.

Pure functions over price history — fast, free, reproducible, and BACKTESTABLE.
This is the layer we validate against historical data before trusting it live.

TODO Phase 1.
"""
from __future__ import annotations


def compute_signals(bars: dict) -> list[dict]:
    """Return one signal row per ticker: rsi, zscore, momentum, vol_ratio, flags."""
    raise NotImplementedError("compute_signals — Phase 1")
