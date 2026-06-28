"""Alfred Lite configuration — watchlist, signal params, guardrails, env loading.

All tunable knobs live here so the rest of the package stays declarative.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# ── Universe ─────────────────────────────────────────────────────────────────
# Small, liquid watchlist. Keeps each run fast, cheap, and focused. Easy to grow.
WATCHLIST: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",
    "META", "TSLA", "AMD", "SPY", "QQQ",
]

# ── Signal parameters (deterministic funnel) ─────────────────────────────────
RSI_PERIOD = 14
ZSCORE_WINDOW = 20          # lookback for the mean-reversion z-score
SMA_FAST = 5                # momentum: fast vs slow simple moving average
SMA_SLOW = 20
LOOKBACK_DAYS = 60          # history pulled per run to compute the above

# ── Guardrails (risk management, applied AFTER the brain) ─────────────────────
MAX_POSITION_PCT = 0.20     # cap any single position at 20% of equity
MAX_POSITIONS = 5           # max concurrent positions (long or short)
MIN_ORDER_USD = 1.0         # don't submit dust (Alpaca's fractional floor)
COOLDOWN_MINUTES = 45       # soft anti-churn: don't fully reverse within this window
ALLOW_SHORTING = True       # shorting allowed — but only when legally permitted
                            # (enough buying power AND the asset is shortable)

# ── The brain ────────────────────────────────────────────────────────────────
MODEL = "claude-opus-4-8"        # decision model — premium judgment where it counts
NEWS_MODEL = "claude-haiku-4-5"  # news/search model — cheap; Opus stays on the decision
RECENT_TRADES_FOR_CONTEXT = 10   # past trades fed to Claude as memory each run

# Candidate filtering: only research news for held positions + tickers carrying one of
# these "notable" flags, capped — cuts news cost and sharpens coverage. (Tune freely.)
NEWS_CANDIDATE_FLAGS = {"oversold", "overbought", "stretched_low", "stretched_high"}
MAX_NEWS_CANDIDATES = 6

# ── Accounts / runtime ───────────────────────────────────────────────────────
BROKER = os.getenv("BROKER", "sim").lower()         # "sim" | "alpaca"
STARTING_CASH = 100_000.0                            # SimBroker starting balance

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"

# ── Logging ──────────────────────────────────────────────────────────────────
# Live (alpaca) log is committed back from the runner; the sim log stays local.
TRADES_CSV = "trades.csv" if BROKER == "alpaca" else "trades_sim.csv"


def summary() -> str:
    """Human-readable snapshot of the active config (never prints secret values)."""
    present = [
        name
        for name, val in (
            ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
            ("ALPACA_API_KEY", ALPACA_API_KEY),
            ("ALPACA_SECRET_KEY", ALPACA_SECRET_KEY),
        )
        if val
    ]
    keys_set = ", ".join(present) if present else "none"
    lines = [
        f"broker        : {BROKER}",
        f"model         : {MODEL}",
        f"watchlist     : {', '.join(WATCHLIST)}",
        f"max position  : {MAX_POSITION_PCT:.0%} of equity",
        f"max positions : {MAX_POSITIONS}",
        f"shorting      : {'on (legal only)' if ALLOW_SHORTING else 'off'}",
        f"cooldown      : {COOLDOWN_MINUTES} min",
        f"keys set      : {keys_set}",
    ]
    return "\n".join(lines)
