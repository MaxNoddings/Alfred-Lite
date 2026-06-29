"""Alfred Lite — one stateless trading run.

cron → read the market → ask the brain → guardrail → execute → log → exit.
"""
from __future__ import annotations

import datetime as dt
import logging

from alfred_lite import broker as bk
from alfred_lite import brain, config, data, executor, logbook, risk, signals, universe

log = logging.getLogger(__name__)


def _execute(broker, orders: list[dict], market_note: str) -> list[dict]:
    """Submit each order and return executed-trade rows for the log."""
    if not orders:
        return []
    prices = data.latest_prices([o["ticker"] for o in orders])
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    executed: list[dict] = []
    for o in orders:
        price = prices.get(o["ticker"])
        if price is None:
            log.warning("no price for %s — skipping order", o["ticker"])
            continue
        try:
            broker.submit(o["ticker"], o["side"], o["notional"])
        except Exception as exc:                       # one reject shouldn't kill the run
            log.warning("order failed for %s: %s", o["ticker"], exc)
            continue
        executed.append({
            "timestamp": ts,
            "ticker": o["ticker"],
            "action": o["action"],
            "side": o["side"],
            "notional": round(o["notional"], 2),
            "price": round(price, 2),
            "qty": round(o["notional"] / price, 4),
            "confidence": o["confidence"],
            "reasoning": o["reasoning"],
            "market_note": market_note,
        })
    return executed


def main(risk_only: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print("=" * 60)
    print(f"  🤵  ALFRED LITE{'  ·  risk sweep' if risk_only else ''}")
    print("=" * 60)
    print(config.summary())
    print("-" * 60)

    broker = bk.make_broker()
    if not broker.is_market_open():
        print("market closed — nothing to do.")
        return

    portfolio = broker.snapshot()
    forced_exits, _ = risk.evaluate(portfolio)          # update peaks, find stop/trail exits

    # Risk sweep: no brain, no data fetch — just enforce stops/trails and exit.
    if risk_only:
        orders = executor.plan_orders([], portfolio, [], broker, forced_exits=forced_exits)
        executed = _execute(broker, orders, "risk sweep")
        if executed:
            post = broker.snapshot()
            for r in executed:
                r["equity"], r["cash"] = round(post.equity, 2), round(post.cash, 2)
            logbook.append(executed)
        print(f"\nrisk sweep: {len(executed)} exit(s)" + (":" if executed else " — all clear"))
        for r in executed:
            print(f"  {r['side'].upper():<4} {r['ticker']:<5} ${r['notional']:>10,.2f}   {r['reasoning']}")
        print("\n=== PORTFOLIO ===")
        print(bk.format_portfolio(broker.snapshot()))
        return

    tickers = universe.build(list(portfolio.positions))
    bars = data.fetch_bars(tickers, config.LOOKBACK_DAYS)
    rows = signals.compute_signals(bars)
    rows = universe.enforce_price_floor(rows, list(portfolio.positions))   # penny-stock guard
    recent = logbook.recent_trades(config.RECENT_TRADES_FOR_CONTEXT)

    result = brain.decide(rows, portfolio, recent)
    print(f"\nmarket note: {result.get('market_note', '')}")
    print(f"brain cost : ${result.get('_cost_usd', 0):.4f}")

    orders = executor.plan_orders(
        result["decisions"], portfolio, recent, broker, forced_exits=forced_exits
    )
    executed = _execute(broker, orders, result.get("market_note", ""))
    if executed:
        post = broker.snapshot()                       # stamp each row with post-trade state
        for r in executed:
            r["equity"] = round(post.equity, 2)
            r["cash"] = round(post.cash, 2)
        logbook.append(executed)

    print(f"\nexecuted {len(executed)} order(s):")
    if executed:
        for r in executed:
            print(
                f"  {r['side'].upper():<4} {r['ticker']:<5} ${r['notional']:>10,.2f} "
                f"@ {r['price']:>8.2f}   {r['reasoning']}"
            )
    else:
        print("  (none — holds / guardrails / dust)")

    print("\n=== PORTFOLIO ===")
    print(bk.format_portfolio(broker.snapshot()))


if __name__ == "__main__":
    import sys

    main(risk_only="--risk-only" in sys.argv)
