"""Local LLM signal processing — runs on Ollama (qwen3:8b) at $0.

Handles high-frequency, low-stakes AI work:
- Sentiment scoring from social media text
- Market categorization (better than regex)
- Trade reasoning logs (audit trail)
- Journal pattern detection

These are SIGNAL INPUTS, not trading decisions. The conviction engine
and probability estimator still use Claude for high-stakes reasoning.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging

from agent.llm import generate_json, generate_text, get_provider_status

logger = logging.getLogger(__name__)


def score_sentiment_local(texts: list[str]) -> dict:
    """Score a batch of social media texts for bull/bear sentiment using local LLM.

    Much more accurate than keyword matching. Understands sarcasm, context, nuance.
    Returns: {"bull_score": int, "bear_score": int, "top_bull": str, "top_bear": str}
    """
    if not texts:
        return {"bull_score": 0, "bear_score": 0, "top_bull": "", "top_bear": ""}

    # Batch up to 15 texts to avoid overloading context
    batch = texts[:15]
    combined = "\n---\n".join(f"[{i+1}] {t[:200]}" for i, t in enumerate(batch))

    prompt = f"""Analyze these social media posts about cryptocurrency/Bitcoin for trading sentiment.

{combined}

Return ONLY valid JSON:
{{"bull_score": <number of bullish posts>, "bear_score": <number of bearish posts>, "neutral": <number of neutral>, "top_bull": "<most bullish quote, max 80 chars>", "top_bear": "<most bearish quote, max 80 chars>", "confidence": "high|medium|low"}}

Rules:
- "bullish" = expects price to go UP (buying, accumulating, breakout, moon, recovery)
- "bearish" = expects price to go DOWN (selling, crash, dump, top signal, overvalued)
- "neutral" = informational, no directional opinion
- Sarcasm counts as the OPPOSITE sentiment ("number go up forever" = bearish)
- Ignore posts about unrelated topics"""

    result = generate_json(prompt, max_tokens=600)
    if result:
        return result

    # Fallback to keyword matching if LLM unavailable
    logger.info("Local LLM unavailable — falling back to keyword sentiment")
    return _keyword_sentiment(texts)


def _keyword_sentiment(texts: list[str]) -> dict:
    """Fallback keyword-based sentiment when Ollama is down."""
    bullish = ["bullish", "moon", "rally", "pump", "surge", "breakout", "buy", "long", "higher", "support", "accumulate"]
    bearish = ["bearish", "crash", "dump", "plunge", "sell", "short", "lower", "resistance", "collapse", "fear"]

    bull = 0
    bear = 0
    for t in texts:
        lower = t.lower()
        bull += sum(1 for w in bullish if w in lower)
        bear += sum(1 for w in bearish if w in lower)

    return {"bull_score": bull, "bear_score": bear, "top_bull": "", "top_bear": "", "confidence": "low"}


def categorize_market_local(question: str) -> str:
    """Use local LLM to categorize a market question. Better than regex."""
    status = get_provider_status()
    if not status.get("ollama"):
        return ""  # Let the regex fallback handle it

    prompt = f"""Categorize this prediction market question into ONE category.
Question: "{question}"
Categories: crypto, weather, politics, economics, geopolitics, sports, tech, markets, culture, other
Return ONLY the category name, nothing else."""

    result = generate_text(prompt, max_tokens=20)
    if result:
        category = result.strip().lower().split()[0].rstrip(".,;:")
        valid = {"crypto", "weather", "politics", "economics", "geopolitics", "sports", "tech", "markets", "culture", "other"}
        if category in valid:
            return category

    return ""


def generate_trade_reasoning(proposal: dict) -> str:
    """Generate a human-readable reasoning log for a trade proposal.

    Written by local LLM, saved as audit trail. Explains WHY we're making
    this trade so future evaluation can learn from it.
    """
    prompt = f"""You are a trading agent's reasoning logger. Write a 2-3 sentence explanation for why this trade is being placed.

Trade details:
- Market: {proposal.get('question', 'Unknown')}
- Strategy: {proposal.get('strategy', 'unknown')}
- Side: {proposal.get('side', '?')} {proposal.get('token_hint', '?')} @ {proposal.get('price', 0):.2f}
- Size: ${proposal.get('size_usd', 0):.0f}
- Edge: {proposal.get('edge_pct', 0):.1f}%
- Reason from strategy: {proposal.get('reason', 'None')}

Write a concise explanation a trader would write in their journal. Be specific about what signal triggered this trade."""

    return generate_text(prompt, max_tokens=200) or proposal.get("reason", "No reasoning available")


def analyze_journal_patterns(trades: list) -> dict:
    """Analyze trade history for behavioral patterns using local LLM.

    Spots: revenge trading, overconcentration, time-of-day patterns,
    strategy drift, win/loss streaks.
    """
    if len(trades) < 10:
        return {"patterns": [], "warning": "Not enough trades for pattern analysis"}

    # Summarize recent trades for the LLM
    recent = trades[-30:]
    summary_lines = []
    for t in recent:
        status = "WIN" if t.get("status") == "paper_filled" and t.get("price", 0) > 0.9 else "PENDING"
        summary_lines.append(
            f"{t.get('timestamp', '?')[:16]} | {t.get('strategy', '?')} | "
            f"{t.get('side', '?')} @ {t.get('price', 0):.2f} | ${t.get('cost_usd', 0):.0f} | "
            f"{t.get('market', '?')[:40]} | {status}"
        )

    trades_text = "\n".join(summary_lines)

    prompt = f"""Analyze this trading log for behavioral patterns. Look for:
1. Overconcentration in one category/strategy
2. Position sizing patterns (too large, too small)
3. Time-of-day patterns
4. Repeated trades on the same market
5. Any signs of strategy drift or inconsistency

Trade log (most recent 30):
{trades_text}

Return ONLY valid JSON:
{{"patterns": ["pattern 1 description", "pattern 2"], "risk_flags": ["flag 1"], "suggestion": "one actionable improvement"}}"""

    result = generate_json(prompt, max_tokens=400)
    return result or {"patterns": [], "risk_flags": [], "suggestion": "LLM analysis unavailable"}
