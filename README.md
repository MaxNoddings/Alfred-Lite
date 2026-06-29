# 🤵 Alfred Lite

A lightweight, Claude-powered paper-trading bot. Deterministic signals scan a
**dynamic universe** of the day's biggest movers; **Claude Opus 4.8** — reading live
news via web search — makes the final buy / sell / short / hold call. Risk management
and position sizing are enforced in code, not left to the model. Trades a $100k
[Alpaca](https://alpaca.markets) paper account.

> Paper trading only. Educational project — not investment advice.

## How it works

A stateless run (`main.py`), woken on a schedule by GitHub Actions:

```
build universe → fetch prices → compute signals → gather news → ask the brain
              → risk overlay + guardrails → place orders → log
```

The **brain is a hybrid**: cheap, reproducible signals narrow the field, then Claude
makes the judgment call with live context. Two model calls per run keep it cheap —
**Haiku 4.5** gathers news (the costly web-search step, capped), **Opus 4.8** decides.
A full run costs roughly **$0.10**.

## Design

| Module | Role |
|---|---|
| `universe.py` | Dynamic universe: lean core + the day's top movers (Alpaca screener) + held names, penny-stock filtered |
| `data.py` | Daily OHLCV bars (yfinance) |
| `signals.py` | RSI, mean-reversion z-score, momentum, volume — fast, free, backtestable |
| `brain.py` | Signals + memory + live news → a structured JSON decision per ticker |
| `risk.py` | Deterministic exits that **override** the brain (stop-loss + trailing take-profit) |
| `executor.py` | Conviction-scaled sizing + gross-exposure guard + guardrails → orders |
| `broker.py` | `SimBroker` (offline dev) / `AlpacaBroker` (live) behind one interface |
| `logbook.py` | Append-only trade log + the brain's recent-trade memory |

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
| `WATCHLIST` | SPY, QQQ, NVDA, AAPL, MSFT, TSLA, SPCX | the stable core anchor |
| `MAX_MOVERS` / `MOVER_MIN_PRICE` | 15 / $5 | dynamic universe size + penny floor |
| `MAX_POSITION_PCT` / `BASE_POSITION_PCT` | 45% / 10% | conviction-sizing range |
| `STOP_LOSS_PCT` | 8% | hard stop below entry |
| `TRAIL_ACTIVATE_PCT` / `TRAIL_GIVEBACK_PCT` | 10% / 5% | trailing take-profit |
| `MODEL` / `NEWS_MODEL` | Opus 4.8 / Haiku 4.5 | decision vs. news models |

## Deployment

Two GitHub Actions workflows run during market hours (gated on Alpaca's clock):

- **`trade.yml`** — full run (signals + news + decisions) every 30 minutes.
- **`risk.yml`** — risk sweep (stops only, no model calls) every 10 minutes.

Both commit the trade log back to the repo. Keys are stored as encrypted repo secrets.

## Dev tools

`scripts/` holds read-only helpers: `preview.py` (live signals), `brain_demo.py`
(a live brain run + cost), `alpaca_check.py` (connection check), and the simulators.
