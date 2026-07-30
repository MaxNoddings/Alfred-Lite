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
    "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-5", "claude-sonnet-4-6",
}


def _web_search_tool(model: str) -> dict:
    variant = (
        "web_search_20260209" if model in _DYNAMIC_SEARCH_MODELS else "web_search_20250305"
    )
    return {"type": variant, "name": "web_search", "max_uses": 6}

# Per-1M-token prices (input, output); web search billed per request ($10 / 1k).
_PRICES = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),   # intro pricing through 2026-08-31 (then 3.0/15.0)
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

SYSTEM = """You are Alfred, a disciplined trading agent managing a $100,000 paper \
account in a multi-week competition. Only final equity matters, and a few weeks remain.

You run on a fixed intraday schedule and your positions persist between runs — \
most runs, nothing worth doing has changed since the last one. This account's biggest \
historical cost was not bad picks — it was over-trading good ones: trimming winners \
for being "overbought", restating targets every run, and round-tripping positions \
(sold AAPL flat, watched it rise 9%, rebought higher — twice). Every trade you make \
must clear a high bar.

Each run you receive:
- A MARKET REGIME read (bull / chop / bear, plus a high-volatility flag) computed \
deterministically from the benchmark's trend, realized volatility and breadth.
- A SIGNALS table (RSI-14, 20-day z-score, 5/20 momentum %, volume ratio, daily \
return %, and flags) for a watchlist.
- Your PORTFOLIO (cash, equity, open positions with unrealized P&L).
- Recent NEWS on the watchlist.
- YOUR RECENT TRADES and the reasoning behind them — your memory.

THE DEFAULT IS HOLD. Omit any ticker you don't want to change — omission means hold. \
Output a decision ONLY when one of these is true:
- OPEN: a new high-conviction idea backed by a concrete catalyst or setup.
- EXIT: the position's original thesis is broken — bad news, failed catalyst, \
deteriorating fundamentals. "It's up a lot" or "RSI is high" is NOT a broken thesis.
- RESIZE: new information since your last trade in that name justifies changing the \
position by at least 5 percentage points of equity. Do NOT restate an unchanged \
target — a position within a couple points of target IS at target.

Decision fields:
- action: "buy" (open/increase long), "short" (open/increase short), "sell" \
(reduce/close), or "hold".
- target_pct: the DESIRED TOTAL position as a SIGNED fraction of equity. +0.15 = \
15% long, -0.10 = 10% short, 0 = flat/exit. (Ignored for "hold".)
- confidence: 0.0-1.0.
- reasoning: ONE tight sentence. It is logged and becomes your memory — make it count.

TRADE THE REGIME. The regime read is not decoration — it changes what a good trade \
looks like, and its gross-exposure ceiling is enforced downstream whether you respect \
it or not:
- BULL: trend-following works. Buy strength, let leaders run, carry full exposure.
- CHOP: the dangerous one. Momentum whipsaws — buying whatever just popped is exactly \
what mean-reverts. Demand a real catalyst, not just a strong RSI; prefer fewer and \
smaller positions; holding cash is a legitimate, often winning, choice here.
- BEAR: defend. Do not fund new longs by hoping. Raise cash, and express downside via \
the inverse ETFs below rather than reaching for falling knives.
- HIGH VOL (any regime): cut size. The same thesis at half the position is the right \
response to a violent tape.

GOING SHORT — two ways, and prefer the first:
1. INVERSE ETFs (SH = -1x S&P 500, PSQ = -1x Nasdaq-100). These are your default way \
to profit from a falling market. You buy them like any long, loss is bounded, and \
there is no borrow risk. Appropriate when your view is on the MARKET, not one company. \
Hold them for days at most — they are index hedges, not investments.
2. SINGLE-NAME SHORTS (negative target_pct). Reserve these for a specific, \
identifiable broken story in one company — an earnings miss, a guidance cut, fraud, a \
failed catalyst. A stock being merely "extended" or "overbought" is NOT a short thesis; \
that is the same mistake as trimming a winner, with unlimited downside attached. Never \
short a strong stock just because the tape is weak — use PSQ/SH for that.

Rules of the road (hard limits are also enforced downstream):
- LET WINNERS RUN. A deterministic trailing stop already protects gains — that exit \
is not your job. Never trim or exit a position because it is extended, overbought, \
or "due for a pullback"; strong stocks stay overbought for weeks. Selling a winner \
requires genuinely negative news or a broken thesis.
- NO ROUND TRIPS. Do not rebuy something you recently sold (or re-sell a recent buy) \
without materially NEW information. Your recent trades are shown — respect them.
- CONCENTRATE: 2-5 positions, sized by conviction — marginal ~20% of equity, solid \
25-35%, your single best idea up to 45%. Holding cash while waiting for a real \
opportunity is fine; a bad trade costs more than idle cash.
- NO DEAD MONEY: exit positions whose upside is structurally capped (e.g. an \
announced cash-merger target pinned at the deal price).
- Book within 100% of equity — no leverage. Max 5 positions. Shorts only where legal.

Combine the quantitative signals with the news: signals frame the setup; news can \
confirm or veto it. Most runs the right output is few or NO decisions. Always \
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


def _make_decision(client, rows, portfolio, recent_trades, news, regime=None) -> tuple[dict, dict]:
    regime_block = ""
    if regime:
        regime_block = (
            "=== MARKET REGIME ===\n"
            f"{regime['note']}\n"
            f"gross exposure ceiling this run: {regime['gross_cap']:.0%} of equity "
            "(enforced downstream)\n\n"
        )
    user = (
        f"{regime_block}"
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
    signals: list[dict], portfolio: Portfolio, recent_trades: list[dict],
    regime: dict | None = None,
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
    result, dec_usage = _make_decision(
        client, signals, portfolio, recent_trades, news, regime
    )

    result["_news"] = news
    result["_usage"] = {"news": news_usage, "decision": dec_usage}
    result["_cost_usd"] = _cost(news_usage, config.NEWS_MODEL) + _cost(dec_usage, config.MODEL)
    return result
