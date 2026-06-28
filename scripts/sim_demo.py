"""Phase 2 demo: exercise SimBroker through a buy / short / trim / cover cycle.

Uses a throwaway state file (cleaned up at the end) so it won't touch state.json.

    python scripts/sim_demo.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from alfred_lite import broker as bk  # noqa: E402

STATE = "state_demo.json"


def show(b: bk.SimBroker, label: str) -> None:
    print(f"\n── {label} ──")
    print(bk.format_portfolio(b.snapshot()))


def main() -> None:
    b = bk.SimBroker(state_path=STATE, fresh=True)
    show(b, "start ($100k)")

    b.submit("AAPL", "buy", 15_000)
    b.submit("NVDA", "buy", 10_000)
    show(b, "after buying $15k AAPL + $10k NVDA")

    b.submit("TSLA", "sell", 5_000)          # sell from flat -> short
    show(b, "after shorting $5k TSLA")

    b.submit("AAPL", "sell", 7_500)          # trim half the AAPL long
    show(b, "after trimming $7.5k AAPL")

    b.submit("TSLA", "buy", 5_000)           # cover the short
    show(b, "after covering TSLA")

    pathlib.Path(STATE).unlink(missing_ok=True)
    print("\n(demo state cleaned up)")


if __name__ == "__main__":
    main()
