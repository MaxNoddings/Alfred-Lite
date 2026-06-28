"""Risk overlay — deterministic exits that override the brain.

Two rules: cut losers (a hard stop below entry) and ride winners (a trailing
take-profit that arms once a position is in profit and exits only on a pullback
from its peak). Risk is non-negotiable here — it overrides the brain's decisions
and bypasses the anti-churn cooldown.

For held positions Alpaca's `market_value` is already marked to the current price,
so `evaluate` needs no brain and no extra data fetch — cheap enough to run on a
tight cron between full trading runs.

The trailing peak (high-water mark) per position is persisted to a small JSON
sidecar, committed back by the runner like trades.csv (the sim copy stays local).
"""
from __future__ import annotations

import json
import logging
import pathlib

from . import config

log = logging.getLogger(__name__)


def _load_state() -> dict:
    path = pathlib.Path(config.RISK_STATE_JSON)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):                       # corrupt/unreadable -> start clean
        log.warning("risk state unreadable — starting fresh")
        return {}


def _save_state(state: dict) -> None:
    pathlib.Path(config.RISK_STATE_JSON).write_text(json.dumps(state, indent=2))


def _exit_reason(is_long: bool, entry: float, price: float, peak: float) -> str | None:
    """Return a stop/trail reason string if this position should be flattened."""
    if entry <= 0:
        return None
    if is_long:
        ret = (price - entry) / entry
        if ret <= -config.STOP_LOSS_PCT:
            return f"stop_loss ({ret:+.1%})"
        peak_ret = (peak - entry) / entry
        if peak_ret >= config.TRAIL_ACTIVATE_PCT and price <= peak * (1 - config.TRAIL_GIVEBACK_PCT):
            return f"trailing_stop (peak {peak_ret:+.1%}, now {ret:+.1%})"
    else:                                               # short: profit when price falls
        ret = (entry - price) / entry
        if ret <= -config.STOP_LOSS_PCT:
            return f"stop_loss ({ret:+.1%})"
        peak_ret = (entry - peak) / entry
        if peak_ret >= config.TRAIL_ACTIVATE_PCT and price >= peak * (1 + config.TRAIL_GIVEBACK_PCT):
            return f"trailing_stop (peak {peak_ret:+.1%}, now {ret:+.1%})"
    return None


def evaluate(portfolio) -> tuple[list[dict], dict]:
    """Update peak state from the snapshot and return (forced_exits, state).

    forced_exits is a list of {ticker, reason} for positions breaching a stop or
    trailing-stop. Current price per position is derived from Alpaca's marked
    market_value, so this is a pure-arithmetic check with no external calls.
    """
    state = _load_state()
    forced: list[dict] = []
    held = set(portfolio.positions)

    for ticker, pos in portfolio.positions.items():
        if pos.qty == 0:
            continue
        price = pos.market_value / pos.qty              # marked to the current price
        is_long = pos.qty > 0
        side = "long" if is_long else "short"
        entry = pos.avg_price

        st = state.get(ticker)
        if st is None or st.get("side") != side:        # new or flipped position
            st = {"side": side, "entry": entry, "peak": price}
        st["peak"] = max(st["peak"], price) if is_long else min(st["peak"], price)
        state[ticker] = st

        reason = _exit_reason(is_long, entry, price, st["peak"])
        if reason:
            forced.append({"ticker": ticker, "reason": reason})
            log.info("risk exit %s: %s", ticker, reason)

    for ticker in list(state):                          # forget closed positions
        if ticker not in held:
            state.pop(ticker)

    _save_state(state)
    return forced, state
