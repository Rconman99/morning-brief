"""AI Probability Engine — estimates true probabilities using Claude.

The $2.2M strategy: use AI to estimate true event probabilities,
compare against Polymarket prices, and trade the gap.

Example: Claude estimates 68% chance of Fed rate cut. Market prices it at 54%.
That's a 14-point tradeable gap.

Uses the Claude API (ANTHROPIC_API_KEY from .env).
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
import os
from datetime import datetime

from lib.data_envelope import load_envelope

logger = logging.getLogger(__name__)

ESTIMATE_CACHE = PROJECT_ROOT / "agent" / "probability_cache.json"


def _load_cache() -> dict:
    if ESTIMATE_CACHE.exists():
        try:
            return json.loads(ESTIMATE_CACHE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_cache(cache: dict) -> None:
    ESTIMATE_CACHE.write_text(json.dumps(cache, indent=2))


def estimate_probability(question: str, context: str = "") -> dict:
    """Use Claude to estimate the true probability of a Polymarket question.

    Returns: {"probability": float, "confidence": str, "reasoning": str}
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"probability": None, "confidence": "none", "reasoning": "No ANTHROPIC_API_KEY"}

    # Check cache (key by question + date)
    cache = _load_cache()
    today = datetime.now().strftime("%Y-%m-%d")
    cache_key = f"{question[:80]}_{today}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        # Build context from existing signals
        signal_context = _build_signal_context()

        prompt = f"""You are a probability estimation engine for prediction markets.
Estimate the TRUE probability (0.00 to 1.00) that the following event will resolve YES.

Question: {question}

Available context:
{signal_context}

{f"Additional context: {context}" if context else ""}

Respond with ONLY valid JSON:
{{"probability": 0.XX, "confidence": "high|medium|low", "reasoning": "1-2 sentence explanation"}}

Rules:
- Base your estimate on the evidence available, not gut feeling
- If you don't have enough information, set confidence to "low"
- Be calibrated: if you say 70%, that event should happen ~70% of the time
- Consider base rates and reference classes
- Factor in current market conditions from the signal context"""

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()
        # Parse JSON from response
        if text.startswith("{"):
            result = json.loads(text)
        else:
            # Try to extract JSON from response
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                result = json.loads(text[json_start:json_end])
            else:
                result = {"probability": None, "confidence": "none", "reasoning": "Failed to parse response"}

        # Cache result
        cache[cache_key] = result
        _save_cache(cache)

        logger.info("Probability estimate for '%s': %.2f (%s) — %s",
                     question[:50], result.get("probability", 0),
                     result.get("confidence", "?"), result.get("reasoning", "")[:80])

        return result

    except Exception as e:
        logger.warning("Probability estimation failed: %s", e)
        return {"probability": None, "confidence": "none", "reasoning": str(e)}


def _build_signal_context() -> str:
    """Build context string from existing morning-brief signals."""
    parts = []

    # Crypto intelligence
    pm = load_envelope("polymarket.json")
    if pm.get("status") in ("success", "partial"):
        ci = pm.get("data", {}).get("crypto_intelligence", {})
        if ci:
            fng = ci.get("fear_greed", {})
            btc = ci.get("btc_price", {})
            funding = ci.get("funding_rate", {})
            parts.append(f"Fear & Greed Index: {fng.get('value', '?')} ({fng.get('label', '?')})")
            parts.append(f"BTC: ${btc.get('current', 0):,.0f} (7d: {btc.get('change_7d_pct', 0):+.1f}%, 30d: {btc.get('change_30d_pct', 0):+.1f}%)")
            parts.append(f"Funding rate: {funding.get('current', 0):.4f}% ({funding.get('signal', 'neutral')})")

    # Macro
    macro = load_envelope("macro_dashboard.json")
    if macro.get("status") in ("success", "partial"):
        indicators = macro.get("data", {}).get("indicators", {})
        for name, data in indicators.items():
            parts.append(f"{name}: {data.get('value', '?')} (trend: {data.get('trend', '?')}, 5d: {data.get('change_5d', '?')}%)")

    # Social sentiment
    social = load_envelope("social_intelligence.json")
    if social.get("status") in ("success", "partial"):
        mood = social.get("data", {}).get("overall_mood", "?")
        parts.append(f"Social sentiment: {mood}")

    return "\n".join(parts) if parts else "No signal data available."


def scan_probability_arbitrage(markets: list, min_gap: float = 0.10) -> list:
    """Scan markets for probability arbitrage opportunities.

    For each market, estimate true probability via Claude and compare
    against market price. Trade when gap exceeds min_gap.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.info("Probability engine: no ANTHROPIC_API_KEY — skipping")
        return []

    proposals = []

    for m in markets[:15]:  # Limit API calls
        question = m.get("question", "")
        if not question:
            continue

        yes_price = m.get("yes_price", 0)
        no_price = m.get("no_price", 0)
        if yes_price <= 0.05 or yes_price >= 0.95:
            continue  # Skip extreme prices — no room for edge

        # Estimate true probability
        estimate = estimate_probability(question)
        est_prob = estimate.get("probability")
        confidence = estimate.get("confidence", "low")

        if est_prob is None or confidence == "none":
            continue

        # Calculate gap
        gap = est_prob - yes_price

        if abs(gap) < min_gap:
            continue

        # Determine trade direction
        if gap > 0:
            # Market underprices YES → buy YES
            side = "BUY"
            token_hint = "yes"
            price = yes_price
            edge = gap
        else:
            # Market overprices YES → buy NO
            side = "BUY"
            token_hint = "no"
            price = no_price
            edge = abs(gap)

        # Size based on confidence and edge
        confidence_mult = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(confidence, 0.3)
        edge_mult = min(edge / 0.20, 1.0)  # 20% edge = max
        conviction = round(confidence_mult * edge_mult, 2)

        proposals.append({
            "strategy": "probability_arb",
            "question": question,
            "slug": m.get("slug", ""),
            "side": side,
            "token_hint": token_hint,
            "price": round(price, 4),
            "estimated_probability": round(est_prob, 3),
            "market_price": round(yes_price, 3),
            "gap_pct": round(edge * 100, 1),
            "confidence": confidence,
            "conviction": conviction,
            "category": m.get("category", "other"),
            "reasoning": estimate.get("reasoning", ""),
            "reason": f"Claude estimates {est_prob:.0%} vs market {yes_price:.0%} = {edge*100:.1f}% gap ({confidence})",
        })

    proposals.sort(key=lambda x: x.get("gap_pct", 0), reverse=True)
    return proposals[:5]
