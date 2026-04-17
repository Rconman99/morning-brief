"""Three strategy families for Polymarket trading.

Each strategy reads from existing morning-brief signal modules,
scores opportunities by conviction, and returns trade proposals
for the risk gate to approve/reject.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
import re
from datetime import datetime, date

from lib.data_envelope import load_envelope

logger = logging.getLogger(__name__)

# Local LLM for sentiment scoring (Ollama, $0)
try:
    from agent.local_signals import score_sentiment_local
    _has_local_llm = True
except ImportError:
    _has_local_llm = False


def _load_signal(filename: str) -> dict:
    """Load a processed signal file, returning empty data on failure."""
    envelope = load_envelope(filename)
    if envelope.get("status") in ("success", "partial"):
        return envelope.get("data", {})
    return {}


# ============================================================
# STRATEGY 1: Weather Edge
# ============================================================

def scan_weather_opportunities(params: dict) -> list:
    """Find weather markets where forecast disagrees with market price.

    Uses the existing polymarket_scanner's weather analysis which already
    cross-references Open-Meteo forecasts against Polymarket prices.
    """
    pm = _load_signal("polymarket.json")
    if not pm:
        return []

    min_edge = params.get("min_edge_pct", 0.15)
    max_pos = params.get("max_position_usd", 200)
    confidence_floor = params.get("forecast_confidence_floor", 0.65)

    proposals = []
    for wx in pm.get("weather_analysis", []):
        weather = wx.get("weather", {})
        if not weather or weather.get("estimated_probability") is None:
            continue

        edge = abs(weather.get("edge", 0))
        est_prob = weather.get("estimated_probability", 0)

        if edge < min_edge or est_prob < confidence_floor:
            continue

        # Determine side
        if weather.get("edge", 0) > 0:
            side = "BUY"
            price = wx.get("yes_price", 0)
            token_hint = "yes"
        else:
            side = "BUY"
            price = wx.get("no_price", 0)
            token_hint = "no"

        if price <= 0.02 or price >= 0.95:
            continue  # Skip near-zero or near-certain prices — won't fill

        # Size: proportional to edge, capped at max
        conviction = min(edge / 0.30, 1.0)  # Normalize: 30% edge = max conviction
        size_usd = max_pos * conviction
        shares = size_usd / price

        proposals.append({
            "strategy": "weather_edge",
            "market_id": wx.get("id", ""),
            "question": wx.get("question", ""),
            "slug": wx.get("slug", ""),
            "side": side,
            "token_hint": token_hint,
            "price": round(price, 4),
            "size_shares": round(shares, 1),
            "size_usd": round(size_usd, 2),
            "edge_pct": round(edge * 100, 1),
            "estimated_prob": round(est_prob, 3),
            "conviction": round(conviction, 2),
            "category": "weather",
            "reason": f"Forecast {est_prob:.0%} vs market {price:.0%} = {edge*100:.1f}% edge ({weather.get('city', '?')})",
        })

    proposals.sort(key=lambda x: x["edge_pct"], reverse=True)
    return proposals


# ============================================================
# STRATEGY 2: Gimme Bets (near-certain plays)
# ============================================================

def scan_gimme_bets(params: dict) -> list:
    """Find markets at 92%+ probability with decent volume resolving soon.

    Uses the existing polymarket_scanner's gimme_bets detection.
    """
    pm = _load_signal("polymarket.json")
    if not pm:
        return []

    min_price = params.get("min_price", 0.92)
    max_days = params.get("max_days_to_expiry", 30)
    min_vol = params.get("min_volume_24h", 10000)

    # Bankroll-aware sizing: prefer pct of bankroll, fall back to flat USD cap
    bankroll = params.get("bankroll", 0)
    max_pct = params.get("max_position_pct")
    if bankroll and max_pct:
        max_pos = bankroll * max_pct
    else:
        max_pos = params.get("max_position_usd", 10)

    proposals = []
    for g in pm.get("gimme_bets", []):
        price = g.get("price", 0)
        if price < min_price:
            continue
        if g.get("volume_24h", 0) < min_vol:
            continue
        if g.get("days_to_expiry") is not None and g["days_to_expiry"] > max_days:
            continue

        # Skip if risks are too high
        risks = g.get("risks", [])
        if "likely_settled" in risks:
            continue  # Already resolved, no edge

        profit_per_share = g.get("profit_per_share", 1.0 - price)
        raw_return = g.get("raw_return_pct", 0)

        # Size: fixed per gimme bet, smaller since edge is small
        size_usd = min(max_pos, max_pos * (raw_return / 10.0))
        shares = size_usd / price

        proposals.append({
            "strategy": "gimme_bets",
            "question": g.get("question", ""),
            "slug": g.get("slug", ""),
            "side": "BUY",
            "token_hint": g.get("side", "").lower(),
            "price": round(price, 4),
            "size_shares": round(shares, 1),
            "size_usd": round(size_usd, 2),
            "edge_pct": round(raw_return, 1),
            "annualized_yield": g.get("annualized_yield_pct"),
            "days_to_expiry": g.get("days_to_expiry"),
            "conviction": round(min(raw_return / 8.0, 1.0), 2),
            "category": g.get("category", "other"),
            "risks": risks,
            "reason": f"{g.get('side', '?')} @ {price:.0%}, {raw_return:.1f}% return in {g.get('days_to_expiry', '?')}d",
        })

    proposals.sort(key=lambda x: x.get("annualized_yield") or 0, reverse=True)
    return proposals[:10]


# ============================================================
# STRATEGY 3: BTC Sentiment-Driven
# ============================================================

def scan_btc_sentiment(params: dict) -> list:
    """Score BTC markets using aggregated intelligence signals.

    Combines: Fear & Greed, funding rate, social sentiment, macro indicators,
    and Polymarket odds into a conviction score (-10 to +10).
    """
    pm = _load_signal("polymarket.json")
    if not pm:
        return []

    crypto_intel = pm.get("crypto_intelligence", {})
    portfolio = pm.get("portfolio", {})
    contradictions = pm.get("contradiction_alerts", [])
    social = _load_signal("social_intelligence.json")

    conviction_threshold = params.get("conviction_threshold", 3)
    max_pos = params.get("max_position_usd", 1000)
    fg_low = params.get("fear_greed_extreme_low", 20)
    fg_high = params.get("fear_greed_extreme_high", 80)
    funding_extreme = params.get("funding_rate_extreme", 0.01)

    # --- Build conviction score ---
    bull_score = 0
    bear_score = 0
    reasons = []

    # Fear & Greed
    fng = crypto_intel.get("fear_greed", {})
    fng_val = fng.get("value", 50)
    if fng_val <= fg_low:
        bull_score += 3  # Extreme fear = strong contrarian buy
        reasons.append(f"F&G {fng_val} (extreme fear → contrarian bullish)")
    elif fng_val <= 35:
        bull_score += 1
        reasons.append(f"F&G {fng_val} (fear)")
    elif fng_val >= fg_high:
        bear_score += 3
        reasons.append(f"F&G {fng_val} (extreme greed → contrarian bearish)")
    elif fng_val >= 65:
        bear_score += 1
        reasons.append(f"F&G {fng_val} (greed)")

    # Funding rate
    funding = crypto_intel.get("funding_rate", {})
    fund_rate = funding.get("current", 0)
    if fund_rate > funding_extreme * 100:  # Already in percentage
        bear_score += 2
        reasons.append(f"Funding {fund_rate:.4f}% (overleveraged longs)")
    elif fund_rate < -funding_extreme * 100:
        bull_score += 2
        reasons.append(f"Funding {fund_rate:.4f}% (overleveraged shorts)")

    # BTC momentum
    btc = crypto_intel.get("btc_price", {})
    change_7d = btc.get("change_7d_pct", 0)
    if change_7d > 5:
        bull_score += 1
        reasons.append(f"BTC +{change_7d:.1f}% 7d momentum")
    elif change_7d < -5:
        bear_score += 1
        reasons.append(f"BTC {change_7d:.1f}% 7d momentum")

    # Social sentiment — use local LLM if available for better scoring
    if social:
        for topic in social.get("topics", []):
            if "bitcoin" not in topic.get("topic", "").lower():
                continue

            # X velocity — if X is buzzing, amplify the social signal
            x_velocity = topic.get("x_velocity", "silent")
            if x_velocity == "high":
                bull_score += 1  # High X volume = something is happening
                reasons.append("X velocity HIGH — elevated crypto chatter")

            # Collect actual text snippets from social data for LLM scoring
            # Include X takes (fastest signal) alongside Reddit takes
            snippets = [item.get("snippet", "") or item.get("title", "")
                        for item in topic.get("top_items", []) if item.get("snippet") or item.get("title")]
            reddit_takes = [t.get("text", "") for t in topic.get("reddit_takes", [])]
            x_takes = [t.get("text", "") for t in topic.get("x_takes", [])]
            all_texts = x_takes + snippets + reddit_takes  # X first — fastest signal

            if _has_local_llm and all_texts:
                # Local Ollama scores sentiment from actual text ($0)
                local_sent = score_sentiment_local(all_texts)
                local_bull = local_sent.get("bull_score", 0)
                local_bear = local_sent.get("bear_score", 0)
                if local_bull > local_bear + 2:
                    bull_score += 2
                    top = local_sent.get("top_bull", "")
                    reasons.append(f"Social sentiment: bullish ({local_bull}b/{local_bear}b via Ollama)" + (f" — \"{top[:50]}\"" if top else ""))
                elif local_bear > local_bull + 2:
                    bear_score += 2
                    top = local_sent.get("top_bear", "")
                    reasons.append(f"Social sentiment: bearish ({local_bull}b/{local_bear}b via Ollama)" + (f" — \"{top[:50]}\"" if top else ""))
                elif local_bull > local_bear:
                    bull_score += 1
                    reasons.append(f"Social sentiment: slightly bullish ({local_bull}b/{local_bear}b)")
                elif local_bear > local_bull:
                    bear_score += 1
                    reasons.append(f"Social sentiment: slightly bearish ({local_bull}b/{local_bear}b)")
            else:
                # Fallback to basic keyword check
                sent = topic.get("sentiment", "neutral")
                if sent == "bullish":
                    bull_score += 1
                    reasons.append("Social sentiment: bullish")
                elif sent == "bearish":
                    bear_score += 1
                    reasons.append("Social sentiment: bearish")

    # Macro (VIX, DXY)
    macro = _load_signal("macro_dashboard.json")
    if macro:
        indicators = macro.get("indicators", {})
        vix = indicators.get("VIX", {})
        if (vix.get("value") or 0) > 25:
            bear_score += 1
            reasons.append(f"VIX {vix['value']:.0f} (risk-off)")
        dxy = indicators.get("DXY", {})
        if (dxy.get("change_5d") or 0) > 1:
            bear_score += 1
            reasons.append(f"DXY strengthening (+{dxy['change_5d']:.1f}%)")
        elif (dxy.get("change_5d") or 0) < -1:
            bull_score += 1
            reasons.append(f"DXY weakening ({dxy['change_5d']:.1f}%)")

    # Net conviction: positive = bullish, negative = bearish
    conviction = bull_score - bear_score
    direction = "bullish" if conviction > 0 else "bearish" if conviction < 0 else "neutral"

    if abs(conviction) < conviction_threshold:
        logger.info("BTC conviction %d (%s) — below threshold %d, no trades",
                     conviction, direction, conviction_threshold)
        return []

    # --- Find tradeable BTC markets ---
    proposals = []
    all_markets = []

    # Check opportunities
    for opp in pm.get("opportunities", []):
        if opp.get("category") == "crypto":
            all_markets.append(opp)

    # Check top volume crypto markets
    for m in pm.get("top_volume", []):
        if m.get("category") == "crypto":
            all_markets.append(m)

    # Also check gimme bets for crypto
    for g in pm.get("gimme_bets", []):
        if g.get("category") == "crypto":
            all_markets.append(g)

    for m in all_markets:
        question = m.get("question", "").lower()
        yes_price = m.get("yes_price", 0) or 0
        no_price = m.get("no_price", 0) or 0

        # Determine what side aligns with our conviction
        if direction == "bullish":
            # Look for "BTC above X" YES or "BTC dip to X" NO
            if "above" in question or "reach" in question:
                side, price, token = "BUY", yes_price, "yes"
            elif "dip" in question or "below" in question:
                side, price, token = "BUY", no_price, "no"
            else:
                continue
        else:  # bearish
            if "dip" in question or "below" in question:
                side, price, token = "BUY", yes_price, "yes"
            elif "above" in question or "reach" in question:
                side, price, token = "BUY", no_price, "no"
            else:
                continue

        if price <= 0.05 or price >= 0.95:
            continue  # Skip extreme prices — no room for edge or won't fill

        # Skip if no slug — can't resolve token for live trading
        if not m.get("slug"):
            continue

        # Size proportional to conviction strength
        conviction_pct = min(abs(conviction) / 8.0, 1.0)
        size_usd = max_pos * conviction_pct
        shares = size_usd / price

        proposals.append({
            "strategy": "btc_sentiment",
            "question": m.get("question", ""),
            "slug": m.get("slug", ""),
            "side": side,
            "token_hint": token,
            "price": round(price, 4),
            "size_shares": round(shares, 1),
            "size_usd": round(size_usd, 2),
            "conviction": conviction,
            "conviction_pct": round(conviction_pct, 2),
            "direction": direction,
            "bull_score": bull_score,
            "bear_score": bear_score,
            "category": "crypto",
            "reasons": reasons,
            "reason": f"BTC {direction} (conviction {conviction}: {bull_score} bull / {bear_score} bear) — {'; '.join(reasons[:3])}",
        })

    proposals.sort(key=lambda x: abs(x.get("conviction", 0)), reverse=True)
    return proposals[:5]


# ============================================================
# MAIN: Run all strategies
# ============================================================

def run_all_strategies(params: dict) -> list:
    """Run all three strategy families and return combined proposals."""
    all_proposals = []

    # Weather Edge
    weather_params = params.get("weather_edge", {})
    weather = scan_weather_opportunities(weather_params)
    weight = weather_params.get("weight", 1.0)
    for p in weather:
        p["strategy_weight"] = weight
        p["weighted_conviction"] = round(p.get("conviction", 0) * weight, 3)
    all_proposals.extend(weather)
    logger.info("Weather Edge: %d proposals", len(weather))

    # Gimme Bets
    gimme_params = params.get("gimme_bets", {})
    gimmes = scan_gimme_bets(gimme_params)
    weight = gimme_params.get("weight", 1.0)
    for p in gimmes:
        p["strategy_weight"] = weight
        p["weighted_conviction"] = round(p.get("conviction", 0) * weight, 3)
    all_proposals.extend(gimmes)
    logger.info("Gimme Bets: %d proposals", len(gimmes))

    # BTC Sentiment
    btc_params = params.get("btc_sentiment", {})
    btc = scan_btc_sentiment(btc_params)
    weight = btc_params.get("weight", 1.0)
    for p in btc:
        p["strategy_weight"] = weight
        norm_conv = min(abs(p.get("conviction", 0)) / 8.0, 1.0)
        p["weighted_conviction"] = round(norm_conv * weight, 3)
    all_proposals.extend(btc)
    logger.info("BTC Sentiment: %d proposals (conviction %s)",
                len(btc), btc[0]["conviction"] if btc else "N/A")

    # Phase 2: AI Probability Arbitrage (requires ANTHROPIC_API_KEY)
    try:
        from agent.probability_engine import scan_probability_arbitrage
        # Feed it the top volume non-extreme markets
        pm = _load_signal("polymarket.json")
        candidate_markets = [
            m for m in (pm or {}).get("top_volume", [])
            if 0.10 < (m.get("yes_price") or 0) < 0.90
        ]
        prob_arb = scan_probability_arbitrage(candidate_markets)
        for p in prob_arb:
            p["strategy_weight"] = 1.0
            p["weighted_conviction"] = p.get("conviction", 0)
        all_proposals.extend(prob_arb)
        logger.info("Probability Arb: %d proposals", len(prob_arb))
    except Exception as e:
        logger.debug("Probability engine skipped: %s", e)

    # Phase 3: On-chain flow signals — boost conviction for whale-backed markets
    try:
        from agent.onchain_flow import get_flow_signals
        flow = get_flow_signals()
        whale_slugs = {w.get("slug") for w in flow.get("whale_markets", []) if w.get("slug")}
        informed_slugs = {i.get("slug") for i in flow.get("informed_flow", []) if i.get("slug")}

        for p in all_proposals:
            slug = p.get("slug", "")
            if slug in whale_slugs:
                p["conviction"] = p.get("conviction", 0) + 1
                p["onchain_signal"] = "whale_activity"
                logger.debug("Whale boost: %s +1 conviction", slug)
            if slug in informed_slugs:
                p["conviction"] = p.get("conviction", 0) + 1
                p["onchain_signal"] = "informed_flow"
                logger.debug("Informed flow boost: %s +1 conviction", slug)
    except Exception as e:
        logger.debug("On-chain flow skipped: %s", e)

    # Phase 4: Microstructure filter — longshot bias, category edge, time-of-day
    try:
        from agent.market_microstructure import filter_proposals_microstructure
        pre_count = len(all_proposals)
        all_proposals = filter_proposals_microstructure(all_proposals)
        if pre_count != len(all_proposals):
            logger.info("Microstructure filter: %d → %d proposals",
                        pre_count, len(all_proposals))
    except Exception as e:
        logger.debug("Microstructure filter skipped: %s", e)

    return all_proposals
