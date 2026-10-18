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


def _load_signal(filename: str, max_age_min: float | None = None) -> dict:
    """Load a processed signal file, returning empty data on failure.

    max_age_min: if set, ignore the file when it is older than that (a crashed
    scanner must never cause yesterday's proposals to be traded again).
    """
    envelope = load_envelope(filename)
    if envelope.get("status") not in ("success", "partial"):
        return {}
    if max_age_min is not None:
        try:
            gen = datetime.fromisoformat(envelope.get("generated_at"))
            age = (datetime.now(gen.tzinfo) - gen).total_seconds() / 60
        except (TypeError, ValueError):
            logger.warning("%s has no valid generated_at — ignoring", filename)
            return {}
        if age > max_age_min:
            logger.warning("%s is %.0f min old (> %s) — ignoring stale signals", filename, age, max_age_min)
            return {}
    return envelope.get("data", {})


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
    """Find heavy favorites the bot's own model still likes after fees.

    On Polymarket US the candidates come from agent/us_scanner.py. The ask is
    not a probability: the scanner's midpoint has to clear the favorite
    threshold on a two-sided book, and this function then requires the model's
    probability to clear the ask plus the taker fee plus US_GIMME_MIN_EDGE.
    Categories without a model (crypto included) are not traded. Proposals are
    ranked by expected value per dollar, not annualized yield.

    ``last_evaluations`` holds one audit row per candidate considered this call,
    including rejects, so the proposal log can record bid, ask, mid, and depth.
    """
    import os

    scan_gimme_bets.last_evaluations = []
    if os.environ.get("POLYMARKET_VENUE", "").strip().lower() == "us":
        proposals, audits = _scan_us_gimme_bets(params)
        scan_gimme_bets.last_evaluations = audits
        return proposals
    return _scan_polygon_gimme_bets(params)


