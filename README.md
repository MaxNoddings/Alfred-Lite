# 🤵 Alfred Lite

A lightweight, Claude-powered paper-trading bot. Deterministic signals filter a
small watchlist; **Claude Opus 4.8** (with live news via web search) makes the
final buy / sell / short / hold call. Trades on a $100k Alpaca paper account.

## How it runs

A stateless script (`main.py`) that GitHub Actions wakes every ~15 min during
market hours:

```
fetch prices → compute signals → ask the brain → apply guardrails → place orders → log
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in your keys
python main.py              # Phase 0: prints the active config
```

## Keys you need

- **Anthropic API key** (+ a few $ of credit) — the brain.
- **Alpaca paper trading** key + secret — the $100k account.

## Design at a glance

- **Broker abstraction** (`broker.py`): `SimBroker` for offline dev, `AlpacaBroker`
  for live. One env var (`BROKER`) flips between them.
- **Signals** (`signals.py`): RSI, mean-reversion z-score, momentum, volume —
  fast, free, reproducible, backtestable.
- **Brain** (`brain.py`): signals + recent-trade memory + live news → a structured
  JSON decision per ticker.
- **Guardrails**: max 20% / position, max 5 positions, fractional (notional)
  orders, soft anti-churn cooldown, shorting allowed only when legally permitted.

## Build phases

| 0 | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| Scaffold ✅ | Signals | SimBroker | Brain | Wire + log | AlpacaBroker | GitHub Actions |
