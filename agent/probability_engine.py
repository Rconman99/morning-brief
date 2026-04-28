"""AI Probability Engine v2 — dual-model ensemble + Bayesian market integration + Kelly sizing.

The $2.2M strategy upgraded:
1. Two models estimate probability independently (ensemble)
2. Bayesian combination: 70% AI + 30% market price
3. Kelly criterion sizes the bet proportional to edge
4. Only trades when models agree (spread < 15%)
"""
import sys, os, json, logging, re
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.data_envelope import load_envelope

logger = logging.getLogger(__name__)

ESTIMATE_CACHE = PROJECT_ROOT / "agent" / "probability_cache.json"

def _load_cache():
    if ESTIMATE_CACHE.exists():
        try: return json.loads(ESTIMATE_CACHE.read_text())
        except: pass
    return {}

def _save_cache(cache):
    ESTIMATE_CACHE.write_text(json.dumps(cache, indent=2))

def _build_context():
    parts = []
    pm = load_envelope("polymarket.json")
    if pm.get("status") in ("success", "partial"):
        ci = pm.get("data", {}).get("crypto_intelligence", {})
        if ci:
            fng = ci.get("fear_greed", {})
            btc = ci.get("btc_price", {})
            parts.append(f"Fear & Greed: {fng.get('value', '?')} ({fng.get('label', '?')})")
            parts.append(f"BTC: ${btc.get('current', 0):,.0f} (7d: {btc.get('change_7d_pct', 0):+.1f}%)")
    macro = load_envelope("macro_dashboard.json")
    if macro.get("status") in ("success", "partial"):
        for name, data in macro.get("data", {}).get("indicators", {}).items():
            parts.append(f"{name}: {data.get('value', '?')} (trend: {data.get('trend', '?')})")
    return "\n".join(parts) if parts else "No signal data."

def estimate_with_ensemble(question, market_yes_price):
    """Estimate true probability using dual-model ensemble + Bayesian market integration.
    
    Returns: {"probability": float, "confidence": str, "kelly_size": float,
              "edge": float, "trade_signal": bool, ...}
    """
    from datetime import datetime
    cache = _load_cache()
    today = datetime.now().strftime("%Y-%m-%d")
    cache_key = f"{question[:60]}_{today}"
    if cache_key in cache:
        return cache[cache_key]

    context = _build_context()
    prompt = f"""Estimate the TRUE probability (0.00 to 1.00) this event resolves YES.

Question: {question}
Current market price: {market_yes_price:.2f} (YES)

Context:
{context}

Return ONLY valid JSON: {{"probability": 0.XX, "reasoning": "1 sentence"}}
Be calibrated. If unsure, stay close to the market price."""

    try:
        from agent.llm import ensemble_probability
        result = ensemble_probability(prompt, max_tokens=200)
    except Exception as e:
        logger.warning("Ensemble failed: %s", e)
        return {"probability": None, "confidence": "none", "trade_signal": False}
    
    ai_prob = result.get("probability")
    confidence = result.get("confidence", "none")
    spread = result.get("spread", 1.0)
    
    if ai_prob is None:
        return {"probability": None, "confidence": "none", "trade_signal": False}
    
    # Bayesian market integration: 70% AI + 30% market
    AI_W = float(os.getenv("AI_WEIGHT", "0.70"))
    MKT_W = float(os.getenv("MARKET_WEIGHT", "0.30"))
    combined_prob = AI_W * ai_prob + MKT_W * market_yes_price
    
    # Edge calculation
    edge = combined_prob - market_yes_price  # Positive = YES underpriced
    abs_edge = abs(edge)
    
    # Kelly criterion: f = (p*b - (1-p)) / b where b = (1/price - 1) for binary
    if edge > 0:
        # Buy YES
        b = (1.0 / market_yes_price) - 1 if market_yes_price > 0 else 0
        side = "yes"
        price = market_yes_price
    else:
        # Buy NO
        no_price = 1 - market_yes_price
        b = (1.0 / no_price) - 1 if no_price > 0 else 0
        side = "no"
        price = no_price
        combined_prob = 1 - combined_prob  # Flip for NO side
    
    KELLY_FRAC = float(os.getenv("KELLY_FRACTION", "0.15"))
    if b > 0 and combined_prob > 0:
        kelly_full = (combined_prob * b - (1 - combined_prob)) / b
        kelly_size = max(0, kelly_full * KELLY_FRAC)
    else:
        kelly_size = 0
    
    # Trade signal: only trade if edge > 5% AND models agree (spread < 15%)
    trade_signal = abs_edge >= 0.05 and confidence in ("high", "medium") and kelly_size > 0
    
    output = {
        "probability": round(combined_prob, 3),
        "ai_raw": round(ai_prob, 3),
        "market_price": round(market_yes_price, 3),
        "combined": round(AI_W * ai_prob + MKT_W * market_yes_price, 3),
        "edge": round(edge, 3),
        "abs_edge": round(abs_edge, 3),
        "kelly_size": round(kelly_size, 4),
        "confidence": confidence,
        "model_a": result.get("model_a"),
        "model_b": result.get("model_b"),
        "spread": round(spread, 3),
        "side": side,
        "price": round(price, 4),
        "trade_signal": trade_signal,
        "reasoning": result.get("reasoning", ""),
    }
    
    cache[cache_key] = output
    _save_cache(cache)
    
    logger.info("Ensemble prob: AI=%.2f Mkt=%.2f Combined=%.2f Edge=%.1f%% Kelly=%.3f Conf=%s Signal=%s",
                ai_prob, market_yes_price, combined_prob, abs_edge*100, kelly_size, confidence, trade_signal)
    
    return output


def scan_probability_arbitrage(markets, min_gap=0.05):
    """Scan markets for probability arbitrage using dual-model ensemble."""
    if not os.environ.get("OPENROUTER_API_KEY"):
        return []
    
    proposals = []
    for m in markets[:10]:  # Limit API calls
        question = m.get("question", "")
        yes_price = m.get("yes_price", 0)
        if not question or yes_price <= 0.10 or yes_price >= 0.90:
            continue
        
        est = estimate_with_ensemble(question, yes_price)
        if not est.get("trade_signal"):
            continue
        
        # Kelly-based sizing (fraction of bankroll)
        kelly = est["kelly_size"]
        # Cap at config max
        max_pct = float(os.getenv("KELLY_MAX_BET_PCT", "0.10"))
        kelly = min(kelly, max_pct)
        
        proposals.append({
            "strategy": "probability_arb",
            "question": question,
            "slug": m.get("slug", ""),
            "side": "BUY",
            "token_hint": est["side"],
            "price": est["price"],
            "kelly_fraction": kelly,
            "edge_pct": round(est["abs_edge"] * 100, 1),
            "confidence": est["confidence"],
            "conviction": round(kelly * 10, 2),  # Normalize for display
            "category": m.get("category", "other"),
            "ai_probability": est["ai_raw"],
            "market_price": est["market_price"],
            "combined_probability": est["combined"],
            "model_spread": est["spread"],
            "reasoning": est["reasoning"],
            "reason": f"Ensemble: AI {est['ai_raw']:.0%} vs Mkt {est['market_price']:.0%} = {est['abs_edge']*100:.1f}% edge ({est['confidence']}, Kelly {kelly:.1%})",
        })
    
    proposals.sort(key=lambda x: x.get("edge_pct", 0), reverse=True)
    return proposals[:5]