def _scan_us_gimme_bets(params: dict) -> tuple[list, list]:
    """US gimme proposals plus an audit row for every evaluated candidate."""
    from agent.config import AUTO_EXIT_PRICE
    from agent.gimme_rules import (
        DEFAULT_MAX_SPREAD,
        DEFAULT_MIN_EDGE,
        DEFAULT_MIN_HOURS,
        assess_us_gimme,
        env_float,
        model_prob_for,
        normalize_category,
        slug_is_skipped,
    )

    pm = _load_signal("polymarket_us.json", max_age_min=30)
    if not pm:
        return [], []

    min_price = float(params.get("min_price", env_float("US_GIMME_PRICE_THRESHOLD", 0.92)))
    max_days = params.get("max_days_to_expiry", 30)
    min_gross_edge = params.get("min_gross_edge", 0.0)
    min_shares = params.get("min_shares", 5)
    min_depth = max(min_shares, env_float("US_GIMME_MIN_ASK_SHARES", 100))
    max_spread = env_float("US_GIMME_MAX_SPREAD", DEFAULT_MAX_SPREAD)
    min_edge = env_float("US_GIMME_MIN_EDGE", params.get("min_model_edge", DEFAULT_MIN_EDGE))
    min_hours = env_float("US_GIMME_MIN_HOURS", DEFAULT_MIN_HOURS)
    skip_categories = set(params.get("skip_categories", ["sports"]))
    skip_neg_risk = params.get("skip_neg_risk", True)
    skip_grouped = params.get("skip_grouped", True)

    bankroll = params.get("bankroll", 0)
    max_pct = params.get("max_position_pct")
    if bankroll and max_pct:
        max_pos = bankroll * max_pct
    else:
        max_pos = params.get("max_position_usd", 10)

    wx_rows = _weather_us_rows()
    items = pm.get("gimme_evaluated")
    if not items:
        items = pm.get("gimme_bets", [])

    proposals = []
    audits = []
    for g in items:
        slug = g.get("slug", "")
        side = (g.get("side") or "").upper()
        wx = wx_rows.get(slug) or {}
        model_prob, model_reason = model_prob_for(slug, side, wx.get("p_yes"))
        # Book rejects are already decided; don't relabel them as "no model".
        if g.get("book_reject"):
            model_for_assess, model_reason_for_assess = None, g.get("book_reject")
        else:
            model_for_assess, model_reason_for_assess = model_prob, model_reason
        decision = assess_us_gimme(
            g, model_for_assess,
            min_price=min_price, max_spread=max_spread, min_edge=min_edge,
            min_hours=min_hours, min_depth=min_depth, model_reason=model_reason_for_assess,
        )
        reason = decision["reason"]
        # Crypto (and any other skipped slug) stays off even when the book looks
        # like a heavy favorite. An empty US_GIMME_SKIP_SLUG_REGEX disables this;
        # no_model still rejects categories that have no probability model.
        if slug_is_skipped(slug):
            reason = "skipped_slug"
        if reason is None and (
            normalize_category(g.get("category", ""), slug) in skip_categories
            or g.get("category", "other") in skip_categories
        ):
            reason = "skip_category"
        if reason is None and skip_neg_risk and g.get("neg_risk"):
            reason = "neg_risk"
        if reason is None and skip_grouped and (g.get("group_size", 0) > 0 or g.get("group_title", "")):
            reason = "grouped"
        risks = list(g.get("risks") or [])
        if reason is None and "likely_settled" in risks:
            reason = "likely_settled"
        pay = decision["pay_price"] or 0.0
        if reason is None and pay and (AUTO_EXIT_PRICE - pay) < min_gross_edge:
            reason = "auto_exit_gap"
        hours = decision["hours"]
        if reason is None and hours is not None and hours > float(max_days) * 24:
            reason = "too_far"

        category = normalize_category(g.get("category", ""), slug)
        audit = {
            "strategy": "gimme_bets",
            "slug": slug,
            "side": side.lower(),
            "market_bid": decision["bid"],
            "market_ask": decision["ask"],
            "market_mid": decision["mid"],
            "depth_shares": decision["depth"],
            "spread": decision["spread"],
            "model_prob": decision["model_prob"] if g.get("book_reject") is None else model_prob,
            "model_source": model_reason,
            "ev_per_dollar": decision["ev_per_dollar"],
            "hours_to_expiry": hours,
            "category": category,
            "reject_reason": reason,
        }
        if reason is not None:
            audits.append(audit)
            continue

        # Size from the gross quote, same formula as before, so a 5-share
        # floor on a small bankroll does not change. The rank key is EV.
        raw_return = (1.0 - pay) / pay * 100 if pay else 0.0
        size_usd = min(max_pos, max_pos * (raw_return / 10.0))
        shares = size_usd / pay if pay else 0
        if shares < min_shares:
            shares = min_shares
            size_usd = shares * pay
            if size_usd > max_pos:
                audit["reject_reason"] = "size"
                audits.append(audit)
                continue

        edge = decision["edge"] or 0.0
        ev = decision["ev_per_dollar"] or 0.0
        proposal = {
            "strategy": "gimme_bets",
            "venue": "us",
            "question": g.get("question", ""),
            "slug": slug,
            "side": "BUY",
            "token_hint": side.lower(),
            "price": round(pay, 4),
            "market_bid": decision["bid"],
            "market_ask": decision["ask"],
            "market_mid": decision["mid"],
            "spread": decision["spread"],
            "depth_shares": decision["depth"],
            "size_shares": round(shares, 1),
            "size_usd": round(size_usd, 2),
            "edge": round(edge, 4),
            "edge_pct": round(edge * 100, 2),
            "ev_per_dollar": round(ev, 6),
            "model_prob": round(decision["model_prob"], 4),
            "model_source": model_reason,
            "annualized_yield": g.get("annualized_yield_pct"),
            "days_to_expiry": g.get("days_to_expiry"),
            "hours_to_expiry": hours,
            "conviction": round(min(raw_return / 8.0, 1.0), 2),
            "category": category,
            "risks": risks,
            "reason": (
                f"{side} model {decision['model_prob']:.0%} vs ask {pay:.3f} "
                f"(mid {decision['mid']:.3f}, edge {edge:.3f} after taker fee)"
            ),
        }
        for key in (
            "prob_method", "prob_fallback", "ensemble_mean", "ensemble_spread",
            "emos_bias", "emos_spread", "obs_floor", "obs_floor_applied",
            "p_yes", "n_members", "n_models", "ensemble_models",
        ):
            if key in wx and wx[key] is not None and wx[key] != "":
                proposal[key] = wx[key]
        audits.append(audit)
        proposals.append(proposal)

    proposals.sort(key=lambda p: p.get("ev_per_dollar") or -1, reverse=True)
    return proposals[:10], audits


