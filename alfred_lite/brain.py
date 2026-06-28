"""The brain: deterministic signals + recent-trade memory + live news → a decision.

Two Claude (Opus 4.8) calls:
  1. _gather_news   — web_search pulls recent, market-moving news on the candidates.
  2. _make_decision — a structured JSON decision via output_config.format.

They're split because web search can attach citations, which are incompatible with
structured outputs; searching first, then deciding, keeps both.

Decision contract returned to the caller:
    {
      "decisions": [
        {
          "ticker", "action" (buy|sell|short|hold),
          "target_pct"  — desired position as a SIGNED fraction of equity
                          (+0.15 = 15% long, -0.10 = 10% short, 0 = flat/exit),
          "confidence"  — 0.0-1.0,
          "reasoning"   — one logged sentence,
        }, ...
      ],
      "market_note": str,
      "_news": str,            # news context, attached for logging (not from the schema)
    }
"""
from __future__ import annotations

import json
import logging

from . import config, signals as signals_mod
from .broker import Portfolio, format_portfolio

log = logging.getLogger(__name__)

# Cap searches so a single run can't spend minutes searching — bounds latency for
# the 15-minute cadence.
_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 6}

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["buy", "sell", "short", "hold"],
                    },
                    "target_pct": {"type": "number"},
                    "confidence": {"type": "number"},
                    "reasoning": {"type": "string"},
                },
                "required": [
                    "ticker", "action", "target_pct", "confidence", "reasoning",
                ],
                "additionalProperties": False,
            },
        },
        "market_note": {"type": "string"},
    },
    "required": ["decisions", "market_note"],
    "additionalProperties": False,
}

SYSTEM = """You are Alfred, a disciplined quantitative trading agent managing a \
$100,000 paper-trading account in a head-to-head competition. Your goal is to grow \
equity through smart, risk-aware decisions — not to trade for the sake of trading.

Each run you receive:
- A SIGNALS table (RSI-14, 20-day z-score, 5/20 momentum %, volume ratio, daily \
return %, and flags) for a watchlist.
- Your PORTFOLIO (cash, equity, open positions with unrealized P&L).
- Recent NEWS on the watchlist.
- YOUR RECENT TRADES and the reasoning behind them — your memory.

For each ticker you want to open, resize, or exit, output a decision:
- action: "buy" (open/increase long), "short" (open/increase short), "sell" \
(reduce/close), or "hold" (leave unchanged).
- target_pct: the DESIRED position as a SIGNED fraction of equity. +0.15 = 15% long, \
-0.10 = 10% short, 0 = flat/exit. (Ignored for "hold".)
- confidence: 0.0-1.0.
- reasoning: ONE tight sentence tying the call to the signals and/or news. It is \
logged — make it count.

Discipline (respect these; hard limits are also enforced downstream):
- Never target more than 20% of equity in one position (|target_pct| <= 0.20).
- Keep total positions to 5 or fewer.
- Only short with a clear bearish case on a liquid, shortable name.
- AVOID CHURN: do not reverse or thrash a position you opened in the last hour unless \
the thesis has genuinely broken. Holding is valid — often the best choice.
- Cash is a position. Doing nothing this run is completely fine.

Combine the quantitative signals with the news: signals frame the setup; news can \
confirm or veto it. Only include decisions for tickers you actually want to act on \
(or holds you're deliberately keeping) — omit tickers you have no view on. Always \
include a one-line market_note on overall conditions."""


def _client():
    import anthropic

    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _gather_news(client, rows: list[dict]) -> str:
    tickers = [r["ticker"] for r in rows]
    prompt = (
        "Search for the most recent (last 1-2 trading days) market-moving news on these "
        f"tickers: {', '.join(tickers)}. For each ticker WITH notable news, give 1-2 "
        "concise bullets (earnings, guidance, analyst actions, product / regulatory / "
        "macro). Omit tickers with nothing notable. Keep the whole summary tight."
    )
    messages = [{"role": "user", "content": prompt}]
    resp = client.messages.create(
        model=config.MODEL, max_tokens=4000, tools=[_WEB_SEARCH_TOOL], messages=messages
    )
    guard = 0
    while resp.stop_reason == "pause_turn" and guard < 4:        # server-tool loop limit
        messages.append({"role": "assistant", "content": resp.content})
        resp = client.messages.create(
            model=config.MODEL, max_tokens=4000, tools=[_WEB_SEARCH_TOOL], messages=messages
        )
        guard += 1
    # The final text block is the clean summary; earlier blocks are between-search
    # narration ("Let me search...") — drop them.
    texts = [b.text for b in resp.content if b.type == "text" and b.text.strip()]
    return texts[-1].strip() if texts else ""


def _format_recent_trades(recent_trades: list[dict]) -> str:
    if not recent_trades:
        return "none yet — this is an early run."
    lines = []
    for t in recent_trades:
        ts = t.get("ts", t.get("timestamp", "?"))
        lines.append(
            f"- {ts}  {t.get('action', '?').upper()} {t.get('ticker', '?')}: "
            f"{t.get('reasoning', '')}"
        )
    return "\n".join(lines)


def _make_decision(client, rows, portfolio, recent_trades, news) -> dict:
    user = (
        "=== SIGNALS ===\n"
        f"{signals_mod.format_table(rows)}\n\n"
        "=== PORTFOLIO ===\n"
        f"{format_portfolio(portfolio)}\n\n"
        "=== RECENT NEWS ===\n"
        f"{news or '(no news gathered)'}\n\n"
        "=== YOUR RECENT TRADES (memory) ===\n"
        f"{_format_recent_trades(recent_trades)}\n\n"
        "Make your decisions now."
    )
    resp = client.messages.create(
        model=config.MODEL,
        max_tokens=8000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": DECISION_SCHEMA}},
        messages=[{"role": "user", "content": user}],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def decide(
    signals: list[dict], portfolio: Portfolio, recent_trades: list[dict]
) -> dict:
    """Run the two-step brain and return the decision dict (see module docstring)."""
    if not signals:
        return {"decisions": [], "market_note": "no signals available", "_news": ""}
    client = _client()
    log.info("brain: gathering news for %d tickers", len(signals))
    news = _gather_news(client, signals)
    log.info("brain: making decision")
    result = _make_decision(client, signals, portfolio, recent_trades, news)
    result["_news"] = news
    return result
