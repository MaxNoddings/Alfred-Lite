"""Turn brain decisions into guardrailed orders (rebalance-to-target).

Each decision is a signed target weight; we trade the delta to reach it, then enforce
the hard limits the brain was asked to respect: position-size cap, max position count,
legal-only shorting, an anti-churn cooldown, and a dust floor. The brain proposes;
this disposes.
"""
from __future__ import annotations

import datetime as dt
import logging

from . import config

log = logging.getLogger(__name__)


def _recent_by_ticker(recent_trades: list[dict]) -> dict:
    """ticker -> (side, datetime) of its most recent logged trade."""
    out: dict = {}
    for r in recent_trades:
        ts, side = r.get("timestamp"), r.get("side")
        if not ts or not side:
            continue
        try:
            when = dt.datetime.fromisoformat(ts)
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        out[r["ticker"]] = (side, when)            # later rows win (most recent)
    return out


def plan_orders(decisions, portfolio, recent_trades, broker, now=None) -> list[dict]:
    """Apply guardrails to the brain's decisions and return executable orders."""
    now = now or dt.datetime.now(dt.timezone.utc)
    recent = _recent_by_ticker(recent_trades)
    equity = portfolio.equity
    open_count = len(portfolio.positions)
    cap = config.MAX_POSITION_PCT

    orders: list[dict] = []
    for d in decisions:
        ticker = d["ticker"]
        if d.get("action") == "hold":
            continue

        target = max(-cap, min(cap, float(d["target_pct"])))       # clamp to size cap
        desired = target * equity
        pos = portfolio.positions.get(ticker)
        current = pos.market_value if pos else 0.0
        delta = desired - current
        if abs(delta) < config.MIN_ORDER_USD:                      # dust floor
            continue
        side = "buy" if delta > 0 else "sell"

        if desired < 0 and (not config.ALLOW_SHORTING or not broker.is_shortable(ticker)):
            log.info("skip %s: short not permitted (legal-only shorting)", ticker)
            continue

        is_new = pos is None
        if is_new and open_count >= config.MAX_POSITIONS:
            log.info("skip %s: max positions (%d) reached", ticker, config.MAX_POSITIONS)
            continue

        last = recent.get(ticker)
        if (
            last
            and last[0] != side
            and (now - last[1]) < dt.timedelta(minutes=config.COOLDOWN_MINUTES)
        ):
            log.info("skip %s: cooldown — opposite of recent %s", ticker, last[0])
            continue

        if is_new:
            open_count += 1
        orders.append({
            "ticker": ticker,
            "side": side,
            "notional": round(abs(delta), 2),
            "action": d.get("action"),
            "target_pct": target,
            "confidence": d.get("confidence"),
            "reasoning": d.get("reasoning"),
        })
    return orders