def _scan_polygon_gimme_bets(params: dict) -> list:
    """Legacy polymarket.com gimme list. US venue does not use this path."""
    from agent.config import AUTO_EXIT_PRICE

    pm = _load_signal("polymarket.json")
    if not pm:
        return []

    min_price = params.get("min_price", 0.92)
    max_days = params.get("max_days_to_expiry", 30)
    min_vol = params.get("min_volume_24h", 10000)
    min_gross_edge = params.get("min_gross_edge", 0.0)
    min_shares = params.get("min_shares", 5)
    skip_categories = set(params.get("skip_categories", ["sports"]))
    skip_neg_risk = params.get("skip_neg_risk", True)
    skip_grouped = params.get("skip_grouped", True)

    bankroll = params.get("bankroll", 0)
    max_pct = params.get("max_position_pct")
    if bankroll and max_pct:
        max_pos = bankroll * max_pct
    else:
        max_pos = params.get("max_position_usd", 10)

    wx_view = _weather_us_view()
    proposals = []
    for g in pm.get("gimme_bets", []):
        price = g.get("price", 0)
        if price < min_price:
            continue
        if g.get("venue") == "us":
            if g.get("depth_shares", 0) < max(min_shares, 100):
                continue
        elif g.get("volume_24h", 0) < min_vol:
            continue
        if g.get("days_to_expiry") is not None and g["days_to_expiry"] > max_days:
            continue
        if g.get("category", "other") in skip_categories:
            continue
        if skip_neg_risk and g.get("neg_risk"):
            continue
        if skip_grouped and (g.get("group_size", 0) > 0 or g.get("group_title", "")):
            continue

        if g.get("venue") == "us" and str(g.get("slug", "")).startswith("tc-temp-"):
            view = wx_view.get(g.get("slug"))
            if view is None:
                continue
            side_p = view if g.get("side", "").upper() == "YES" else 1 - view
            if side_p < price + 0.02:
                continue

        risks = g.get("risks", [])
        if "likely_settled" in risks:
            continue

        if (AUTO_EXIT_PRICE - price) < min_gross_edge:
            continue

        profit_per_share = g.get("profit_per_share", 1.0 - price)
        raw_return = g.get("raw_return_pct", 0)

        size_usd = min(max_pos, max_pos * (raw_return / 10.0))
        shares = size_usd / price
        if shares < min_shares:
            shares = min_shares
            size_usd = shares * price
            if size_usd > max_pos:
                continue

        proposals.append({
            "strategy": "gimme_bets",
            "venue": g.get("venue", "polygon"),
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
# STRATEGY 2b: Weather model on Polymarket US temperature markets
# ============================================================

def _weather_us_rows() -> dict:
    """{slug: weather row} from agent/weather_us.py, fresh within 30 minutes.

    ``p_yes`` is the unblended EMOS / ensemble probability, not the market.
    """
    env = _load_signal("weather_us.json", max_age_min=30)
    out = {}
    for r in (env or {}).get("rows", []):
        if r.get("slug"):
            out[r["slug"]] = r
    return out


def _weather_us_view() -> dict:
    """{slug: p_yes} from agent/weather_us.py's latest pricing (fresh within 30 min)."""
    return {slug: row.get("p_yes") for slug, row in _weather_us_rows().items()}


def scan_weather_us(params: dict) -> list:
    """Proposals from the temperature model (agent/weather_us.py).

    Only sides the model rates >= WX_MIN_PROB with edge >= WX_MIN_EDGE after fees
    (and <= WX_MAX_EDGE, the humility cap). Limit price is capped at
    the probability used for the trade, minus WX_MIN_EDGE; the executor rests
    it as a maker bid.

    When WX_ENSEMBLE is on, the ensemble/EMOS probability is blended with the
    market mid in log-odds before that edge test. model_prob on the proposal
    stays the unblended side probability so the proposal log can score the
    model against the market. WX_ENSEMBLE=0 leaves this blend off.
    """
    import os
    from agent.weather_ensemble import ensemble_enabled, reapply_weather_gate
    if os.environ.get("POLYMARKET_VENUE", "").strip().lower() != "us":
        return []
    data = _load_signal("weather_us.json", max_age_min=30)
    if not data:
        return []
    bankroll = params.get("bankroll", 0) or 0
    max_pos = bankroll * params.get("max_position_pct", 0.05) if bankroll else 5
    min_shares = params.get("min_shares", 5)
    blend_on = ensemble_enabled()
    out = []
    for w in data.get("proposals", [])[: params.get("max_proposals", 5) * 3]:
        if blend_on:
            w = reapply_weather_gate(w)
            if not w:
                continue
        price = float(w.get("price") or 0)
        if not 0.05 <= price <= 0.97:
            continue
        shares = max(min_shares, int(max_pos / price))
        cost = shares * price
        if cost > max_pos * 1.05 and shares > min_shares:
            shares = int(max_pos / price)
            cost = shares * price
        if shares < min_shares or cost > max_pos * 1.5:
            continue
        p = w["model_prob"]
        method = w.get("prob_method") or "model"
        item = {
            "strategy": "weather_us",
            "venue": "us",
            "question": w["question"],
            "slug": w["slug"],
            "side": "BUY",
            "token_hint": w["side"].lower(),
            "price": round(price, 3),
            "size_shares": shares,
            "size_usd": round(cost, 2),
            "edge_pct": round(w["edge"] * 100, 1),
            "model_prob": p,
            "conviction": round(min(1.0, 0.5 + w["edge"] * 3), 2),
            "category": "climate",
            "days_to_expiry": w.get("lead", 1),
            "volume_24h": 10 ** 6,  # liquidity checked by depth below
            "risks": [],
            "reason": f"{w['side']} {method} {p:.0%} vs ask {w['ask']:.2f} (fc {w['forecast']}F, lead {w['lead']})",
        }
        for key in (
            "prob_method", "prob_fallback", "ensemble_mean", "ensemble_spread",
            "emos_bias", "emos_spread", "obs_floor", "obs_floor_applied",
            "trade_prob", "p_yes", "n_members", "n_models", "ensemble_models",
            "model_weight", "yes_ask", "yes_bid", "no_ask",
        ):
            if key in w and w[key] is not None and w[key] != "":
                item[key] = w[key]
        out.append(item)
    out.sort(key=lambda x: x["edge_pct"], reverse=True)
    return out[: params.get("max_proposals", 5)]


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
    run_all_strategies.last_gimme_evaluations = []
    all_proposals = []

    # Weather model on Polymarket US (first: it has the best information on temperature brackets)
    wx_params = params.get("weather_us", {})
    wx = scan_weather_us(wx_params)
    for p in wx:
        p["strategy_weight"] = wx_params.get("weight", 1.0)
        p["weighted_conviction"] = round(p.get("conviction", 0) * p["strategy_weight"], 3)
    all_proposals.extend(wx)
    logger.info("Weather US: %d proposals", len(wx))

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
    run_all_strategies.last_gimme_evaluations = list(getattr(scan_gimme_bets, "last_evaluations", []) or [])
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

    # Polymarket US venue: only proposals built from US markets may trade. Legacy
    # polymarket.com strategies (weather_edge, btc_sentiment, probability_arb)
    # carry foreign slugs and would otherwise be fuzzy-matched to the wrong market.
    import os
    if os.environ.get("POLYMARKET_VENUE", "").strip().lower() == "us":
        dropped = [p for p in all_proposals if p.get("venue") != "us"]
        if dropped:
            logger.info("US venue: dropping %d non-US proposal(s) from %s", len(dropped),
                        sorted({p.get("strategy", "?") for p in dropped}))
        all_proposals = [p for p in all_proposals if p.get("venue") == "us"]

    return all_proposals
