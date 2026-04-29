"""Polymarket Scanner: scans prediction markets for daily opportunities.

Data sources:
1. Gamma API (free, no auth needed) — market prices, volume, liquidity
2. NOAA/Open-Meteo weather APIs (free) — forecast cross-reference for weather markets
3. Existing technical signals — cross-reference crypto/market predictions
4. Sample data fallback

This module is READ-ONLY — it scans and recommends, never places trades.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
import re
from datetime import datetime, date, timedelta
from collections import defaultdict

from lib.data_envelope import create_envelope, save_envelope, load_envelope
from lib.cache import get_cached, set_cached

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:
    requests = None
    logger.warning("requests not installed — API fetching disabled")

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
WEATHER_API = "https://api.open-meteo.com/v1/forecast"
NOAA_API = "https://api.weather.gov"

# Category detection patterns
CATEGORY_PATTERNS = {
    "crypto": [r"bitcoin", r"\bbtc\b", r"ethereum", r"\beth\b", r"crypto", r"solana", r"\bsol\b", r"dogecoin"],
    "weather": [r"temperature", r"rain", r"snow", r"weather", r"°[fc]", r"inches of rain", r"wind speed", r"heat wave", r"cold"],
    "politics": [r"president", r"congress", r"senate", r"election", r"executive order", r"trump", r"biden", r"democrat", r"republican", r"governor", r"nomination"],
    "economics": [r"\bfed\b", r"interest rate", r"inflation", r"gdp", r"recession", r"unemployment", r"cpi\b", r"federal reserve", r"rate cut", r"rate hike", r"bps"],
    "geopolitics": [r"iran", r"china", r"russia", r"ukraine", r"war", r"strait", r"sanctions", r"nato", r"ceasefire", r"invasion", r"regime"],
    "sports": [r"nba", r"nfl", r"mlb", r"nhl", r"world cup", r"super bowl", r"championship", r"playoffs", r"finals", r"la liga", r"premier league", r"win the",
                r"\bvs\.?\b", r"march madness", r"ncaa", r"bracket", r"sweet sixteen", r"elite eight", r"final four",
                r"\bufc\b", r"fight night", r"\bbout\b", r"score", r"match"],
    "tech": [r"\bai\b", r"agi\b", r"openai", r"google", r"apple", r"microsoft", r"launch", r"ship", r"artificial intelligence", r"gpt"],
    "markets": [r"s&p", r"nasdaq", r"dow jones", r"all-time high", r"stock market", r"spy\b", r"qqq\b"],
    "culture": [r"elon", r"tweet", r"musk", r"celebrity", r"social media", r"tiktok", r"viral"],
}

# Minimum thresholds for opportunity flagging
MIN_VOLUME_24H = 5000       # $5K daily volume minimum
MIN_LIQUIDITY = 2000        # $2K liquidity minimum
MIN_EDGE_PCT = 0.10         # 10% expected value minimum
BIG_MOVE_THRESHOLD = 0.03   # 3% price change = "big move" (real movers are 1-3%)

# Gimme bet thresholds (near-guaranteed plays)
GIMME_BET_PRICE_THRESHOLD = 0.92  # YES or NO price >= 92%
GIMME_BET_MIN_VOLUME = 10000      # $10K minimum volume
GIMME_BET_MAX_DAYS = 30           # Resolves within 30 days


def setup_logging():
    """Call this ONLY inside main(). NEVER at module level."""
    log_dir = PROJECT_ROOT / "data" / "outputs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_dir / "run.log", mode="a"),
            ],
        )


def categorize_market(question: str) -> str:
    """Categorize a market question into a domain."""
    q_lower = question.lower()
    scores = {}
    for cat, patterns in CATEGORY_PATTERNS.items():
        score = sum(1 for p in patterns if re.search(p, q_lower))
        if score > 0:
            scores[cat] = score
    if scores:
        return max(scores, key=scores.get)
    return "other"


def parse_outcome_prices(prices_str) -> tuple:
    """Parse outcomePrices string or list into (yes_price, no_price)."""
    if isinstance(prices_str, str):
        try:
            prices = json.loads(prices_str)
        except (json.JSONDecodeError, TypeError):
            return None, None
    elif isinstance(prices_str, list):
        prices = prices_str
    else:
        return None, None

    if len(prices) >= 2:
        try:
            return float(prices[0]), float(prices[1])
        except (ValueError, TypeError):
            return None, None
    return None, None


def fetch_polymarket_markets(limit: int = 100) -> list:
    """Fetch active markets from Polymarket Gamma API."""
    if requests is None:
        return []

    cache_key = f"polymarket_markets_{date.today().isoformat()}"
    cached = get_cached(cache_key, max_age_hours=4)
    if cached:
        return cached.get("markets", [])

    all_markets = []
    try:
        # Fetch by volume (most active markets)
        url = f"{GAMMA_API}/markets"
        params = {
            "active": "true",
            "closed": "false",
            "order": "volume24hr",
            "ascending": "false",
            "limit": str(min(limit, 100)),
        }
        resp = requests.get(url, params=params, timeout=15,
                           headers={"User-Agent": "TradingSystem/1.0"})
        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, list):
            all_markets = data
        elif isinstance(data, dict) and "data" in data:
            all_markets = data["data"]

        # If we got markets, also fetch a second page for broader coverage
        if len(all_markets) >= 50 and limit > 100:
            params["offset"] = "100"
            resp2 = requests.get(url, params=params, timeout=15,
                                headers={"User-Agent": "TradingSystem/1.0"})
            if resp2.status_code == 200:
                data2 = resp2.json()
                if isinstance(data2, list):
                    all_markets.extend(data2)
                elif isinstance(data2, dict) and "data" in data2:
                    all_markets.extend(data2["data"])

        set_cached(cache_key, {"markets": all_markets})
        logger.info("Fetched %d markets from Polymarket Gamma API", len(all_markets))

    except Exception as e:
        logger.warning("Failed to fetch Polymarket markets: %s", e)

    return all_markets


def fetch_weather_forecast(lat: float, lon: float, days: int = 3) -> dict:
    """Fetch weather forecast from Open-Meteo API."""
    if requests is None:
        return {}
    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,rain_sum,snowfall_sum,wind_speed_10m_max",
            "temperature_unit": "fahrenheit",
            "precipitation_unit": "inch",
            "wind_speed_unit": "mph",
            "timezone": "America/New_York",
            "forecast_days": days,
        }
        resp = requests.get(WEATHER_API, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.debug("Weather API failed: %s", e)
        return {}


# City coordinates for weather market matching
CITY_COORDS = {
    "nyc": (40.7128, -74.0060),
    "new york": (40.7128, -74.0060),
    "los angeles": (34.0522, -118.2437),
    "la": (34.0522, -118.2437),
    "chicago": (41.8781, -87.6298),
    "london": (51.5074, -0.1278),
    "miami": (25.7617, -80.1918),
    "dallas": (32.7767, -96.7970),
    "seattle": (47.6062, -122.3321),
    "denver": (39.7392, -104.9903),
    "seoul": (37.5665, 126.9780),
    "tokyo": (35.6762, 139.6503),
}


def detect_city(question: str) -> tuple:
    """Try to detect city from market question. Returns (city_name, lat, lon) or None."""
    q_lower = question.lower()
    for city, (lat, lon) in CITY_COORDS.items():
        if city in q_lower:
            return city, lat, lon
    return None


def analyze_weather_market(market: dict) -> dict:
    """Cross-reference weather market with forecast data."""
    question = market.get("question", "")
    yes_price, no_price = parse_outcome_prices(market.get("outcomePrices"))
    if yes_price is None:
        return {}

    city_info = detect_city(question)
    if not city_info:
        return {"analysis": "no_city_match", "detail": "Could not identify city in question"}

    city_name, lat, lon = city_info
    forecast = fetch_weather_forecast(lat, lon)
    if not forecast or "daily" not in forecast:
        return {"analysis": "no_forecast", "detail": f"Weather API unavailable for {city_name}"}

    daily = forecast["daily"]
    q_lower = question.lower()

    # Try to match specific weather conditions
    result = {
        "city": city_name,
        "forecast_available": True,
        "analysis": "analyzed",
    }

    # Temperature markets
    if "temperature" in q_lower or "°f" in q_lower or "high" in q_lower:
        if daily.get("temperature_2m_max"):
            tomorrow_high = daily["temperature_2m_max"][0] if daily["temperature_2m_max"] else None
            if tomorrow_high is not None:
                result["forecast_high_f"] = tomorrow_high
                # Try to extract threshold from question
                temp_match = re.search(r'(\d+)\s*°?f', q_lower)
                above_match = re.search(r'above\s+(\d+)', q_lower)
                below_match = re.search(r'below\s+(\d+)', q_lower)

                threshold = None
                direction = "above"
                if above_match:
                    threshold = float(above_match.group(1))
                    direction = "above"
                elif below_match:
                    threshold = float(below_match.group(1))
                    direction = "below"
                elif temp_match:
                    threshold = float(temp_match.group(1))
                    direction = "above"  # default

                if threshold:
                    margin = tomorrow_high - threshold
                    if direction == "above":
                        # Rough probability based on margin and typical forecast error (~5°F)
                        if margin > 10:
                            est_prob = 0.92
                        elif margin > 5:
                            est_prob = 0.80
                        elif margin > 2:
                            est_prob = 0.65
                        elif margin > -2:
                            est_prob = 0.45
                        elif margin > -5:
                            est_prob = 0.25
                        else:
                            est_prob = 0.10
                    else:  # below
                        if margin < -10:
                            est_prob = 0.92
                        elif margin < -5:
                            est_prob = 0.80
                        elif margin < -2:
                            est_prob = 0.65
                        elif margin < 2:
                            est_prob = 0.45
                        elif margin < 5:
                            est_prob = 0.25
                        else:
                            est_prob = 0.10

                    result["threshold_f"] = threshold
                    result["direction"] = direction
                    result["estimated_probability"] = round(est_prob, 2)
                    result["market_price"] = yes_price
                    result["edge"] = round(est_prob - yes_price, 3)
                    result["ev_per_dollar"] = round(est_prob * 1.0 - yes_price, 3)

    # Rain/precipitation markets
    elif "rain" in q_lower or "precipitation" in q_lower:
        if daily.get("rain_sum"):
            tomorrow_rain = daily["rain_sum"][0] if daily["rain_sum"] else 0
            result["forecast_rain_inches"] = tomorrow_rain
            # Any rain > 0.01 inches counts
            est_prob = 0.80 if tomorrow_rain > 0.1 else 0.55 if tomorrow_rain > 0.01 else 0.15
            result["estimated_probability"] = round(est_prob, 2)
            result["market_price"] = yes_price
            result["edge"] = round(est_prob - yes_price, 3)
            result["ev_per_dollar"] = round(est_prob * 1.0 - yes_price, 3)

    # Snow markets
    elif "snow" in q_lower:
        if daily.get("snowfall_sum"):
            tomorrow_snow = daily["snowfall_sum"][0] if daily["snowfall_sum"] else 0
            result["forecast_snow_inches"] = tomorrow_snow
            snow_match = re.search(r'(\d+)\+?\s*inch', q_lower)
            threshold = float(snow_match.group(1)) if snow_match else 1.0
            margin = tomorrow_snow - threshold
            if margin > 3:
                est_prob = 0.85
            elif margin > 1:
                est_prob = 0.65
            elif margin > -0.5:
                est_prob = 0.40
            else:
                est_prob = 0.12
            result["estimated_probability"] = round(est_prob, 2)
            result["market_price"] = yes_price
            result["edge"] = round(est_prob - yes_price, 3)
            result["ev_per_dollar"] = round(est_prob * 1.0 - yes_price, 3)

    return result


def analyze_crypto_market(market: dict) -> dict:
    """Cross-reference crypto market with existing technical signals."""
    question = market.get("question", "")
    yes_price, no_price = parse_outcome_prices(market.get("outcomePrices"))
    if yes_price is None:
        return {}

    result = {"analysis": "crypto_signal"}

    # Try loading technical signals for BTC correlation
    try:
        tech = load_envelope("technical_signals.json")
        if tech.get("status") in ("success", "partial"):
            for t in tech["data"].get("results", []):
                ticker = t.get("ticker", "")
                # Check if this market relates to any of our tracked tickers
                q_lower = question.lower()
                if ("bitcoin" in q_lower or "btc" in q_lower) and ticker in ("BTC-USD", "GBTC"):
                    result["technical_composite"] = t.get("composite_score")
                    result["rsi"] = t.get("rsi_14")
                    if t.get("composite_score", 0) > 2:
                        result["signal"] = "bullish_technicals"
                    elif t.get("composite_score", 0) < -2:
                        result["signal"] = "bearish_technicals"
                    else:
                        result["signal"] = "neutral_technicals"
    except Exception:
        pass

    return result


def scan_gimme_bets(markets: list) -> list:
    """Find near-guaranteed profit plays: extreme prices, decent volume, resolving soon."""
    now = datetime.now().astimezone()
    results = []
    for m in markets:
        question = m.get("question", "")
        yes_price, no_price = parse_outcome_prices(m.get("outcomePrices"))
        if yes_price is None:
            continue

        try:
            vol = float(m.get("volume24hr", 0) or 0)
            liq = float(m.get("liquidity", 0) or 0)
        except (ValueError, TypeError):
            continue
        if vol < GIMME_BET_MIN_VOLUME:
            continue

        end_str = m.get("endDate", "")
        days_to_expiry = None
        if end_str:
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                days_to_expiry = (end_dt - now).days
            except (ValueError, TypeError):
                pass

        if days_to_expiry is not None and days_to_expiry > GIMME_BET_MAX_DAYS:
            continue
        if days_to_expiry is not None and days_to_expiry < 0:
            continue

        # Check for extreme prices (YES >= 0.92 or NO >= 0.92)
        for side, price in [("YES", yes_price), ("NO", no_price)]:
            if price >= GIMME_BET_PRICE_THRESHOLD:
                profit_per_share = 1.0 - price
                raw_return_pct = round((profit_per_share / price) * 100, 2)
                annualized = round(raw_return_pct * (365 / max(days_to_expiry, 1)), 1) if days_to_expiry else None

                # Risk flags
                risks = []
                try:
                    spread = float(m.get("spread", 0) or 0)
                except (ValueError, TypeError):
                    spread = 0
                if spread > 0.04:
                    risks.append("wide_spread")
                if liq < 5000:
                    risks.append("low_liquidity")
                if days_to_expiry is not None and days_to_expiry <= 2:
                    risks.append("imminent_resolution")
                if price >= 0.98:
                    risks.append("likely_settled")

                results.append({
                    "question": question,
                    "slug": m.get("slug", ""),
                    "side": side,
                    "price": round(price, 3),
                    "profit_per_share": round(profit_per_share, 3),
                    "raw_return_pct": raw_return_pct,
                    "annualized_yield_pct": annualized,
                    "volume_24h": vol,
                    "liquidity": liq,
                    "days_to_expiry": days_to_expiry,
                    "risks": risks,
                    "category": categorize_market(question),
                    # neg_risk = multi-outcome / winner-take-all contract.
                    # py-clob-client 0.34.6 fails these with order_version_mismatch.
                    "neg_risk": bool(m.get("negRisk", False)),
                })

    # Sort by annualized yield (None last)
    results.sort(key=lambda x: x.get("annualized_yield_pct") or 0, reverse=True)
    return results[:15]


# Sports event grouping patterns
SPORTS_EVENTS = {
    "March Madness": [r"march madness", r"ncaa.*tournament", r"bracket", r"sweet sixteen", r"elite eight", r"final four"],
    "NBA Playoffs": [r"nba.*playoff", r"nba.*finals", r"nba.*champion"],
    "NBA": [r"\bnba\b"],
    "NFL": [r"\bnfl\b", r"super bowl"],
    "Premier League": [r"premier league", r"\bepl\b"],
    "La Liga": [r"la liga"],
    "UFC": [r"\bufc\b", r"fight night", r"\bbout\b"],
    "MLB": [r"\bmlb\b", r"world series"],
    "NHL": [r"\bnhl\b", r"stanley cup"],
    "College Basketball": [r"ncaa", r"college basketball"],
    "Other Sports": [r"\bvs\.?\b", r"match", r"score", r"championship", r"playoffs", r"finals", r"win the"],
}


def group_sports_events(markets: list) -> list:
    """Group sports markets by named events, flagging today/this-week markets."""
    now = datetime.now().astimezone()
    today = now.date()
    week_end = today + timedelta(days=7)

    sports_markets = []
    for m in markets:
        question = m.get("question", "")
        if not question:
            continue
        cat = m.get("category") or categorize_market(question)
        if cat != "sports":
            continue

        yes_price, no_price = parse_outcome_prices(m.get("outcomePrices"))
        if yes_price is None:
            continue

        end_str = m.get("endDate", "")
        end_date = None
        if end_str:
            try:
                end_date = datetime.fromisoformat(end_str.replace("Z", "+00:00")).date()
            except (ValueError, TypeError):
                pass

        is_today = end_date == today if end_date else False
        is_this_week = (today <= end_date <= week_end) if end_date else False

        # Match to event group
        q_lower = question.lower()
        event_name = "Other Sports"
        for evt, patterns in SPORTS_EVENTS.items():
            if evt == "Other Sports":
                continue
            if any(re.search(p, q_lower) for p in patterns):
                event_name = evt
                break

        sports_markets.append({
            "question": question,
            "slug": m.get("slug", ""),
            "yes_price": yes_price,
            "no_price": no_price,
            "volume_24h": m.get("volume24hr", 0) or 0,
            "end_date": end_str,
            "event": event_name,
            "is_today": is_today,
            "is_this_week": is_this_week,
        })

    # Group by event
    from collections import OrderedDict
    groups = defaultdict(list)
    for sm in sports_markets:
        groups[sm["event"]].append(sm)

    result = []
    for event_name, items in groups.items():
        # Sort: today first, then this-week, then by volume
        items.sort(key=lambda x: (not x["is_today"], not x["is_this_week"], -x["volume_24h"]))
        has_today = any(i["is_today"] for i in items)
        has_this_week = any(i["is_this_week"] for i in items)
        result.append({
            "event": event_name,
            "market_count": len(items),
            "has_today": has_today,
            "has_this_week": has_this_week,
            "total_volume": sum(i["volume_24h"] for i in items),
            "top_markets": items[:10],
        })

    # Sort: today-events first, then this-week, then by volume
    result.sort(key=lambda x: (not x["has_today"], not x["has_this_week"], -x["total_volume"]))
    return result


def fetch_crypto_intelligence() -> dict:
    """Fetch BTC Fear & Greed, funding rates, and price context from free APIs."""
    if requests is None:
        return {}

    cache_key = f"crypto_intelligence_{date.today().isoformat()}"
    cached = get_cached(cache_key, max_age_hours=1)
    if cached:
        return cached

    intel = {}

    # Fear & Greed Index (alternative.me — free, no auth)
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=7", timeout=10)
        resp.raise_for_status()
        fng_data = resp.json().get("data", [])
        if fng_data:
            current = fng_data[0]
            intel["fear_greed"] = {
                "value": int(current["value"]),
                "label": current["value_classification"],
                "history": [{"value": int(d["value"]), "label": d["value_classification"]} for d in fng_data],
                "trend": "improving" if len(fng_data) >= 3 and int(fng_data[0]["value"]) > int(fng_data[2]["value"]) else
                         "worsening" if len(fng_data) >= 3 and int(fng_data[0]["value"]) < int(fng_data[2]["value"]) else "flat",
            }
    except Exception as e:
        logger.debug("Fear & Greed API failed: %s", e)

    # BTC funding rate (OKX — free, no auth, no geo-block)
    try:
        resp = requests.get("https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP", timeout=10)
        resp.raise_for_status()
        okx = resp.json().get("data", [])
        if okx:
            rate = float(okx[0].get("fundingRate", 0))
            settled = float(okx[0].get("settFundingRate", 0))
            # Annualize: rate * 3 * 365 (funding every 8h)
            annualized = rate * 3 * 365 * 100
            intel["funding_rate"] = {
                "current": round(rate * 100, 4),  # as percentage
                "settled": round(settled * 100, 4),
                "annualized_pct": round(annualized, 1),
                "signal": "overleveraged_long" if rate > 0.01 else
                          "overleveraged_short" if rate < -0.01 else
                          "slightly_long" if rate > 0.005 else
                          "slightly_short" if rate < -0.005 else "neutral",
            }
    except Exception as e:
        logger.debug("OKX funding rate failed: %s", e)

    # BTC price context (CoinGecko — free, no auth)
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin",
            params={"localization": "false", "tickers": "false", "market_data": "true",
                    "community_data": "false", "developer_data": "false"},
            timeout=10,
        )
        resp.raise_for_status()
        md = resp.json().get("market_data", {})
        intel["btc_price"] = {
            "current": md.get("current_price", {}).get("usd", 0),
            "high_24h": md.get("high_24h", {}).get("usd", 0),
            "low_24h": md.get("low_24h", {}).get("usd", 0),
            "change_24h_pct": round(md.get("price_change_percentage_24h", 0), 2),
            "change_7d_pct": round(md.get("price_change_percentage_7d", 0), 2),
            "change_30d_pct": round(md.get("price_change_percentage_30d", 0), 2),
            "ath": md.get("ath", {}).get("usd", 0),
            "ath_drop_pct": round(md.get("ath_change_percentage", {}).get("usd", 0), 1),
        }
    except Exception as e:
        logger.debug("CoinGecko BTC price failed: %s", e)

    if intel:
        set_cached(cache_key, intel)
    return intel


def generate_contradiction_alerts(portfolio: dict, crypto_intel: dict) -> list:
    """Cross-reference portfolio positions against technical signals, macro data, and crypto intel.

    Flags positions that contradict available signals — e.g., holding bullish BTC bets
    while Fear & Greed is at Extreme Fear and funding is negative.
    """
    alerts = []
    if not portfolio or not portfolio.get("top_positions"):
        return alerts

    # Load existing module outputs for cross-referencing
    tech = load_envelope("technical_signals.json")
    macro = load_envelope("macro_dashboard.json")
    news = load_envelope("news_sentiment.json")

    # Determine overall BTC bias from signals
    btc_signals = {"bullish": 0, "bearish": 0, "reasons": []}

    # Fear & Greed signal
    fng = crypto_intel.get("fear_greed", {})
    fng_val = fng.get("value", 50)
    if fng_val <= 20:
        # Extreme fear historically = contrarian buy signal
        btc_signals["bullish"] += 2
        btc_signals["reasons"].append(f"Fear & Greed at {fng_val} (Extreme Fear) — contrarian bullish")
    elif fng_val <= 35:
        btc_signals["bullish"] += 1
        btc_signals["reasons"].append(f"Fear & Greed at {fng_val} (Fear) — leaning contrarian bullish")
    elif fng_val >= 80:
        btc_signals["bearish"] += 2
        btc_signals["reasons"].append(f"Fear & Greed at {fng_val} (Extreme Greed) — contrarian bearish")
    elif fng_val >= 65:
        btc_signals["bearish"] += 1
        btc_signals["reasons"].append(f"Fear & Greed at {fng_val} (Greed) — leaning contrarian bearish")

    # Funding rate signal
    funding = crypto_intel.get("funding_rate", {})
    fund_signal = funding.get("signal", "neutral")
    if fund_signal == "overleveraged_long":
        btc_signals["bearish"] += 2
        btc_signals["reasons"].append(f"Funding rate {funding.get('current', 0):.4f}% — overleveraged longs, correction risk")
    elif fund_signal == "overleveraged_short":
        btc_signals["bullish"] += 2
        btc_signals["reasons"].append(f"Funding rate {funding.get('current', 0):.4f}% — overleveraged shorts, squeeze potential")
    elif fund_signal == "slightly_long":
        btc_signals["bearish"] += 1
        btc_signals["reasons"].append(f"Funding rate slightly positive — mild long bias")
    elif fund_signal == "slightly_short":
        btc_signals["bullish"] += 1
        btc_signals["reasons"].append(f"Funding rate slightly negative — mild short bias")

    # BTC price momentum
    btc_price = crypto_intel.get("btc_price", {})
    change_7d = btc_price.get("change_7d_pct", 0)
    if change_7d > 5:
        btc_signals["bullish"] += 1
        btc_signals["reasons"].append(f"BTC +{change_7d:.1f}% in 7d — positive momentum")
    elif change_7d < -5:
        btc_signals["bearish"] += 1
        btc_signals["reasons"].append(f"BTC {change_7d:.1f}% in 7d — negative momentum")

    # Macro signals (VIX, DXY, Gold)
    if macro.get("status") in ("success", "partial"):
        indicators = macro.get("data", {}).get("indicators", {})
        vix = indicators.get("VIX", {})
        if (vix.get("value") or 0) > 25:
            btc_signals["bearish"] += 1
            btc_signals["reasons"].append(f"VIX at {vix['value']:.0f} — risk-off environment")
        dxy = indicators.get("DXY", {})
        if (dxy.get("change_5d") or 0) > 1:
            btc_signals["bearish"] += 1
            btc_signals["reasons"].append(f"Dollar strengthening (DXY +{dxy['change_5d']:.1f}% 5d) — headwind for BTC")
        elif (dxy.get("change_5d") or 0) < -1:
            btc_signals["bullish"] += 1
            btc_signals["reasons"].append(f"Dollar weakening (DXY {dxy['change_5d']:.1f}% 5d) — tailwind for BTC")
        gold = indicators.get("GOLD", {})
        if (gold.get("change_5d") or 0) > 3:
            btc_signals["bullish"] += 1
            btc_signals["reasons"].append(f"Gold surging (+{gold['change_5d']:.1f}% 5d) — risk hedge demand up")

    # Determine overall signal bias
    bull = btc_signals["bullish"]
    bear = btc_signals["bearish"]
    if bull >= bear + 2:
        overall_bias = "bullish"
    elif bear >= bull + 2:
        overall_bias = "bearish"
    elif bull > bear:
        overall_bias = "slightly_bullish"
    elif bear > bull:
        overall_bias = "slightly_bearish"
    else:
        overall_bias = "neutral"

    btc_signals["overall_bias"] = overall_bias
    btc_signals["bull_score"] = bull
    btc_signals["bear_score"] = bear

    # Now check each active position for contradictions
    btc_current = btc_price.get("current", 0)

    for pos in portfolio.get("top_positions", []):
        title = pos.get("title", "").lower()
        outcome = pos.get("outcome", "")
        pnl = pos.get("pnl", 0)
        current_value = pos.get("current_value", 0)

        # Determine position's directional bet
        position_direction = None
        if any(kw in title for kw in ["dip to", "reach", "hit"]):
            # "Will BTC dip to $65K" — YES = bearish, NO = bullish
            if "dip" in title:
                position_direction = "bearish" if outcome == "Yes" else "bullish"
            else:
                position_direction = "bullish" if outcome == "Yes" else "bearish"
        elif "above" in title:
            position_direction = "bullish" if outcome == "Yes" else "bearish"
        elif "below" in title:
            position_direction = "bearish" if outcome == "Yes" else "bullish"
        elif "between" in title:
            position_direction = "range"  # neutral/range bet
        elif "up or down" in title:
            if outcome.lower() == "up":
                position_direction = "bullish"
            elif outcome.lower() == "down":
                position_direction = "bearish"

        if position_direction is None:
            continue

        # Check for contradiction
        contradiction = False
        severity = "info"
        detail = ""

        if position_direction == "bullish" and overall_bias in ("bearish", "slightly_bearish"):
            contradiction = True
            severity = "warning" if overall_bias == "bearish" else "info"
            detail = f"You're betting BTC UP ({outcome} on \"{pos['title'][:50]}\") but signals lean {overall_bias} ({bear}🐻 vs {bull}🐂)"
        elif position_direction == "bearish" and overall_bias in ("bullish", "slightly_bullish"):
            contradiction = True
            severity = "warning" if overall_bias == "bullish" else "info"
            detail = f"You're betting BTC DOWN ({outcome} on \"{pos['title'][:50]}\") but signals lean {overall_bias} ({bull}🐂 vs {bear}🐻)"
        elif position_direction == "range" and abs(change_7d) > 7:
            contradiction = True
            severity = "warning"
            detail = f"Range bet on \"{pos['title'][:50]}\" but BTC moved {change_7d:+.1f}% in 7d — high volatility"

        # Also flag positions that are deeply underwater and contradicted
        if contradiction and pnl < -1000:
            severity = "critical"
            detail += f" | Already down ${abs(pnl):,.0f}"

        if contradiction:
            alerts.append({
                "title": pos.get("title", ""),
                "outcome": outcome,
                "position_direction": position_direction,
                "signal_bias": overall_bias,
                "severity": severity,
                "detail": detail,
                "current_value": current_value,
                "pnl": pnl,
            })

    # Sort: critical first, then warning, then info
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: severity_order.get(a["severity"], 3))

    return alerts


def analyze_economics_markets(markets: list) -> list:
    """Cross-reference economics markets with macro dashboard and economic calendar."""
    econ_markets = []
    for m in markets:
        question = m.get("question", "")
        if not question:
            continue
        cat = m.get("category") or categorize_market(question)
        if cat != "economics":
            continue
        yes_price, no_price = parse_outcome_prices(m.get("outcomePrices"))
        if yes_price is None:
            continue
        econ_markets.append({
            "question": question,
            "slug": m.get("slug", ""),
            "yes_price": yes_price,
            "no_price": no_price,
            "volume_24h": m.get("volume24hr", 0) or 0,
            "end_date": m.get("endDate", ""),
        })

    if not econ_markets:
        return []

    # Load macro data
    macro = load_envelope("macro_dashboard.json")
    econ_cal = load_envelope("economic_calendar.json")

    indicators = {}
    if macro.get("status") in ("success", "partial"):
        indicators = macro.get("data", {}).get("indicators", {})

    fomc_next = None
    if econ_cal.get("status") in ("success", "partial"):
        fomc_next = econ_cal.get("data", {}).get("fomc_next", {})

    results = []
    for em in econ_markets:
        q_lower = em["question"].lower()
        macro_signals = []
        edge_direction = None

        # Fed/rate markets
        if any(kw in q_lower for kw in ["fed", "rate", "interest rate", "rate cut", "rate hike", "bps", "federal reserve"]):
            hawkish = 0
            dovish = 0

            us10y = indicators.get("US10Y", {})
            if us10y.get("trend") == "rising" or (us10y.get("change_5d", 0) or 0) > 0:
                hawkish += 1
                macro_signals.append(f"10Y yield rising ({us10y.get('value', '?')})")
            elif us10y.get("trend") == "falling" or (us10y.get("change_5d", 0) or 0) < 0:
                dovish += 1
                macro_signals.append(f"10Y yield falling ({us10y.get('value', '?')})")

            dxy = indicators.get("DXY", {})
            if (dxy.get("change_5d", 0) or 0) > 0:
                hawkish += 1
                macro_signals.append(f"Dollar strengthening (DXY {dxy.get('value', '?')})")
            elif (dxy.get("change_5d", 0) or 0) < 0:
                dovish += 1
                macro_signals.append(f"Dollar weakening (DXY {dxy.get('value', '?')})")

            oil = indicators.get("OIL", {})
            if (oil.get("change_20d", 0) or 0) > 10:
                hawkish += 1
                macro_signals.append(f"Oil surging (+{oil.get('change_20d', 0):.0f}% 20d)")

            vix = indicators.get("VIX", {})
            if (vix.get("value", 0) or 0) > 25:
                dovish += 1
                macro_signals.append(f"VIX elevated ({vix.get('value', '?')}) — market stress")

            if hawkish > dovish:
                edge_direction = "hawkish"
            elif dovish > hawkish:
                edge_direction = "dovish"
            else:
                edge_direction = "mixed"

        # CPI/inflation markets
        elif any(kw in q_lower for kw in ["cpi", "inflation"]):
            oil = indicators.get("OIL", {})
            if (oil.get("change_20d", 0) or 0) > 10:
                macro_signals.append(f"Oil up sharply — inflationary pressure")
                edge_direction = "higher_inflation"
            elif (oil.get("change_20d", 0) or 0) < -10:
                macro_signals.append(f"Oil down sharply — disinflationary")
                edge_direction = "lower_inflation"

        # FOMC proximity flag
        if fomc_next and fomc_next.get("days_until") is not None:
            days = fomc_next["days_until"]
            if days <= 7:
                macro_signals.append(f"FOMC in {days} day(s) — {fomc_next.get('date', '?')}")

        if macro_signals:
            results.append({
                **em,
                "macro_signals": macro_signals,
                "edge_direction": edge_direction,
            })

    return results


def detect_trending(markets: list) -> list:
    """Detect trending markets using oneWeekPriceChange (primary) and oneDayPriceChange."""
    results = []
    for m in markets:
        question = m.get("question", "")
        if not question:
            continue
        yes_price, no_price = parse_outcome_prices(m.get("outcomePrices"))
        if yes_price is None:
            continue

        week_change = m.get("oneWeekPriceChange", 0) or 0
        day_change = m.get("oneDayPriceChange", 0) or 0

        if abs(week_change) < 0.03:
            continue

        # Momentum: week + day change in same direction
        has_momentum = (week_change > 0 and day_change > 0) or (week_change < 0 and day_change < 0)

        results.append({
            "question": question,
            "slug": m.get("slug", ""),
            "yes_price": yes_price,
            "no_price": no_price,
            "volume_24h": m.get("volume24hr", 0) or 0,
            "week_change_pct": round(week_change * 100, 1),
            "day_change_pct": round(day_change * 100, 1),
            "has_momentum": has_momentum,
            "direction": "up" if week_change > 0 else "down",
            "category": m.get("category") or categorize_market(question),
        })

    # Sort: momentum markets first, then by absolute week change
    results.sort(key=lambda x: (not x["has_momentum"], -abs(x["week_change_pct"])))
    return results[:20]


def analyze_markets(markets: list) -> dict:
    """Analyze all markets and identify opportunities.

    Returns analysis dict with:
    - opportunities: markets with estimated edge > threshold
    - big_movers: markets with large 24h price changes
    - category_summary: breakdown by domain
    - top_volume: highest volume markets
    """
    if not markets:
        return {
            "opportunities": [],
            "big_movers": [],
            "weather_analysis": [],
            "crypto_analysis": [],
            "gimme_bets": [],
            "sports_events": [],
            "economics_signals": [],
            "trending": [],
            "category_summary": {},
            "top_volume": [],
            "markets_scanned": 0,
            "signal": "no_data",
            "signal_detail": "No market data available",
        }

    categorized = defaultdict(list)
    opportunities = []
    big_movers = []
    weather_analysis = []
    crypto_analysis = []

    for m in markets:
        question = m.get("question", "")
        if not question:
            continue

        yes_price, no_price = parse_outcome_prices(m.get("outcomePrices"))
        if yes_price is None:
            continue

        vol = m.get("volume24hr", 0) or 0
        liq = m.get("liquidity", 0) or 0
        spread = m.get("spread", 0) or 0
        week_change = m.get("oneWeekPriceChange", 0) or 0
        day_change = m.get("oneDayPriceChange", 0) or 0
        change = week_change if abs(week_change) > abs(day_change) else day_change

        # Skip very low liquidity/volume markets
        if vol < MIN_VOLUME_24H and liq < MIN_LIQUIDITY:
            continue

        category = m.get("category") or categorize_market(question)

        market_info = {
            "id": m.get("id", ""),
            "question": question,
            "slug": m.get("slug", ""),
            "yes_price": yes_price,
            "no_price": no_price,
            "volume_24h": vol,
            "liquidity": liq,
            "spread": spread,
            "price_change_24h": change,
            "end_date": m.get("endDate", ""),
            "category": category,
        }

        categorized[category].append(market_info)

        # Check for big movers
        if abs(change) >= BIG_MOVE_THRESHOLD:
            direction = "up" if change > 0 else "down"
            big_movers.append({
                **market_info,
                "move_direction": direction,
                "move_pct": round(change * 100, 1),
            })

        # Weather market analysis
        if category == "weather":
            wx = analyze_weather_market(m)
            if wx and wx.get("estimated_probability") is not None:
                edge = wx.get("edge", 0)
                weather_entry = {**market_info, "weather": wx}
                weather_analysis.append(weather_entry)
                if abs(edge) >= MIN_EDGE_PCT:
                    side = "YES" if edge > 0 else "NO"
                    est_prob = wx["estimated_probability"] if edge > 0 else 1 - wx["estimated_probability"]
                    price = yes_price if edge > 0 else no_price
                    opportunities.append({
                        **market_info,
                        "opportunity_type": "weather_edge",
                        "recommended_side": side,
                        "estimated_probability": round(est_prob, 2),
                        "market_price": round(price, 3),
                        "edge_pct": round(abs(edge) * 100, 1),
                        "ev_per_dollar": round(est_prob - price, 3),
                        "confidence": "high" if abs(edge) > 0.25 else "medium" if abs(edge) > 0.15 else "low",
                        "source": "weather_forecast",
                        "detail": wx.get("detail", f"Forecast analysis for {wx.get('city', 'unknown')}"),
                    })

        # Crypto market analysis
        elif category == "crypto":
            cx = analyze_crypto_market(m)
            if cx and cx.get("signal"):
                crypto_analysis.append({**market_info, "crypto": cx})

    # Arbitrage detection: YES + NO != 1.00
    for m in markets:
        yes_price, no_price = parse_outcome_prices(m.get("outcomePrices"))
        if yes_price is not None and no_price is not None:
            total = yes_price + no_price
            if total < 0.97 or total > 1.03:
                arb_edge = abs(1.0 - total)
                if arb_edge >= 0.02:
                    opportunities.append({
                        "id": m.get("id", ""),
                        "question": m.get("question", ""),
                        "slug": m.get("slug", ""),
                        "yes_price": yes_price,
                        "no_price": no_price,
                        "volume_24h": m.get("volume24hr", 0) or 0,
                        "liquidity": m.get("liquidity", 0) or 0,
                        "spread": m.get("spread", 0) or 0,
                        "price_change_24h": m.get("oneDayPriceChange", 0) or 0,
                        "end_date": m.get("endDate", ""),
                        "category": categorize_market(m.get("question", "")),
                        "opportunity_type": "arbitrage",
                        "recommended_side": "BOTH",
                        "estimated_probability": None,
                        "market_price": total,
                        "edge_pct": round(arb_edge * 100, 1),
                        "ev_per_dollar": round(arb_edge, 3),
                        "confidence": "high" if arb_edge > 0.05 else "medium",
                        "source": "price_mismatch",
                        "detail": f"YES ({yes_price}) + NO ({no_price}) = {total:.3f} (should be 1.00)",
                    })

    # Sort opportunities by EV
    opportunities.sort(key=lambda x: abs(x.get("ev_per_dollar", 0)), reverse=True)
    big_movers.sort(key=lambda x: abs(x.get("price_change_24h", 0)), reverse=True)

    # Category summary
    category_summary = {}
    for cat, items in categorized.items():
        cat_opps = [o for o in opportunities if o.get("category") == cat]
        category_summary[cat] = {
            "markets": len(items),
            "opportunities": len(cat_opps),
            "total_volume_24h": sum(i.get("volume_24h", 0) for i in items),
        }

    # Top volume markets
    all_market_infos = []
    for items in categorized.values():
        all_market_infos.extend(items)
    all_market_infos.sort(key=lambda x: x.get("volume_24h", 0), reverse=True)
    top_volume = all_market_infos[:10]

    # Overall signal
    total_scanned = sum(len(items) for items in categorized.values())
    if len(opportunities) >= 3:
        signal = "multiple_opportunities"
        signal_detail = f"{len(opportunities)} opportunities found across {len(set(o['category'] for o in opportunities))} categories"
    elif len(opportunities) > 0:
        signal = "opportunities_found"
        signal_detail = f"{len(opportunities)} opportunity found"
    elif len(big_movers) > 0:
        signal = "volatile"
        signal_detail = f"No clear opportunities but {len(big_movers)} big movers detected"
    else:
        signal = "quiet"
        signal_detail = f"Scanned {total_scanned} markets — no strong opportunities today"

    # New analysis functions — run on full market list
    gimme_bets = scan_gimme_bets(markets)
    sports_events = group_sports_events(markets)
    economics_signals = analyze_economics_markets(markets)
    trending = detect_trending(markets)

    return {
        "opportunities": opportunities[:15],
        "big_movers": big_movers[:10],
        "weather_analysis": weather_analysis[:10],
        "crypto_analysis": crypto_analysis[:10],
        "gimme_bets": gimme_bets,
        "sports_events": sports_events,
        "economics_signals": economics_signals,
        "trending": trending,
        "category_summary": category_summary,
        "top_volume": top_volume,
        "markets_scanned": total_scanned,
        "signal": signal,
        "signal_detail": signal_detail,
    }


def load_sample_markets() -> list:
    """Load sample Polymarket data as fallback."""
    sample_path = PROJECT_ROOT / "data" / "sample" / "polymarket_markets.json"
    if sample_path.exists():
        try:
            data = json.loads(sample_path.read_text())
            logger.info("Loaded %d sample Polymarket markets", len(data.get("markets", [])))
            return data.get("markets", [])
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load sample markets: %s", e)
    return []


def fetch_portfolio_positions(wallet: str) -> list:
    """Fetch all positions for a wallet from the Polymarket Data API."""
    if requests is None or not wallet:
        return []
    cache_key = f"polymarket_portfolio_{wallet[:10]}_{date.today().isoformat()}"
    cached = get_cached(cache_key, max_age_hours=1)
    if cached:
        return cached.get("positions", [])
    try:
        resp = requests.get(
            f"{DATA_API}/positions",
            params={"user": wallet, "sizeThreshold": "0.1"},
            timeout=15,
            headers={"User-Agent": "TradingSystem/1.0"},
        )
        resp.raise_for_status()
        positions = resp.json()
        if isinstance(positions, list):
            set_cached(cache_key, {"positions": positions})
            logger.info("Fetched %d portfolio positions for %s...", len(positions), wallet[:10])
            return positions
    except Exception as e:
        logger.warning("Failed to fetch portfolio positions: %s", e)
    return []


def fetch_portfolio_activity(wallet: str, limit: int = 20) -> list:
    """Fetch recent trade activity for a wallet from the Polymarket Data API."""
    if requests is None or not wallet:
        return []
    cache_key = f"polymarket_activity_{wallet[:10]}_{date.today().isoformat()}"
    cached = get_cached(cache_key, max_age_hours=1)
    if cached:
        return cached.get("activity", [])
    try:
        resp = requests.get(
            f"{DATA_API}/activity",
            params={"user": wallet, "limit": str(limit)},
            timeout=15,
            headers={"User-Agent": "TradingSystem/1.0"},
        )
        resp.raise_for_status()
        activity = resp.json()
        if isinstance(activity, list):
            set_cached(cache_key, {"activity": activity})
            logger.info("Fetched %d activity records for %s...", len(activity), wallet[:10])
            return activity
    except Exception as e:
        logger.warning("Failed to fetch portfolio activity: %s", e)
    return []


def analyze_portfolio(positions: list, activity: list) -> dict:
    """Analyze a Polymarket portfolio: P&L, win rate, concentration, risk."""
    if not positions:
        return {}

    active = [p for p in positions if (p.get("currentValue") or 0) > 0]
    closed = [p for p in positions if (p.get("currentValue") or 0) == 0 and p.get("redeemable")]

    # Aggregate P&L
    total_initial = sum(p.get("initialValue", 0) or 0 for p in positions)
    total_current = sum(p.get("currentValue", 0) or 0 for p in positions)
    total_cash_pnl = sum(p.get("cashPnl", 0) or 0 for p in positions)
    total_realized = sum(p.get("realizedPnl", 0) or 0 for p in positions)

    # Win rate from closed positions
    wins = sum(1 for p in positions if (p.get("cashPnl") or 0) > 0)
    losses = sum(1 for p in positions if (p.get("cashPnl") or 0) < 0)
    total_trades = wins + losses
    win_rate = round(wins / total_trades * 100, 1) if total_trades > 0 else 0

    # Top positions by current value
    active_sorted = sorted(active, key=lambda p: p.get("currentValue", 0), reverse=True)
    top_positions = []
    for p in active_sorted[:10]:
        top_positions.append({
            "title": p.get("title", ""),
            "slug": p.get("slug", ""),
            "outcome": p.get("outcome", ""),
            "size": round(p.get("size", 0), 2),
            "avg_price": round(p.get("avgPrice", 0), 4),
            "current_price": round(p.get("curPrice", 0), 4),
            "current_value": round(p.get("currentValue", 0), 2),
            "pnl": round(p.get("cashPnl", 0), 2),
            "pnl_pct": round(p.get("percentPnl", 0), 1),
            "end_date": p.get("endDate", ""),
            "category": categorize_market(p.get("title", "")),
        })

    # Biggest winners and losers
    by_pnl = sorted(positions, key=lambda p: p.get("cashPnl", 0))
    biggest_losers = [
        {"title": p.get("title", "")[:60], "pnl": round(p.get("cashPnl", 0), 2), "outcome": p.get("outcome", "")}
        for p in by_pnl[:3] if (p.get("cashPnl") or 0) < 0
    ]
    biggest_winners = [
        {"title": p.get("title", "")[:60], "pnl": round(p.get("cashPnl", 0), 2), "outcome": p.get("outcome", "")}
        for p in reversed(by_pnl) if (p.get("cashPnl") or 0) > 0
    ][:3]

    # Concentration by category
    category_exposure = defaultdict(float)
    for p in active:
        cat = categorize_market(p.get("title", ""))
        category_exposure[cat] += p.get("currentValue", 0) or 0
    category_exposure = {k: round(v, 2) for k, v in sorted(category_exposure.items(), key=lambda x: x[1], reverse=True)}

    # Recent activity summary
    today_trades = []
    now_ts = datetime.now().astimezone().timestamp()
    for a in activity[:20]:
        ts = a.get("timestamp", 0)
        if now_ts - ts < 86400:  # last 24h
            today_trades.append({
                "title": a.get("title", ""),
                "side": a.get("side", ""),
                "outcome": a.get("outcome", ""),
                "size": a.get("size", 0),
                "usdc": round(a.get("usdcSize", 0), 2),
                "price": round(a.get("price", 0), 4),
            })

    # Expiring soon (next 3 days)
    now_date = date.today()
    expiring_soon = []
    for p in active:
        end_str = p.get("endDate", "")
        if end_str:
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00")).date()
                days_left = (end_dt - now_date).days
                if 0 <= days_left <= 3:
                    expiring_soon.append({
                        "title": p.get("title", ""),
                        "outcome": p.get("outcome", ""),
                        "current_value": round(p.get("currentValue", 0), 2),
                        "pnl": round(p.get("cashPnl", 0), 2),
                        "days_left": days_left,
                        "current_price": round(p.get("curPrice", 0), 4),
                    })
            except (ValueError, TypeError):
                pass
    expiring_soon.sort(key=lambda x: x["days_left"])

    return {
        "wallet": positions[0].get("proxyWallet", "") if positions else "",
        "pseudonym": activity[0].get("pseudonym", "") if activity else "",
        "active_positions": len(active),
        "closed_positions": len(closed),
        "total_positions": len(positions),
        "total_invested": round(total_initial, 2),
        "total_current_value": round(total_current, 2),
        "total_cash_pnl": round(total_cash_pnl, 2),
        "total_realized_pnl": round(total_realized, 2),
        "overall_pnl_pct": round(total_cash_pnl / total_initial * 100, 1) if total_initial > 0 else 0,
        "win_rate": win_rate,
        "wins": wins,
        "losses": losses,
        "top_positions": top_positions,
        "biggest_winners": biggest_winners,
        "biggest_losers": biggest_losers,
        "category_exposure": category_exposure,
        "today_trades": today_trades,
        "expiring_soon": expiring_soon,
    }


def run_polymarket_scanner(limit: int = 200) -> dict:
    """Main entry point: fetch markets and analyze, plus portfolio tracking."""
    markets = fetch_polymarket_markets(limit)
    source = "gamma_api"

    if not markets:
        markets = load_sample_markets()
        source = "sample_data"

    if not markets:
        source = "none"

    logger.info("Polymarket scanner: %d markets from %s", len(markets), source)

    analysis = analyze_markets(markets)
    analysis["data_source"] = source
    analysis["analyzed_at"] = datetime.now().astimezone().isoformat()

    # Portfolio tracking — load wallet from settings
    settings_path = PROJECT_ROOT / "config" / "settings.json"
    wallet = ""
    try:
        settings = json.loads(settings_path.read_text())
        wallet = settings.get("polymarket_wallet", "")
    except Exception:
        pass

    # Crypto intelligence — Fear & Greed, funding rates, BTC price context
    crypto_intel = fetch_crypto_intelligence()
    analysis["crypto_intelligence"] = crypto_intel

    if wallet:
        positions = fetch_portfolio_positions(wallet)
        activity = fetch_portfolio_activity(wallet)
        portfolio = analyze_portfolio(positions, activity)
        analysis["portfolio"] = portfolio
        logger.info("Portfolio: %d positions, P&L $%.2f",
                     portfolio.get("total_positions", 0), portfolio.get("total_cash_pnl", 0))

        # Cross-module contradiction alerts
        contradictions = generate_contradiction_alerts(portfolio, crypto_intel)
        analysis["contradiction_alerts"] = contradictions
        if contradictions:
            critical = sum(1 for c in contradictions if c["severity"] == "critical")
            warnings = sum(1 for c in contradictions if c["severity"] == "warning")
            logger.info("Contradiction alerts: %d critical, %d warnings", critical, warnings)
    else:
        analysis["portfolio"] = {}
        analysis["contradiction_alerts"] = []

    return analysis


def main():
    setup_logging()
    logger.info("=== Polymarket Scanner ===")

    data = run_polymarket_scanner()

    if data["signal"] == "no_data" and data.get("data_source") == "none":
        status = "error"
        error = "No Polymarket data available from any source"
    elif data.get("data_source") == "sample_data":
        status = "partial"
        error = "Using sample data — live API fetch failed"
    else:
        status = "success"
        error = None

    envelope = create_envelope("polymarket_scanner", data, status=status, error=error)
    save_envelope(envelope, "polymarket.json")

    logger.info("Polymarket scanner complete: %d markets, %d opportunities, signal=%s",
                data.get("markets_scanned", 0), len(data.get("opportunities", [])), data["signal"])


if __name__ == "__main__":
    main()
