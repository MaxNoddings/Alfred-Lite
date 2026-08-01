# 🤵 Alfred Lite

A lightweight, Claude-powered paper-trading bot. Deterministic signals scan a
**dynamic universe** of the day's biggest movers; **Claude Sonnet 5** — reading live
news via web search — makes the final buy / sell / short / hold call. Risk management
and position sizing are enforced in code, not left to the model. The executor is
deliberately **low-churn**: hold is the default, and a trade deadband stops the
bot from micro-rebalancing itself to death. Trades a $100k
[Alpaca](https://alpaca.markets) paper account.

> Paper trading only. Educational project — not investment advice.

## How it works

A stateless run (`main.py`), woken on a schedule by GitHub Actions:

```
build universe → fetch prices → compute signals → read the regime → gather news
              → ask the brain → risk overlay + guardrails → place orders → log
```

The **brain is a hybrid**: cheap, reproducible signals narrow the field, then Claude
makes the judgment call with live context. Two model calls per run keep it cheap —
**Haiku 4.5** gathers news (the costly web-search step, capped), **Sonnet 5** decides.
A full run costs roughly **$0.07–0.15** (the web-search step dominates; the free
deterministic scan and regime read add nothing). The brain's default answer is **hold**: it only
speaks when a thesis breaks, a real catalyst appears, or a resize is worth ≥5 points
of equity — winners are exited by the trailing stop, not by second-guessing.

## Design

| Module | Role |
|---|---|
| `scan.py` | **Full-market scan**: ranks every tradable name by relative strength → shortlist |
| `universe.py` | Assembles the run's universe: core + scan shortlist (or screener) + held names |
| `data.py` | Daily OHLCV bars (yfinance) |
| `signals.py` | RSI, z-score, momentum, volume + the **market-regime read** — fast, free, backtestable |
| `brain.py` | Signals + memory + live news → a structured JSON decision per ticker |
| `risk.py` | Deterministic exits that **override** the brain (stop-loss + trailing take-profit) |
| `executor.py` | Conviction-scaled sizing + gross-exposure guard + guardrails → orders |
| `broker.py` | `SimBroker` (offline dev) / `AlpacaBroker` (live) behind one interface |
| `logbook.py` | Append-only trade log + the brain's recent-trade memory |

## Full-market scan

Alfred sees the **whole market** every run — not a fixed watchlist, and not the raw
1-day-% "top movers" the screener used to hand it (which surfaced low-float pumps and
near-duplicate junk: `IRE / IREX / IREG / IREZ` once ate four slots in a single run).

The trick is that Alfred doesn't need to *see* thousands of stocks to *scan* thousands
of stocks:

```
roster ~6,800  →  bulk bars  →  rank by relative strength  →  shortlist ~20  →  prompt
```

Stages 1–3 are pure arithmetic on bulk-downloaded bars — **no model calls, no added
prompt size, no added spend**; the whole sweep takes ~30s. What changes is that the
shortlist is drawn from every tradable name rather than from a 15-name screener call.

- **Roster** — every fractionable, major-exchange US equity. Fractionable matters:
  Alfred sizes by notional, and Alpaca only accepts notional orders on fractionable
  assets, so a non-fractionable name is one it could rank but never buy.
- **Ranking** — relative strength vs the benchmark over a fast (20d) and slow (60d)
  window, not raw return. A stock up 10% in a market up 10% has earned nothing.
- **Liquidity floor** — median $20M/day traded. This is the filter that does the real
  work of killing junk.
- **Leveraged/inverse ETPs excluded** — they reset daily and decay when held, and
  unfiltered they swamp the leaderboard outright. Alfred's inverse exposure stays
  curated (`INVERSE_ETFS`).
- **Both ends kept** — leaders are long candidates; laggards are short candidates and a
  standing warning about what *not* to buy. Each is flagged `leader` / `laggard` in the
  signals table so the brain reads the ranking, not just the technicals.

Bars are **split- and dividend-adjusted**. Alpaca defaults to raw, and on raw bars a
reverse split reads as a monster rally — TZA showed `4.71 → 41.80` (+787%) raw versus
`46.65 → 41.80` (−10.4%) adjusted, which put five reverse-split ETPs atop the market.

The scan degrades safely: any failure falls back to the old screener path, and
`USE_FULL_MARKET_SCAN = False` disables it outright.

## Market regime (deterministic — no model calls)

Alfred's worst blind spot was not knowing *what market it was in*: it ranked names on
RSI and momentum and bought the strongest, which in a directionless tape means buying
whatever just popped — precisely what mean-reverts. Every run now opens with a regime
read computed from the benchmark's trend (50-/200-day SMA), its 20-day realized
volatility, and universe breadth:

| Regime | Read | Gross cap |
|---|---|---|
| **bull** | above both SMAs, breadth confirming | 100% |
| **chop** | mixed — the whipsaw tape | 60% |
| **bear** | below both SMAs | 50% |
| *high vol* | 20-day realized vol ≥ 25% (any regime) | ×0.75 |

The label is passed to the brain as context **and** enforced as a gross-exposure
ceiling in the executor — guidance the model can reason about but cannot overrule. The
cap scales only *new* targets, so a tight regime throttles fresh risk without ever
force-selling the existing book. Missing history degrades to `unknown` (no throttle):
an absent benchmark must never silently flatten the account.

To profit from a falling tape, the watchlist carries **SH** and **PSQ** — the *-1x*
inverse S&P and Nasdaq ETFs. Loss is bounded, there is no borrow or recall risk, and
they route through the normal long-sizing path. The leveraged (-2x/-3x) versions are
deliberately excluded: they reset daily and decay when held.

## Risk & sizing (enforced in code)

- **Conviction sizing** — position size scales with the brain's confidence, from ~10%
  up to 45% of equity. Bet bigger when the edge is bigger.
- **Gross-exposure guard** — long + short exposure capped at 100% of equity. No margin.
- **Asymmetric exits** — a hard stop-loss cuts losers; a trailing take-profit rides
  winners and exits only on a pullback from the peak. These override the brain.
- **Legal-only shorting** — shorts only when the asset is actually borrowable.
- **Anti-churn cooldown** — won't thrash a position it just opened.

A lightweight **risk sweep** runs between full trading runs to enforce stops promptly,
with no model calls (it reads the live snapshot — essentially free).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in your keys
python main.py              # runs against the simulator by default (BROKER=sim)
```

You'll need an **Anthropic API key** (a few dollars of credit) and **Alpaca paper
trading** keys. Flip `BROKER=sim → alpaca` in `.env` to trade the paper account.

## Configuration

All tunables live in `alfred_lite/config.py`:

| Knob | Default | What it does |
|---|---|---|
| `WATCHLIST` | SPY, QQQ, NVDA, AAPL, MSFT, TSLA, SPCX, SH, PSQ | the stable core anchor |
| `INVERSE_ETFS` | SH, PSQ | -1x index ETFs — downside without shorting |
| `REGIME_GROSS_CAP` | bull 100% / chop 60% / bear 50% | gross-exposure ceiling per regime |
| `HIGH_VOL_ANNUALIZED` / `HIGH_VOL_GROSS_MULT` | 25% / ×0.75 | violent tape → shrink the book |
| `MAX_MOVERS` / `MOVER_MIN_PRICE` | 15 / $5 | screener-fallback universe size + penny floor |
| `USE_FULL_MARKET_SCAN` | `True` | scan every tradable name instead of the screener |
| `SCAN_MIN_DOLLAR_VOLUME` | $20M/day | liquidity floor — the main junk filter |
| `SCAN_TOP_LONGS` / `SCAN_TOP_SHORTS` | 12 / 8 | leaders + laggards handed to the brain |
| `SCAN_RS_FAST` / `SCAN_RS_SLOW` | 20d / 60d | relative-strength windows |
| `MAX_POSITION_PCT` / `BASE_POSITION_PCT` | 45% / 20% | conviction-sizing range |
| `MIN_TRADE_PCT` | 2% | trade deadband — skip rebalances smaller than this % of equity (full exits exempt) |
| `STOP_LOSS_PCT` | 8% | hard stop below avg cost |
| `TRAIL_ACTIVATE_PCT` / `TRAIL_GIVEBACK_PCT` | 10% / 5% | trailing take-profit, armed from original entry |
| `MODEL` / `NEWS_MODEL` | Sonnet 5 / Haiku 4.5 | decision vs. news models |

## Deployment

Two GitHub Actions workflows run during market hours (gated on Alpaca's clock).
GitHub's built-in cron proved unreliable, so both are fired via `workflow_dispatch`
by an **external cron service (cron-jobs.org)** — cadence changes happen there, not
in the yml:

- **`trade.yml`** — full run (signals + news + decisions), triggered hourly.
- **`risk.yml`** — risk sweep (stops only, no model calls), triggered every 15 minutes.

Both commit the trade log back to the repo. Keys are stored as encrypted repo secrets.

## Dev tools

`scripts/` holds read-only helpers: `preview.py` (live signals), `brain_demo.py`
(a live brain run + cost), `alpaca_check.py` (connection check), and the simulators.
