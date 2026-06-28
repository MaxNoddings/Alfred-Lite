"""Phase 3 demo: run the brain on live signals + a seeded portfolio.

Makes LIVE Anthropic API calls (web_search + a structured decision) — a few cents
of credit per run. Seeds an NVDA position + a memory entry so we can see Alfred
manage an existing holding, not just open fresh ones. Uses a throwaway state file.

    python scripts/brain_demo.py
"""
from __future__ import annotations

import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from alfred_lite import brain, config, data, signals  # noqa: E402
from alfred_lite import broker as bk  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

STATE = "state_demo.json"


def _print_decisions(decisions: list[dict]) -> None:
    if not decisions:
        print("(no action this run)")
        return
    print(f"{'ticker':<7}{'action':<7}{'target%':>9}{'conf':>6}  reasoning")
    print("-" * 78)
    for d in decisions:
        print(
            f"{d['ticker']:<7}{d['action']:<7}{d['target_pct'] * 100:>8.1f}%"
            f"{d['confidence']:>6.2f}  {d['reasoning']}"
        )


def main() -> None:
    import anthropic

    print(f"anthropic SDK {anthropic.__version__}\n")

    # Seed a portfolio so we can see position management + the memory feed in action.
    b = bk.SimBroker(state_path=STATE, fresh=True)
    b.submit("NVDA", "buy", 12_000)
    portfolio = b.snapshot()
    recent = [
        {
            "ts": "yesterday 15:30",
            "ticker": "NVDA",
            "action": "buy",
            "reasoning": "oversold bounce + momentum setup",
        }
    ]

    rows = signals.compute_signals(data.fetch_bars(config.WATCHLIST, config.LOOKBACK_DAYS))

    print("=== PORTFOLIO (seeded) ===")
    print(bk.format_portfolio(portfolio))
    print("\n=== SIGNALS ===")
    print(signals.format_table(rows))
    print()

    result = brain.decide(rows, portfolio, recent)

    print("\n=== NEWS (web_search) ===")
    print(result.get("_news", "") or "(none)")
    print(f"\n=== MARKET NOTE ===\n{result['market_note']}")
    print("\n=== DECISIONS ===")
    _print_decisions(result["decisions"])

    pathlib.Path(STATE).unlink(missing_ok=True)
    print("\n(demo state cleaned up)")


if __name__ == "__main__":
    main()
