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
    "SH", "PSQ",            # -1x inverse S&P / Nasdaq — see INVERSE_ETFS below
]

# Inverse index ETFs: how Alfred profits from a falling tape WITHOUT shorting.
# Deliberately the -1x pair (not -2x/-3x): those reset daily and decay when held
# more than a day. Loss is bounded (an ETF can't go below zero), there is no borrow
# or recall risk, and they flow through the normal long-sizing path — no new logic.
INVERSE_ETFS: tuple[str, ...] = ("SH", "PSQ")

# Dynamic universe: pull today's biggest movers from Alpaca's screener each run.
# Only used when the full-market scan below is off or unavailable.
USE_DYNAMIC_UNIVERSE = True
MAX_MOVERS = 15             # max dynamic mover names added per run (on top of core + held)
MOVER_MIN_PRICE = 5.0       # penny-stock floor; enforced on the real fetched close
                            # (catches junk the screener gives no price for, e.g. backfill)

# ── Full-market scan (see scan.py) ───────────────────────────────────────────
# Replaces the screener's raw 1-day % "top movers" — which surfaced low-float pumps
# and near-duplicate junk (IRE/IREX/IREG/IREZ in a single run) — with a ranked sweep
# of EVERY tradable name. Pure arithmetic on bulk-downloaded bars: no model calls, no
# added prompt size, ~40s per run. Falls back to the screener on any failure.
USE_FULL_MARKET_SCAN = True
SCAN_LOOKBACK_DAYS = 120    # calendar days of bars pulled per name (~80 trading days)
SCAN_BATCH_SIZE = 1000      # symbols per bulk request (~5s each)
SCAN_FEED = "sip"           # consolidated tape. IEX sees ~3% of volume, which would
                            # make the dollar-volume floor below meaningless.
SCAN_ADJUSTMENT = "all"     # split + dividend adjusted. NEVER "raw" (Alpaca's default):
                            # a reverse split then reads as a huge rally and reverse-
                            # split ETPs take over the whole leaderboard.
SCAN_MIN_PRICE = MOVER_MIN_PRICE   # ONE penny floor for the whole system. Kept as an
                            # alias rather than a second literal so the scan and the
                            # post-fetch `enforce_price_floor` guard can never disagree.
SCAN_MIN_DOLLAR_VOLUME = 20_000_000   # median $/day — the junk filter that does the work
SCAN_RS_FAST = 20           # relative-strength windows (trading days)
SCAN_RS_SLOW = 60
SCAN_RS_SLOW_WEIGHT = 0.6   # weight on the slow window — the more stable read
SCAN_TOP_LONGS = 12         # leaders handed to the brain each run
SCAN_TOP_SHORTS = 8         # laggards: short candidates, and what NOT to buy

# ── Signal parameters (deterministic funnel) ─────────────────────────────────
RSI_PERIOD = 14
ZSCORE_WINDOW = 20          # lookback for the mean-reversion z-score
SMA_FAST = 5                # momentum: fast vs slow simple moving average
SMA_SLOW = 20
LOOKBACK_DAYS = 260         # history pulled per run — 260 trading days so the regime
                            # read below can compute a real 200-day SMA on the benchmark

# ── Market regime (deterministic, once per run — no model calls, no cost) ─────
# Alfred's biggest blind spot was not knowing WHAT MARKET IT WAS IN: it ranked names
# on RSI/momentum and bought the strongest, which in a chop tape means buying whatever
# just popped — precisely what mean-reverts. This classifies the tape from the
# benchmark's trend, its realized volatility, and universe breadth. The label is fed
# to the brain as context AND caps gross exposure in the executor, so it is guidance
# the model can reason about but cannot overrule.
REGIME_BENCHMARK = "SPY"
REGIME_SMA_FAST = 50        # price vs this = intermediate trend
REGIME_SMA_SLOW = 200       # price vs this = primary trend
REGIME_VOL_WINDOW = 20      # lookback for realized volatility
REGIME_BREADTH_MIN = 0.45   # fraction of the universe trending up required for "bull"
REGIME_CONFIRM_DAYS = 3     # HYSTERESIS: a new trend label must hold this many sessions
                            # before Alfred acts on it. Without it the raw label flips on
                            # noise — SPY sitting 0.3% from its 50-day produced 41 regime
                            # changes in 548 days, 10 of them lasting a single day, each
                            # yanking the gross cap 100%->60%->100%. That is the trade
                            # churn we already fixed, one level up. K=3 cuts it to 18
                            # flips and kills every 1-day run while leaving the regime
                            # mix almost unchanged (76/15/9 -> 78/13/9), i.e. it removes
                            # noise, not signal. Higher K adds lag entering a bear — the
                            # expensive direction to be late in.
