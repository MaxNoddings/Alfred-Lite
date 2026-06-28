"""The brain: deterministic signals + recent-trade memory + live news → a decision.

Two-step Claude call (Opus 4.8):
  1. web_search to gather recent news on the candidates (free text), then
  2. a structured JSON decision via output_config.format.

Lazy-imports `anthropic` so the rest of the package imports without it.

TODO Phase 3.
"""
from __future__ import annotations


def decide(signals: list[dict], portfolio, recent_trades: list[dict]) -> dict:
    """Return {"decisions": [...], "market_note": str}.

    Each decision: {ticker, action (buy|sell|short|hold), target_pct,
    confidence, reasoning}. Guardrails are applied by the caller, not here.
    """
    raise NotImplementedError("decide — Phase 3")
