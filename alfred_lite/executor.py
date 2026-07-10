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


def _effective_cap(confidence) -> float:
    """Max |position weight| allowed for a decision, scaled by the brain's conviction.

    A max-confidence idea may reach MAX_POSITION_PCT; a zero-confidence one is held to
    BASE_POSITION_PCT. Bet bigger when the edge is bigger.
    """
    base, ceiling = config.BASE_POSITION_PCT, config.MAX_POSITION_PCT
    try:
        c = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        c = 0.0
    return base + c * (ceiling - base)


def plan_orders(decisions, portfolio, recent_trades, broker, now=None, forced_exits=None) -> list[dict]:
    """Apply guardrails to the brain's decisions and return executable orders.

    `forced_exits` (from the risk overlay) is a list of {ticker, reason}. Those
    positions are flattened first — overriding any brain decision for the same
    ticker and bypassing the anti-churn cooldown (risk is non-negotiable).

    Sizing is conviction-scaled (see `_effective_cap`), then the whole book is held
    within MAX_GROSS_EXPOSURE of equity (no margin) by scaling the brain's targets.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    recent = _recent_by_ticker(recent_trades)
    equity = portfolio.equity
    forced = {f["ticker"]: f["reason"] for f in (forced_exits or [])}
    orders: list[dict] = []

    # 1) Risk exits first — flatten to zero, override the brain, ignore the cooldown.
    #    close_all = qty-based full close (no sliver left behind), not a notional sell.
    for ticker, reason in forced.items():
        pos = portfolio.positions.get(ticker)
        if not pos or pos.qty == 0:
            continue
        orders.append({
            "ticker": ticker,
            "side": "sell" if pos.qty > 0 else "buy",   # flatten long or cover short
            "notional": round(abs(pos.market_value), 2),
            "action": "exit",
            "target_pct": 0.0,
            "confidence": None,
            "reasoning": f"risk exit: {reason}",
            "close_all": True,
        })

    # 2) Conviction-scaled target weights for everything actionable (skip holds/exits).
    actionable = {
        d["ticker"]: d for d in decisions
        if d["ticker"] not in forced and d.get("action") != "hold"
    }
    targets: dict[str, float] = {}
    for ticker, d in actionable.items():
        cap = _effective_cap(d.get("confidence"))
        targets[ticker] = max(-cap, min(cap, float(d["target_pct"])))

    # 2b) Dust sweep: notional fills round to cents and can leave sub-$1 slivers that
    #     Alpaca won't accept sell orders for — but they still count as positions.
    #     Close them outright so they never clog position slots.
    for ticker, pos in portfolio.positions.items():
        if ticker in forced or ticker in targets or pos.qty == 0:
            continue
        if abs(pos.market_value) < config.MIN_ORDER_USD:
            orders.append({
                "ticker": ticker,
                "side": "sell" if pos.qty > 0 else "buy",
                "notional": round(abs(pos.market_value), 2),
                "action": "exit",
                "target_pct": 0.0,
                "confidence": None,
                "reasoning": "dust sweep: sub-$1 sliver freed its position slot",
                "close_all": True,
            })

    # 3) Gross-exposure guard: keep long+short within MAX_GROSS_EXPOSURE of equity.
    #    Held names the brain left alone (and isn't exiting) stay put and consume budget;
    #    scale the brain's NEW targets to fit whatever remains. Never levers past equity.
    held_kept = (
        sum(abs(p.market_value) for t, p in portfolio.positions.items()
            if t not in targets and t not in forced) / equity
        if equity > 0 else 0.0
    )
    brain_gross = sum(abs(w) for w in targets.values())
    available = config.MAX_GROSS_EXPOSURE - held_kept
    if brain_gross > available and brain_gross > 0:
        scale = max(0.0, available) / brain_gross
        targets = {t: w * scale for t, w in targets.items()}
        log.info("gross guard: scaling new targets x%.2f (kept book %.0f%%)", scale, held_kept * 100)

    # 4) Turn target weights into guardrailed orders.
    #    Position slots: dust slivers don't count, and anything exiting this run
    #    (risk exit, dust sweep, or a brain target of ~zero) frees its slot now.
    exiting = set(forced) | {
        t for t, w in targets.items()
        if t in portfolio.positions and abs(w * equity) < config.MIN_ORDER_USD
    }
    open_count = sum(
        1 for t, p in portfolio.positions.items()
        if abs(p.market_value) >= config.MIN_ORDER_USD and t not in exiting
    )
    for ticker, target in targets.items():
        d = actionable[ticker]
        desired = target * equity
        pos = portfolio.positions.get(ticker)
        current = pos.market_value if pos else 0.0
        delta = desired - current
        if abs(delta) < config.MIN_ORDER_USD:                      # dust floor
            continue
        side = "buy" if delta > 0 else "sell"
        full_close = pos is not None and abs(desired) < config.MIN_ORDER_USD

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
            # A flatten closes the WHOLE position by qty (no sliver); otherwise trade the delta.
            "notional": round(abs(current) if full_close else abs(delta), 2),
            "action": d.get("action"),
            "target_pct": round(target, 4),
            "confidence": d.get("confidence"),
            "reasoning": d.get("reasoning"),
            "close_all": full_close,
        })
    return orders
