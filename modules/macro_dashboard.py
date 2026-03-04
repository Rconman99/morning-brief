"""Macro Dashboard: market-wide indicators (VIX, DXY, yields, oil, gold) and risk regime detection."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging
import pandas as pd
import numpy as np
from datetime import datetime

from lib.data_envelope import create_envelope, save_envelope
from lib.cache import get_cached, set_cached, make_cache_key

logger = logging.getLogger(__name__)

# Macro indicators tracked
MACRO_TICKERS = {
    "VIX": "^VIX",
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
    "OIL": "CL=F",
    "GOLD": "GC=F",
}

# Valid ranges for each indicator to detect corrupted cache/data
VALID_RANGES = {
    "VIX": (5, 90),       # VIX above 90 is historically impossible
    "DXY": (70, 130),     # Dollar index realistic range
    "US10Y": (0, 15),     # 10-year yield range
    "OIL": (10, 200),     # Oil price range
    "GOLD": (500, 5000),  # Gold price range
}


def is_valid_indicator_value(name: str, value: float) -> bool:
    """Check if indicator value is within valid range and is not NaN.

    Returns True if value is valid, False if out of range or NaN.
    Logs warnings for invalid values.
    """
    # Check for NaN
    if pd.isna(value) or np.isnan(value):
        logger.warning("Invalid value for %s: NaN detected", name)
        return False

    # Check against valid range
    if name not in VALID_RANGES:
        return True  # If no range defined, accept it

    min_val, max_val = VALID_RANGES[name]
    if not (min_val <= value <= max_val):
        logger.warning(
            "Invalid value for %s: %.2f is outside valid range [%.2f, %.2f]",
            name, value, min_val, max_val
        )
        return False

    return True


def check_all_indicators_same(indicators: dict) -> bool:
    """Check if all indicators return the same value (corrupted cache indicator).

    Returns True if all values are identical (corruption detected), False otherwise.
    """
    if len(indicators) < 2:
        return False

    values = [ind.get("value") for ind in indicators.values()]
    values = [v for v in values if v is not None]

    if not values:
        return False

    # Check if all values are identical
    if all(v == values[0] for v in values):
        logger.warning("All indicators returned same value: %.2f — corrupted data detected", values[0])
        return True

    return False


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


def yahoo_finance_price_history(ticker: str, period: str = "3mo") -> pd.DataFrame:
    """Download price history via yfinance. Returns empty DataFrame on failure.

    Handles MultiIndex columns from yfinance and ensures proper index setup.
    """
    try:
        import yfinance as yf
        data = yf.download(ticker, period=period, auto_adjust=True, progress=False)

        if data.empty:
            return pd.DataFrame()

        # Fix MultiIndex columns (happens when downloading multiple tickers)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
            logger.debug("Flattened MultiIndex columns for %s", ticker)

        return data

    except Exception as e:
        logger.warning("yfinance price history error for %s: %s", ticker, e)
        return pd.DataFrame()


def get_indicator_data(name: str, ticker: str) -> dict | None:
    """Fetch indicator data for a single macro ticker.

    Returns dict with:
    - value: current price
    - change_1d, change_5d, change_20d: percent changes
    - high_20d, low_20d: 20-day range
    - percentile_1y: percentile rank vs 1-year range
    - trend: "rising" | "falling" | "flat"
    - interpretation: human-readable assessment

    Returns None if data fetch fails or value is out of valid range.
    """
    try:
        # Try cache first
        cache_key = make_cache_key("yfinance", f"history_{ticker}", {"period": "3mo"})
        cached = get_cached(cache_key, max_age_hours=24)

        if cached is not None:
            logger.info("Using cached data for %s", name)
            # Handle cached data with _data key
            if "_data" in cached:
                history_dict = cached["_data"]
                history = pd.DataFrame(history_dict)
                # Handle Date column if present from reset_index
                if "Date" in history.columns:
                    history.set_index("Date", inplace=True)
                    history.index = pd.to_datetime(history.index)
                else:
                    history.index = pd.to_datetime(history.index)
            else:
                # Fallback if structure is different
                history = pd.DataFrame(cached)
                history.index = pd.to_datetime(history.index)
        else:
            logger.info("Fetching fresh data for %s (%s)", name, ticker)
            history = yahoo_finance_price_history(ticker, period="3mo")

            if history.empty:
                logger.warning("No data for %s", ticker)
                return None

            # Cache the result (reset_index to include Date column)
            set_cached(cache_key, {"_data": history.reset_index().to_dict('records')})

        if history.empty:
            return None

        # Get Close prices
        close = history["Close"]
        if close.empty:
            return None

        current = float(close.iloc[-1])

        # VALIDATE: Check if current value is within valid range
        if not is_valid_indicator_value(name, current):
            return None

        # Calculate changes
        change_1d = ((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100) if len(close) >= 2 else 0
        change_5d = ((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100) if len(close) >= 5 else 0
        change_20d = ((close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] * 100) if len(close) >= 20 else 0

        # 20-day range
        high_20d = float(history["High"].iloc[-20:].max()) if len(history) >= 20 else float(history["High"].max())
        low_20d = float(history["Low"].iloc[-20:].min()) if len(history) >= 20 else float(history["Low"].min())

        # Percentile vs 1-year range (use available data, cap at ~252 trading days)
        hist_for_percentile = close.iloc[-min(252, len(close)):]
        percentile = 0.0
        if len(hist_for_percentile) > 0:
            hist_min = hist_for_percentile.min()
            hist_max = hist_for_percentile.max()
            if hist_max > hist_min:
                percentile = round((current - hist_min) / (hist_max - hist_min), 2)
            else:
                percentile = 0.5

        # Trend
        if change_5d > 1.0:
            trend = "rising"
        elif change_5d < -1.0:
            trend = "falling"
        else:
            trend = "flat"

        # Interpretation (context-dependent)
        interpretation = _get_interpretation(name, current, change_1d, percentile, trend)

        return {
            "value": round(current, 2),
            "change_1d": round(change_1d, 2),
            "change_5d": round(change_5d, 2),
            "change_20d": round(change_20d, 2),
            "high_20d": round(high_20d, 2),
            "low_20d": round(low_20d, 2),
            "percentile_1y": percentile,
            "trend": trend,
            "interpretation": interpretation,
        }

    except Exception as e:
        logger.error("Error fetching data for %s (%s): %s", name, ticker, e)
        return None


def _get_interpretation(name: str, value: float, change_1d: float, percentile: float, trend: str) -> str:
    """Generate context-specific interpretation for each indicator."""
    if name == "VIX":
        if value > 35:
            return "Extreme fear — panic selling"
        elif value > 25:
            return "Elevated fear — above normal range"
        elif value > 20:
            return "Heightened vigilance — moderately elevated"
        elif value > 15:
            return "Normal volatility range"
        else:
            return "Complacency — very low volatility"

    elif name == "DXY":
        if percentile > 0.75:
            return "Strong dollar — headwind for exporters"
        elif percentile < 0.25:
            return "Weak dollar — favorable for commodities"
        else:
            return "Normal trading range"

    elif name == "US10Y":
        if value > 5.0:
            return "High yields — restrictive environment"
        elif value > 4.0:
            return "Elevated yields — tightening signals"
        elif value > 3.0:
            return "Moderate yields — normal range"
        else:
            return "Low yields — accommodative environment"

    elif name == "OIL":
        if value > 90:
            return "High energy costs — inflation concern"
        elif value > 70:
            return "Elevated oil — watch for inflation"
        else:
            return "Normal energy prices"

    elif name == "GOLD":
        if percentile > 0.75:
            return "Gold near highs — flight to safety"
        elif percentile > 0.50:
            return "Gold elevated — risk-off positioning"
        else:
            return "Gold lower — risk-on sentiment"

    return "Data available"


def determine_signal(indicators: dict) -> str:
    """Determine overall market signal from macro indicators.

    Returns: "crisis" | "risk_off" | "risk_on" | "mixed"
    """
    if not indicators:
        return "mixed"

    vix = indicators.get("VIX", {})
    dxy = indicators.get("DXY", {})
    us10y = indicators.get("US10Y", {})
    oil = indicators.get("OIL", {})
    gold = indicators.get("GOLD", {})

    vix_val = vix.get("value", 0)
    vix_trend = vix.get("trend", "flat")
    gold_trend = gold.get("trend", "flat")
    us10y_trend = us10y.get("trend", "flat")
    dxy_trend = dxy.get("trend", "flat")
    oil_val = oil.get("value", 0)

    # Crisis: extreme VIX
    if vix_val > 35:
        return "crisis"

    # Risk-off: elevated VIX + rising gold + falling yields
    if vix_val > 25 and gold_trend == "rising" and us10y_trend == "falling":
        return "risk_off"

    # Risk-on: low VIX + stable yields + falling DXY
    if vix_val < 15 and us10y_trend in ("flat", "falling") and dxy_trend in ("flat", "falling"):
        return "risk_on"

    # Default: mixed
    return "mixed"


def determine_signal_detail(indicators: dict, signal: str) -> str:
    """Generate a one-sentence explanation of the signal."""
    if not indicators:
        return "Insufficient data to determine signal."

    vix = indicators.get("VIX", {})
    oil = indicators.get("OIL", {})
    us10y = indicators.get("US10Y", {})
    gold = indicators.get("GOLD", {})

    vix_val = vix.get("value", 0)
    oil_val = oil.get("value", 0)
    us10y_val = us10y.get("value", 0)
    gold_trend = gold.get("trend", "flat")

    if signal == "crisis":
        return f"VIX at extreme level ({vix_val:.1f}) — panic conditions."
    elif signal == "risk_off":
        return f"VIX elevated ({vix_val:.1f}) with gold rising — flight to safety."
    elif signal == "risk_on":
        return f"Low VIX ({vix_val:.1f}) with stable yields — favorable for equities."
    else:  # mixed
        if vix_val > 22 and oil_val > 80:
            return f"VIX elevated ({vix_val:.1f}) with rising oil — inflation watch."
        elif us10y_val > 4.5:
            return f"High yields ({us10y_val:.1f}%) with elevated VIX ({vix_val:.1f}) — restrictive environment."
        else:
            return f"Mixed signals: VIX at {vix_val:.1f}, oil at ${oil_val:.1f}, yields at {us10y_val:.2f}%."


def determine_regime_summary(indicators: dict) -> str:
    """Generate a 1-2 sentence regime summary based on indicator combination."""
    if not indicators:
        return "Insufficient data for regime analysis."

    vix = indicators.get("VIX", {})
    dxy = indicators.get("DXY", {})
    us10y = indicators.get("US10Y", {})
    oil = indicators.get("OIL", {})
    gold = indicators.get("GOLD", {})

    vix_val = vix.get("value", 0)
    vix_trend = vix.get("trend", "flat")
    dxy_trend = dxy.get("trend", "flat")
    us10y_trend = us10y.get("trend", "flat")
    us10y_val = us10y.get("value", 0)
    oil_val = oil.get("value", 0)
    oil_trend = oil.get("trend", "flat")
    gold_trend = gold.get("trend", "flat")
    gold_percentile = gold.get("percentile_1y", 0.5)

    # Build sentiment description
    if vix_val > 30:
        mood = "fearful"
    elif vix_val > 20:
        mood = "cautious"
    elif vix_val < 12:
        mood = "complacent"
    else:
        mood = "neutral"

    # Detect common patterns
    if vix_trend == "rising" and gold_trend == "rising" and us10y_trend == "falling":
        return f"Flight to safety — {mood} market is rotating to defensive assets. Rising volatility with gold strength signals risk-off sentiment."

    if vix_val < 15 and us10y_trend in ("flat", "falling") and dxy_trend == "falling":
        return f"Risk-on environment — {mood} market favoring equities. Dollar weakness supports commodities and emerging markets."

    if oil_trend == "rising" and us10y_val > 4.0:
        return f"Inflation concerns — {mood} market watching energy costs and Fed policy. Rising oil and elevated yields suggest hawkish expectations."

    if vix_val > 25 and oil_val < 60:
        return f"Equity-specific fear — {mood} market with flight to quality. High volatility contrasts with soft commodity prices."

    if dxy_trend == "rising" and oil_trend == "falling":
        return f"Risk-off USD strength — {mood} market with strong dollar pressuring commodities. Likely macro headwinds for multinational earnings."

    # Default comprehensive summary
    vix_desc = "high" if vix_val > 20 else "low" if vix_val < 12 else "moderate"
    yield_desc = "elevated" if us10y_val > 4.0 else "moderate" if us10y_val > 3.0 else "low"
    return f"Mixed macro regime with {vix_desc} volatility and {yield_desc} yields. Monitor VIX trend and Fed policy shifts for direction."


def analyze_macro(indicators: dict) -> dict:
    """Perform macro analysis and generate all signal outputs."""
    signal = determine_signal(indicators)
    signal_detail = determine_signal_detail(indicators, signal)
    regime_summary = determine_regime_summary(indicators)

    return {
        "signal": signal,
        "signal_detail": signal_detail,
        "regime_summary": regime_summary,
        "indicators": indicators,
        "data_source": "yfinance",
        "analyzed_at": datetime.now().astimezone().isoformat(),
    }


def main():
    """Main entry point."""
    setup_logging()
    logger.info("=== Building Macro Dashboard ===")

    indicators = {}
    failed_tickers = []

    # Fetch all macro indicators
    for name, ticker in MACRO_TICKERS.items():
        logger.info("Fetching %s (%s)", name, ticker)
        data = get_indicator_data(name, ticker)
        if data is not None:
            indicators[name] = data
            logger.info("  ✓ %s: %.2f", name, data["value"])
        else:
            logger.warning("  ✗ %s failed", name)
            failed_tickers.append(name)

    # CHECK: Detect corrupted data (all indicators same value)
    if check_all_indicators_same(indicators):
        logger.error("Corrupted cache detected: all indicators returned same value. Clearing indicators.")
        indicators = {}
        failed_tickers = list(MACRO_TICKERS.keys())

    if not indicators:
        logger.error("All indicators failed to fetch")
        result_data = {
            "signal": "unknown",
            "signal_detail": "Unable to fetch market data",
            "regime_summary": "Data unavailable",
            "indicators": {},
            "data_source": "yfinance",
        }
        envelope = create_envelope(
            "macro_dashboard",
            result_data,
            status="error",
            error="All indicator fetches failed"
        )
    elif failed_tickers:
        logger.warning("Partial success: %d/%d indicators fetched", len(indicators), len(MACRO_TICKERS))
        result_data = analyze_macro(indicators)
        envelope = create_envelope(
            "macro_dashboard",
            result_data,
            status="partial",
            error=f"Failed to fetch: {', '.join(failed_tickers)}"
        )
    else:
        logger.info("All indicators fetched successfully")
        result_data = analyze_macro(indicators)
        envelope = create_envelope("macro_dashboard", result_data, status="success")

    # Save output
    output_path = save_envelope(envelope, "macro_dashboard.json")
    logger.info("Macro Dashboard complete. Output: %s", output_path)

    # Print to stdout
    print(f"Signal: {envelope['data'].get('signal', 'unknown')}")
    print(f"Regime: {envelope['data'].get('regime_summary', 'N/A')}")

    return output_path


if __name__ == "__main__":
    main()
