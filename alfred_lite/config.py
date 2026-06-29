"""Alfred Lite configuration — watchlist, signal params, guardrails, env loading.

All tunable knobs live here so the rest of the package stays declarative.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# ── Universe ─────────────────────────────────────────────────────────────────
# A lean, stable CORE anchor (market ETFs + a few megacaps + SPCX/SpaceX). Each run
# the dynamic universe adds the day's biggest movers on top (see universe.py) plus any
# names we currently hold, so Alfred trades what's actually moving — not a fixed list.
WATCHLIST: list[str] = [
    "SPY", "QQQ", "NVDA", "AAPL", "MSFT", "TSLA", "SPCX",
]

# Dynamic universe: pull today's biggest movers from Alpaca's screener each run.
USE_DYNAMIC_UNIVERSE = True
MAX_MOVERS = 15             # max dynamic mover names added per run (on top of core + held)
MOVER_MIN_PRICE = 5.0       # penny-stock floor; enforced on the real fetched close
                            # (catches junk the screener gives no price for, e.g. backfill)

# ── Signal parameters (deterministic funnel) ─────────────────────────────────
RSI_PERIOD = 14
ZSCORE_WINDOW = 20          # lookback for the mean-reversion z-score
SMA_FAST = 5                # momentum: fast vs slow simple moving average
SMA_SLOW = 20
LOOKBACK_DAYS = 60          # history pulled per run to compute the above

# ── Guardrails (risk management, applied AFTER the brain) ─────────────────────
# Position size scales with the brain's confidence: a max-conviction idea may reach
# MAX_POSITION_PCT, a marginal one is held to BASE_POSITION_PCT. "Bet bigger when the
# edge is bigger" — enforced. The gross cap keeps the whole book within equity (no margin).
MAX_POSITION_PCT = 0.45     # cap a top-conviction position at 45% of equity
BASE_POSITION_PCT = 0.10    # cap a low-conviction position at 10% of equity
MAX_GROSS_EXPOSURE = 1.00   # long + short exposure <= 100% of equity — NO leverage
MAX_POSITIONS = 5           # max concurrent positions (long or short)
MIN_ORDER_USD = 1.0         # don't submit dust (Alpaca's fractional floor)
COOLDOWN_MINUTES = 45       # soft anti-churn: don't fully reverse within this window
ALLOW_SHORTING = True       # shorting allowed — but only when legally permitted
                            # (enough buying power AND the asset is shortable)

# ── Asymmetric exits (risk overlay — deterministic, OVERRIDES the brain) ──────
# Cut losers fast, ride winners. Evaluated every run from the live snapshot; cheap
# enough (no brain) to also run on a tight cron between full trading runs.
STOP_LOSS_PCT = 0.08        # hard exit if a position falls this far below entry
TRAIL_ACTIVATE_PCT = 0.10   # profit needed to arm the trailing take-profit
TRAIL_GIVEBACK_PCT = 0.05   # once armed, exit on this much pullback from the peak

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
# Trailing-stop high-water marks persist here between stateless runs (committed back
# from the runner, like trades.csv; the sim copy stays local).
RISK_STATE_JSON = "risk_state.json" if BROKER == "alpaca" else "risk_state_sim.json"


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
        f"position size : {BASE_POSITION_PCT:.0%}-{MAX_POSITION_PCT:.0%} by conviction",
        f"max positions : {MAX_POSITIONS}  ·  gross <= {MAX_GROSS_EXPOSURE:.0%} (no margin)",
        f"shorting      : {'on (legal only)' if ALLOW_SHORTING else 'off'}",
        f"cooldown      : {COOLDOWN_MINUTES} min",
        f"stop / trail  : -{STOP_LOSS_PCT:.0%} stop | arm +{TRAIL_ACTIVATE_PCT:.0%}, "
        f"give back {TRAIL_GIVEBACK_PCT:.0%}",
        f"keys set      : {keys_set}",
    ]
    return "\n".join(lines)
