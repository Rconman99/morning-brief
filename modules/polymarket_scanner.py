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
WEATHER_API = "https://api.open-meteo.com/v1/forecast"
NOAA_API = "https://api.weather.gov"

# Category detection patterns
CATEGORY_PATTERNS = {
    "crypto": [r"bitcoin", r"\bbtc\b", r"ethereum", r"\beth\b", r"crypto", r"solana", r"\bsol\b", r"dogecoin"],
    "weather": [r"temperature", r"rain", r"snow", r"weather", r"°[fc]", r"inches of rain", r"wind speed", r"heat wave", r"cold"],
    "politics": [r"president", r"congress", r"senate", r"election", r"executive order", r"trump", r"biden", r"democrat", r"republican", r"governor", r"nomination"],
    "economics": [r"\bfed\b", r"interest rate", r"inflation", r"gdp", r"recession", r"unemployment", r"cpi\b", r"federal reserve", r"rate cut", r"rate hike", r"bps"],
    "geopolitics": [r"iran", r"china", r"russia", r"ukraine", r"war", r"strait", r"sanctions", r"nato", r"ceasefire", r"invasion", r"regime"],
    "sports": [r"nba", r"nfl", r"mlb", r"nhl", r"world cup", r"super bowl", r"championship", r"playoffs", r"finals", r"la liga", r"premier league", r"win the"],
    "tech": [r"\bai\b", r"agi\b", r"openai", r"google", r"apple", r"microsoft", r"launch", r"ship", r"artificial intelligence", r"gpt"],
    "markets": [r"s&p", r"nasdaq", r"dow jones", r"all-time high", r"stock market", r"spy\b", r"qqq\b"],
    "culture": [r"elon", r"tweet", r"musk", r"celebrity", r"social media", r"tiktok", r"viral"],
}

# Minimum thresholds for opportunity flagging
MIN_VOLUME_24H = 5000       # $5K daily volume minimum
MIN_LIQUIDITY = 2000        # $2K liquidity minimum
MIN_EDGE_PCT = 0.10         # 10% expected value minimum
BIG_MOVE_THRESHOLD = 0.08   # 8% price change = "big move"


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
        change = m.get("oneDayPriceChange", 0) or 0

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

    return {
        "opportunities": opportunities[:15],
        "big_movers": big_movers[:10],
        "weather_analysis": weather_analysis[:10],
        "crypto_analysis": crypto_analysis[:10],
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


def run_polymarket_scanner(limit: int = 200) -> dict:
    """Main entry point: fetch markets and analyze."""
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
