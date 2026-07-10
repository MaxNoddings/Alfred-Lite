"""The brain: deterministic signals + recent-trade memory + live news → a decision.

Two Claude calls:
  1. _gather_news   — web_search pulls recent, market-moving news on the candidates
                      (NEWS_MODEL — the cost lever).
  2. _make_decision — a structured JSON decision via output_config.format (MODEL).

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
      "_news": str,          # news context, attached for logging (not from the schema)
      "_usage": {"news": {...}, "decision": {...}},
      "_cost_usd": float,    # estimated cost of this run
    }
"""
from __future__ import annotations

import json
import logging

from . import config, signals as signals_mod
from .broker import Portfolio, format_portfolio

log = logging.getLogger(__name__)

# The 20260209 (dynamic-filtering) web_search runs code execution under the hood and
# is only supported on Opus 4.6+/Sonnet 4.6. Cheaper/older models (e.g. Haiku) must use
# the basic 20250305 variant. max_uses caps searches so a run can't spend minutes (and
# dollars) searching unbounded.
_DYNAMIC_SEARCH_MODELS = {
    "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6",
}


def _web_search_tool(model: str) -> dict:
    variant = (
        "web_search_20260209" if model in _DYNAMIC_SEARCH_MODELS else "web_search_20250305"
    )
    return {"type": variant, "name": "web_search", "max_uses": 6}

# Per-1M-token prices (input, output); web search billed per request ($10 / 1k).
_PRICES = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
_WEB_SEARCH_USD = 0.01

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

SYSTEM = """You are Alfred, a trading agent in a short, winner-take-all paper-trading \
competition: one opponent, $100,000 each, and ONLY the final equity ranking matters. \
Second place is last place. Your opponent runs sophisticated ML models and quant \
algorithms — you will not out-compute him with caution. Your edge is judgment, live \
news, and the willingness to concentrate. A flat account is a slow loss; there is no \
prize for a good Sharpe ratio.

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

How to play (hard limits are also enforced downstream):
- SIZE TO WIN: your `confidence` sets how large a position may be. A marginal idea \
starts around 20% of equity, a solid one 25-35%, and your single best idea deserves \
the full 45%. Concentration in your best ideas is the strategy, not a risk to manage.
- STAY DEPLOYED: aim to keep most of the book working. Cash is not safety — it is a \
guaranteed loss to an opponent who is invested. Hold cash only when you genuinely \
expect better prices within a day or two.
- NO DEAD MONEY: capital must have a path to move. Exit positions whose upside is \
capped or pinned — e.g. an announced cash-merger target trading at a tight spread \
goes nowhere for months; that capital must be redeployed into something that can run.
- PRESS REAL CATALYSTS: earnings beats, guidance raises, approvals, big contracts on \
liquid names are exactly where outsized moves live — lean in while the move is young. \
Still avoid being exit liquidity on thin, low-float pump-and-dumps; if such a name is \
genuinely liquid and shortable, shorting the blow-off is a legitimate weapon.
- Keep the whole book within 100% of equity — NO leverage (long exposure + short \
exposure <= equity). Keep total positions to 5 or fewer — concentration over spray.
- AVOID CHURN: do not reverse or thrash a position you opened in the last hour unless \
the thesis has genuinely broken. A deterministic risk overlay (stop-loss + trailing \
stop) already cuts losers and protects winners — you don't need to micro-trim; let \
winners breathe.

Combine the quantitative signals with the news: signals frame the setup; news can \
confirm or veto it. Only include decisions for tickers you actually want to act on \
(or holds you're deliberately keeping) — omit tickers you have no view on. Always \
include a one-line market_note on overall conditions."""


def _client():
    import anthropic

    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


# ── usage / cost accounting ───────────────────────────────────────────────────
def _blank_usage() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read": 0,
        "cache_write": 0,
        "web_searches": 0,
    }


