"""Full-market scan: rank every tradable US equity, hand the brain a shortlist.

Alfred used to see whatever Alpaca's screener called a "top mover" — a raw 1-day %
ranking that surfaces low-float pumps (IRE / IREX / IREG / IREZ ate four slots in one
run) and made the brain spend its news budget explaining why it was avoiding garbage.

This scans the WHOLE market instead. The key insight is that Alfred does not need to
SEE thousands of stocks to SCAN thousands of stocks:

    roster (~7,000)  →  bulk bars  →  rank  →  shortlist (~20)  →  the prompt

Stages 0-2 are pure arithmetic on bulk-downloaded bars — no model calls, no cost. Only
the shortlist reaches the brain, so prompt size and spend are unchanged; what changes
is that the shortlist is now drawn from the entire market rather than from a 15-name
screener call.

Ranking is RELATIVE STRENGTH versus the benchmark, not raw return: leaders are names
beating the index over both a fast and a slow window, laggards are the mirror image
(short candidates, or a reason to stay out). A hard dollar-volume floor removes the
illiquid junk the old screener kept surfacing.

Everything here degrades safely: any failure returns an empty result and the caller
falls back to the old screener path. A scan must never kill a trading run.
"""
from __future__ import annotations

import datetime as dt
import logging
import re

import numpy as np

from . import config

log = logging.getLogger(__name__)

_MAJOR_EXCHANGES = ("NASDAQ", "NYSE", "ARCA", "AMEX", "BATS")

# Leveraged / inverse ETPs must not reach the ranking. They reset daily and decay when
# held, so a "leader" among them is an artifact of the last few sessions, not a trend
# worth buying — and unfiltered they swamp the leaderboard outright (one run returned
# TZA, DRIP, TECS, SOXS and UVIX as the top five names in the entire market).
# Alfred's inverse exposure stays deliberately curated in config.INVERSE_ETFS.
# Heuristic by design: Alpaca exposes no "is leveraged" flag, so this reads the name.
_LEVERAGED_RE = re.compile(
    r"(?:\b-?\d+(?:\.\d+)?x\b"                 # 3X, 2x, 1.5X, -1x
    r"|ultra(?:short|pro)?\b"                  # ProShares Ultra / UltraShort / UltraPro
    r"|\binverse\b|\bleveraged\b"
    r"|\bvix\s+futures\b"
    r"|\bdirexion\s+daily\b"                   # Direxion's daily book is all leveraged
    r"|\bproshares\s+short\b)",
    re.IGNORECASE,
)


def roster() -> list[str]:
    """Every symbol Alfred could actually trade today.

    Filtered to fractionable names on major exchanges: Alfred sizes positions by
    NOTIONAL, and Alpaca only accepts notional orders on fractionable assets — so a
    non-fractionable name is one it could rank but never buy. Also drops OTC (junk,
    thin) and symbols containing "." (preferred/warrant classes that break lookups).
    """
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest

    client = TradingClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=True)
    assets = client.get_all_assets(
        GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
    )
    syms, leveraged = set(), 0
    for a in assets:
        if not (a.tradable and a.fractionable):
            continue
        if str(a.exchange).split(".")[-1] not in _MAJOR_EXCHANGES or "." in a.symbol:
            continue
        if _LEVERAGED_RE.search(a.name or ""):
            leveraged += 1
            continue
        syms.add(a.symbol)
    log.info("scan: excluded %d leveraged/inverse ETPs by name", leveraged)
    syms.add(config.REGIME_BENCHMARK)          # the yardstick must be in the sample
    return sorted(syms)


