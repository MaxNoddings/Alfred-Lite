"""Alfred Lite — one stateless trading run.

Wakes up (cron) → reads the market → asks the brain → places orders → logs → exits.

Phase 0: wiring only — prints the active config so we can confirm setup. The full
pipeline (fetch → signals → brain → guardrails → orders → log) lands in later phases.
"""
from __future__ import annotations

from alfred_lite import config


def main() -> None:
    print("=" * 60)
    print("  🤵  ALFRED LITE")
    print("=" * 60)
    print(config.summary())
    print("-" * 60)
    # TODO Phase 1-4: fetch → signals → brain → guardrails → orders → log
    print("scaffold OK — pipeline not yet wired (Phase 0)")


if __name__ == "__main__":
    main()