def _add_usage(acc: dict, resp) -> dict:
    u = resp.usage
    acc["input_tokens"] += getattr(u, "input_tokens", 0) or 0
    acc["output_tokens"] += getattr(u, "output_tokens", 0) or 0
    acc["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
    acc["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
    stu = getattr(u, "server_tool_use", None)
    if stu is not None:
        acc["web_searches"] += getattr(stu, "web_search_requests", 0) or 0
    return acc


def _cost(usage: dict, model: str) -> float:
    inp, out = _PRICES.get(model, (5.0, 25.0))
    return (
        usage["input_tokens"] / 1e6 * inp
        + usage["output_tokens"] / 1e6 * out
        + usage["cache_read"] / 1e6 * inp * 0.1
        + usage["cache_write"] / 1e6 * inp * 1.25
        + usage["web_searches"] * _WEB_SEARCH_USD
    )


# ── the two steps ─────────────────────────────────────────────────────────────
def _gather_news(client, rows: list[dict]) -> tuple[str, dict]:
    tickers = [r["ticker"] for r in rows]
    prompt = (
        "Search for the most recent (last 1-2 trading days) market-moving news on these "
        f"tickers: {', '.join(tickers)}. For each ticker WITH notable news, give 1-2 "
        "concise bullets (earnings, guidance, analyst actions, product / regulatory / "
        "macro). Omit tickers with nothing notable. Keep the whole summary tight. "
        "Output ONLY the final news summary — do not narrate your search process or "
        "add any preamble."
    )
    messages = [{"role": "user", "content": prompt}]
    usage = _blank_usage()
    tool = _web_search_tool(config.NEWS_MODEL)
    resp = client.messages.create(
        model=config.NEWS_MODEL, max_tokens=4000, tools=[tool], messages=messages
    )
    _add_usage(usage, resp)
    guard = 0
    while resp.stop_reason == "pause_turn" and guard < 4:        # server-tool loop limit
        messages.append({"role": "assistant", "content": resp.content})
        resp = client.messages.create(
            model=config.NEWS_MODEL, max_tokens=4000, tools=[tool], messages=messages
        )
        _add_usage(usage, resp)
        guard += 1
    # Join ALL text blocks — the summary can span several (esp. with the basic search
    # variant). Completeness matters more than dropping a little search narration.
    text = "\n".join(b.text for b in resp.content if b.type == "text" and b.text.strip())
    return text.strip(), usage


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


def _make_decision(client, rows, portfolio, recent_trades, news) -> tuple[dict, dict]:
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
    usage = _add_usage(_blank_usage(), resp)
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text), usage


def _news_candidates(rows: list[dict], portfolio: Portfolio) -> list[dict]:
    """Pick the tickers worth researching: held positions + flagged names, capped.

    The decision step still sees ALL signals; only the (costly) news step is
    narrowed. Falls back to the most-stretched names if nothing stands out.
    """
    held = set(portfolio.positions)
    held_rows = [r for r in rows if r["ticker"] in held]
    flagged = [r for r in rows if set(r["flags"]) & config.NEWS_CANDIDATE_FLAGS]
    ordered, seen = [], set()
    for r in held_rows + sorted(flagged, key=lambda x: -abs(x["zscore_20"] or 0)):
        if r["ticker"] not in seen:
            seen.add(r["ticker"])
            ordered.append(r)
    if not ordered:
        ordered = sorted(rows, key=lambda x: -abs(x["zscore_20"] or 0))[:3]
    return ordered[: config.MAX_NEWS_CANDIDATES]


def decide(
    signals: list[dict], portfolio: Portfolio, recent_trades: list[dict]
) -> dict:
    """Run the two-step brain and return the decision dict (see module docstring)."""
    if not signals:
        return {
            "decisions": [],
            "market_note": "no signals available",
            "_news": "",
            "_usage": {},
            "_cost_usd": 0.0,
        }
    client = _client()
    candidates = _news_candidates(signals, portfolio)
    log.info(
        "brain: gathering news for %d/%d tickers (model %s)",
        len(candidates), len(signals), config.NEWS_MODEL,
    )
    news, news_usage = _gather_news(client, candidates)
    log.info("brain: making decision (model %s)", config.MODEL)
    result, dec_usage = _make_decision(client, signals, portfolio, recent_trades, news)

    result["_news"] = news
    result["_usage"] = {"news": news_usage, "decision": dec_usage}
    result["_cost_usd"] = _cost(news_usage, config.NEWS_MODEL) + _cost(dec_usage, config.MODEL)
    return result