def fetch_bars(symbols: list[str], lookback_days: int | None = None) -> dict:
    """Daily bars for many symbols at once, batched. {symbol: DataFrame} (lowercase cols).

    Uses Alpaca's multi-symbol bars endpoint — thousands of names in a handful of
    requests, versus yfinance's one-call-per-ticker loop which would take ~30 minutes
    at this scale. A failed batch is logged and skipped, never raised.
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    days = lookback_days or config.SCAN_LOOKBACK_DAYS
    client = StockHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
    start = dt.date.today() - dt.timedelta(days=days)
    size = config.SCAN_BATCH_SIZE
    out: dict = {}

    for i in range(0, len(symbols), size):
        batch = symbols[i : i + size]
        try:
            df = client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=batch,
                    timeframe=TimeFrame.Day,
                    start=start,
                    feed=config.SCAN_FEED,
                    # MUST be split/dividend adjusted. Alpaca defaults to "raw", and a
                    # reverse split then reads as a monster rally: TZA showed 4.71 ->
                    # 41.80 (+787%) raw versus 46.65 -> 41.80 (-10.4%) adjusted. Raw
                    # bars put five reverse-split ETPs at the top of the market.
                    adjustment=config.SCAN_ADJUSTMENT,
                )
            ).df
        except Exception as exc:                # noqa: BLE001 - skip the batch, keep going
            log.warning("scan: batch %d-%d failed (%s)", i, i + len(batch), exc)
            continue
        if df is None or df.empty:
            continue
        for sym in df.index.get_level_values(0).unique():
            out[sym] = df.loc[sym]
    return out


def _metrics(df, fast: int, slow: int) -> dict | None:
    """Price, liquidity and trailing returns for one symbol — None if history is short."""
    if df is None or len(df) < slow + 1:
        return None
    close = df["close"].astype(float).to_numpy()
    volume = df["volume"].astype(float).to_numpy()
    price = float(close[-1])
    if not np.isfinite(price) or price <= 0:
        return None
    # Median (not mean) dollar volume: one halt-and-spike day shouldn't qualify a
    # name that is otherwise untradeable.
    window = min(20, len(close))
    dollar_vol = float(np.median(close[-window:] * volume[-window:]))
    return {
        "price": price,
        "dollar_vol": dollar_vol,
        "ret_fast": price / float(close[-fast - 1]) - 1.0,
        "ret_slow": price / float(close[-slow - 1]) - 1.0,
    }


def rank(bars: dict) -> list[dict]:
    """Score every symbol by relative strength vs the benchmark, best first.

    Score blends two windows so a name must be working over more than one horizon —
    the slow window is weighted higher because it is the more stable read, the fast
    one keeps the ranking responsive to a genuine change in leadership.
    """
    fast, slow = config.SCAN_RS_FAST, config.SCAN_RS_SLOW
    bench = _metrics(bars.get(config.REGIME_BENCHMARK), fast, slow)
    if bench is None:
        log.warning("scan: no benchmark history — cannot rank on relative strength")
        return []

    scored: list[dict] = []
    for sym, df in bars.items():
        if sym == config.REGIME_BENCHMARK:
            continue
        m = _metrics(df, fast, slow)
        if m is None:
            continue
        if m["price"] < config.SCAN_MIN_PRICE:
            continue
        if m["dollar_vol"] < config.SCAN_MIN_DOLLAR_VOLUME:
            continue
        rs_fast = m["ret_fast"] - bench["ret_fast"]
        rs_slow = m["ret_slow"] - bench["ret_slow"]
        scored.append({
            "ticker": sym,
            "score": config.SCAN_RS_SLOW_WEIGHT * rs_slow
            + (1 - config.SCAN_RS_SLOW_WEIGHT) * rs_fast,
            "rs_fast": rs_fast,
            "rs_slow": rs_slow,
            "price": m["price"],
            "dollar_vol": m["dollar_vol"],
        })
    scored.sort(key=lambda r: -r["score"])
    return scored


def shortlist(ranked: list[dict]) -> tuple[list[str], list[str]]:
    """(leaders, laggards) — the top and bottom of the ranking.

    Both ends are useful: leaders are long candidates, laggards are short candidates
    in a bear tape and a warning about what NOT to buy in any tape.
    """
    if not ranked:
        return [], []
    leaders = [r["ticker"] for r in ranked[: config.SCAN_TOP_LONGS]]
    laggards = [r["ticker"] for r in ranked[-config.SCAN_TOP_SHORTS :]][::-1]
    lead_set = set(leaders)
    laggards = [t for t in laggards if t not in lead_set]     # tiny universes can overlap
    return leaders, laggards


def run() -> dict:
    """Execute the whole scan. Returns {} on any failure — the caller falls back."""
    if not (config.ALPACA_API_KEY and config.ALPACA_SECRET_KEY):
        return {}
    try:
        syms = roster()
        log.info("scan: roster %d tradable names", len(syms))
        bars = fetch_bars(syms)
        log.info("scan: bars for %d names", len(bars))
        ranked = rank(bars)
        if not ranked:
            return {}
        leaders, laggards = shortlist(ranked)
        log.info(
            "scan: %d names passed liquidity (>=$%.0fM/day) — %d leaders, %d laggards",
            len(ranked), config.SCAN_MIN_DOLLAR_VOLUME / 1e6, len(leaders), len(laggards),
        )
        return {
            "leaders": leaders,
            "laggards": laggards,
            "ranked": ranked,
            "scanned": len(bars),
            "eligible": len(ranked),
        }
    except Exception as exc:                    # noqa: BLE001 - never kill a run
        log.warning("scan unavailable — falling back to screener: %s", exc)
        return {}


def format_table(ranked: list[dict], n: int = 10) -> str:
    """Top and bottom of the ranking, for previews and debug logs."""
    if not ranked:
        return "(no scan results)"
    head = f"{'ticker':<8}{'price':>9}{'rs_fast':>9}{'rs_slow':>9}{'score':>9}{'$vol/day':>12}"
    lines = [head, "-" * len(head)]

    def row(r):
        return (
            f"{r['ticker']:<8}{r['price']:>9.2f}{r['rs_fast'] * 100:>8.1f}%"
            f"{r['rs_slow'] * 100:>8.1f}%{r['score'] * 100:>8.1f}%"
            f"{r['dollar_vol'] / 1e6:>11.0f}M"
        )

    for r in ranked[:n]:
        lines.append(row(r))
    if len(ranked) > 2 * n:
        lines.append(f"{'…':<8}{len(ranked) - 2 * n} more")
    for r in ranked[-n:]:
        lines.append(row(r))
    return "\n".join(lines)