REGIME_LABEL_WINDOW = 40    # trailing sessions evaluated to find the confirmed label
HIGH_VOL_ANNUALIZED = 0.25  # 20d realized vol at/above this = a high-volatility tape
HIGH_VOL_GROSS_MULT = 0.75  # ...which shrinks the regime's gross cap by this factor

# Gross-exposure ceiling per regime. Chop and bear deliberately throttle the book:
# the guard scales only NEW targets, so a tight cap stops Alfred ADDING risk into a
# bad tape — it never force-sells what it already holds.
REGIME_GROSS_CAP = {
    "bull": 1.00,           # trend up, decent breadth — full freedom
    "chop": 0.60,           # directionless: whipsaw risk, size down
    "bear": 0.50,           # primary downtrend: defend, favour cash/inverse
    "unknown": 1.00,        # not enough history — don't punish missing data
}

# ── Guardrails (risk management, applied AFTER the brain) ─────────────────────
# Position size scales with the brain's confidence: a max-conviction idea may reach
# MAX_POSITION_PCT, a marginal one is held to BASE_POSITION_PCT. "Bet bigger when the
# edge is bigger" — enforced. The gross cap keeps the whole book within equity (no margin).
MAX_POSITION_PCT = 0.45     # cap a top-conviction position at 45% of equity
BASE_POSITION_PCT = 0.20    # cap a low-conviction position at 20% of equity
MAX_GROSS_EXPOSURE = 1.00   # long + short exposure <= 100% of equity — NO leverage
MAX_POSITIONS = 5           # max concurrent positions (long or short)
MIN_ORDER_USD = 1.0         # don't submit dust (Alpaca's fractional floor)
MIN_TRADE_PCT = 0.02        # deadband: skip rebalances smaller than this % of equity.
                            # Kills the micro-rebalance treadmill (a target restated
                            # every run + drifting prices = endless tiny buys/sells).
                            # Full exits and risk exits bypass this — closing is always allowed.
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
MODEL = "claude-sonnet-5"        # decision model — near-Opus judgment at ~40% of the cost
                                 # (intro $2/$10 per Mtok through 2026-08-31)
NEWS_MODEL = "claude-haiku-4-5"  # news/search model — cheap; decisions stay on MODEL
RECENT_TRADES_FOR_CONTEXT = 10   # past trades fed to Claude as memory each run

# Candidate filtering: only research news for held positions + tickers carrying one of
# these "notable" flags, capped — cuts news cost and sharpens coverage. (Tune freely.)
# "leader"/"laggard" MUST be here. The brain may only OPEN a position on a concrete
# catalyst, and catalysts come from news — so a scan-surfaced idea with no news is one
# it is structurally unable to act on. Before these were included, 16 of 20 scan
# names reached the brain with no news, quietly wasting most of the scan's value.
NEWS_CANDIDATE_FLAGS = {
    "oversold", "overbought", "stretched_low", "stretched_high", "leader", "laggard",
}
MAX_NEWS_CANDIDATES = 12    # web_search is capped at 6 uses per run regardless, so a
                            # wider candidate list costs little beyond a few tokens.
                            # Not the full 20-name shortlist: coverage trades against
                            # depth per name, and the ranking means the tail matters
                            # least. Held + the top leaders and laggards get researched.

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
        f"max positions : {MAX_POSITIONS}  ·  gross <= {MAX_GROSS_EXPOSURE:.0%} hard ceiling "
        "(no margin; the regime cap below usually binds tighter)",
        f"universe      : {'full-market scan' if USE_FULL_MARKET_SCAN else 'screener movers'}"
        + (f"  ·  {SCAN_TOP_LONGS} leaders + {SCAN_TOP_SHORTS} laggards, "
           f">=${SCAN_MIN_DOLLAR_VOLUME/1e6:.0f}M/day" if USE_FULL_MARKET_SCAN else ""),
        f"trade deadband: {MIN_TRADE_PCT:.0%} of equity (full exits exempt)",
        "regime caps   : " + "  ".join(
            f"{k}={v:.0%}" for k, v in REGIME_GROSS_CAP.items() if k != "unknown"
        ) + f"  ·  high-vol x{HIGH_VOL_GROSS_MULT:.2f}",
        f"inverse ETFs  : {', '.join(INVERSE_ETFS)} (-1x, bounded loss, no borrow)",
        f"shorting      : {'on (legal only)' if ALLOW_SHORTING else 'off'}",
        f"cooldown      : {COOLDOWN_MINUTES} min",
        f"stop / trail  : -{STOP_LOSS_PCT:.0%} stop | arm +{TRAIL_ACTIVATE_PCT:.0%}, "
        f"give back {TRAIL_GIVEBACK_PCT:.0%}",
        f"keys set      : {keys_set}",
    ]
    return "\n".join(lines)
