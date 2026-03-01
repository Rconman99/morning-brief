"""Risk Dashboard: assesses risk at NOW/SHORT/LONG horizons with regime classification."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime, date

from lib.data_envelope import create_envelope, save_envelope, load_envelope
from lib.api import yahoo_finance_price_history

logger = logging.getLogger(__name__)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


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


def load_settings() -> dict:
    """Load risk thresholds from settings.json."""
    path = PROJECT_ROOT / "config" / "settings.json"
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def get_vix() -> float | None:
    """Fetch current VIX level."""
    try:
        df = yahoo_finance_price_history("^VIX", period="5d")
        if not df.empty:
            return round(float(df["Close"].iloc[-1]), 2)
    except Exception as e:
        logger.warning("Failed to fetch VIX: %s", e)
    return None


def get_ten_year_yield() -> tuple[float | None, float | None]:
    """Fetch current and 30-day-ago 10Y yield. Returns (current, 30d_ago)."""
    try:
        df = yahoo_finance_price_history("^TNX", period="3mo")
        if not df.empty:
            current = round(float(df["Close"].iloc[-1]), 3)
            ago_30d = round(float(df["Close"].iloc[-22]), 3) if len(df) >= 22 else round(float(df["Close"].iloc[0]), 3)
            return current, ago_30d
    except Exception as e:
        logger.warning("Failed to fetch 10Y yield: %s", e)
    return None, None


def get_earnings_dates(ticker: str) -> int | None:
    """Check if earnings are within 5 days. Returns days until earnings or None."""
    try:
        import yfinance as yf
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return None
        # Calendar can be a dict or DataFrame
        if isinstance(cal, dict):
            earnings_date = cal.get("Earnings Date")
            if isinstance(earnings_date, list) and earnings_date:
                earnings_date = earnings_date[0]
        else:
            # DataFrame format
            try:
                earnings_date = cal.iloc[0, 0] if not cal.empty else None
            except Exception:
                return None

        if earnings_date is None:
            return None

        # Convert to date if it's a timestamp
        if hasattr(earnings_date, "date"):
            earnings_date = earnings_date.date()
        elif isinstance(earnings_date, str):
            from dateutil.parser import parse
            earnings_date = parse(earnings_date).date()

        days = (earnings_date - date.today()).days
        if 0 <= days <= 5:
            return days
    except Exception:
        pass
    return None


def assess_now_risks(portfolio_data: dict, sentiment_data: dict,
                     settings: dict, vix: float | None) -> list[dict]:
    """Assess immediate (today) risks."""
    risks = []
    vix_high = settings.get("risk_vix_high", 20)
    vix_extreme = settings.get("risk_vix_extreme", 30)
    concentration_threshold = settings.get("risk_concentration_threshold", 0.40)
    correlation_alert = settings.get("risk_correlation_alert", 0.85)
    stop_imminent = settings.get("risk_stop_imminent_pct", 0.03)
    stop_close = settings.get("risk_stop_close_pct", 0.07)

    # 1. VIX level
    if vix is not None:
        if vix > vix_extreme:
            risks.append({"risk": "VIX_EXTREME", "level": "high",
                          "detail": f"VIX at {vix:.1f} — extreme fear, expect wild swings"})
        elif vix > vix_high:
            risks.append({"risk": "VIX_ELEVATED", "level": "medium",
                          "detail": f"VIX at {vix:.1f} — elevated volatility"})

    # 2. Portfolio concentration
    holdings = portfolio_data.get("holdings", [])
    total_value = sum(
        (h.get("current_price", 0) or 0) * (h.get("shares", 0) or 0)
        for h in holdings
    )
    if total_value > 0:
        for h in holdings:
            price = h.get("current_price", 0) or 0
            shares = h.get("shares", 0) or 0
            weight = (price * shares) / total_value
            if weight > concentration_threshold:
                risks.append({"risk": "CONCENTRATION", "level": "high",
                              "detail": f"{h['ticker']} is {weight:.0%} of portfolio"})

    # 3. Correlation clustering
    correlations = portfolio_data.get("correlations", {})
    for pair, corr_data in correlations.items():
        corr_val = None
        if isinstance(corr_data, dict):
            corr_val = corr_data.get("correlation")
        elif isinstance(corr_data, (int, float)):
            corr_val = corr_data
        if corr_val is not None and corr_val > correlation_alert:
            risks.append({"risk": "CORRELATED_PAIR", "level": "medium",
                          "detail": f"{pair} correlation {corr_val:.2f} — moves in sync"})

    # 4. Stop proximity
    for h in holdings:
        stop = h.get("trailing_stop")
        price = h.get("current_price")
        if stop and price and price > 0:
            distance_pct = (price - stop) / price
            if distance_pct < stop_imminent:
                risks.append({"risk": "STOP_IMMINENT", "level": "high",
                              "detail": f"{h['ticker']} only {distance_pct:.1%} above trailing stop"})
            elif distance_pct < stop_close:
                risks.append({"risk": "STOP_CLOSE", "level": "medium",
                              "detail": f"{h['ticker']} {distance_pct:.1%} above trailing stop"})

    # 5. Sentiment shock
    for s in sentiment_data.get("results", []):
        score = s.get("sentiment_score")
        if score is not None and score < -0.7:
            risks.append({"risk": "SENTIMENT_SHOCK", "level": "high",
                          "detail": f"{s['ticker']} sentiment {score:.2f} — very negative news"})

    return risks


def assess_short_risks(technical_data: dict, options_data: dict,
                       portfolio_data: dict) -> list[dict]:
    """Assess short-term (1-5 day) risks."""
    risks = []
    tech_results = technical_data.get("results", [])
    holdings = portfolio_data.get("holdings", [])

    # 1. Options expiry / max pain magnet
    for opt in options_data.get("tickers", []):
        if opt.get("skipped"):
            continue
        front_expiry = opt.get("front_month_expiry")
        max_pain = opt.get("max_pain")
        ticker = opt.get("ticker")
        if front_expiry and max_pain and ticker:
            try:
                from dateutil.parser import parse
                days_to_expiry = (parse(front_expiry).date() - date.today()).days
            except Exception:
                days_to_expiry = 999

            if days_to_expiry <= 5:
                # Find current price
                current_price = None
                for h in holdings:
                    if h.get("ticker") == ticker:
                        current_price = h.get("current_price")
                        break
                if current_price and current_price > 0:
                    distance = abs(current_price - max_pain) / current_price
                    if distance > 0.03:
                        direction = "down" if current_price > max_pain else "up"
                        risks.append({"risk": "MAX_PAIN_PULL", "level": "medium",
                                      "detail": f"{ticker} max pain ${max_pain} — may pull {direction} {distance:.1%} by Friday"})

    # 2. Overbought / oversold conditions
    for t in tech_results:
        rsi = t.get("rsi_14")
        ticker = t.get("ticker", "")
        if rsi and rsi > 75:
            risks.append({"risk": "OVERBOUGHT", "level": "medium",
                          "detail": f"{ticker} RSI {rsi:.0f} — pullback likely within days"})
        elif rsi and rsi < 25:
            risks.append({"risk": "OVERSOLD_BOUNCE", "level": "low",
                          "detail": f"{ticker} RSI {rsi:.0f} — bounce candidate"})

    # 3. Bollinger squeeze
    for t in tech_results:
        if t.get("bb_position") == "squeeze":
            risks.append({"risk": "BB_SQUEEZE", "level": "medium",
                          "detail": f"{t['ticker']} in Bollinger squeeze — breakout imminent, direction uncertain"})

    # 4. Earnings approaching
    all_tickers = set()
    for h in holdings:
        all_tickers.add(h.get("ticker", ""))
    for t in tech_results:
        all_tickers.add(t.get("ticker", ""))
    all_tickers.discard("")

    for ticker in all_tickers:
        days = get_earnings_dates(ticker)
        if days is not None:
            risks.append({"risk": "EARNINGS_IMMINENT", "level": "high",
                          "detail": f"{ticker} earnings in {days} days — expect IV crush or gap"})

    return risks


def assess_long_risks(technical_data: dict, earnings_data: dict,
                      ten_year_yield: float | None, ten_year_yield_30d: float | None) -> list[dict]:
    """Assess longer-term (weeks/months) risks."""
    risks = []

    # 1. Death cross on any ticker
    for t in technical_data.get("results", []):
        if t.get("sma_trend") == "death_cross":
            risks.append({"risk": "DEATH_CROSS", "level": "high",
                          "detail": f"{t['ticker']} 50-day SMA crossed below 200-day — bearish trend"})

    # 2. Rising rates
    if ten_year_yield is not None and ten_year_yield_30d is not None:
        rate_change = ten_year_yield - ten_year_yield_30d
        if rate_change > 0.25:
            risks.append({"risk": "RISING_RATES", "level": "medium",
                          "detail": f"10Y yield up {rate_change:.2f}% in 30 days — headwind for growth stocks"})

    # 3. Weak earnings tone
    for e in earnings_data.get("results", []):
        tone = e.get("tone_score")
        if tone is not None and tone < 0:
            risks.append({"risk": "WEAK_FUNDAMENTALS", "level": "medium",
                          "detail": f"{e['ticker']} earnings tone {tone:.1f} — management hedging"})

    return risks


def calculate_regime(now_risks: list, short_risks: list, long_risks: list) -> tuple[float, float, float, float, str, str]:
    """Calculate aggregate risk scores and determine regime.

    Returns (now_score, short_score, long_score, total_risk, regime, regime_note).
    """
    risk_weights = {"high": 3, "medium": 1, "low": 0.5}

    now_score = sum(risk_weights.get(r["level"], 0) for r in now_risks)
    short_score = sum(risk_weights.get(r["level"], 0) for r in short_risks)
    long_score = sum(risk_weights.get(r["level"], 0) for r in long_risks)
    total_risk = round(now_score + short_score * 0.7 + long_score * 0.4, 1)

    if total_risk > 15:
        regime = "DANGER"
        regime_note = "Multiple high-severity risks active — reduce exposure or tighten stops"
    elif total_risk > 8:
        regime = "CAUTION"
        regime_note = "Elevated risk — be selective, smaller positions"
    elif total_risk > 3:
        regime = "NORMAL"
        regime_note = "Standard risk environment — trade your plan"
    else:
        regime = "LOW_RISK"
        regime_note = "Favorable conditions — lean into high-conviction setups"

    return now_score, short_score, long_score, total_risk, regime, regime_note


def analyze_risk(portfolio_data: dict = None, technical_data: dict = None,
                 sentiment_data: dict = None, options_data: dict = None,
                 earnings_data: dict = None, processed_dir: Path = None) -> dict:
    """Run full risk assessment across all horizons.

    If data dicts are not provided, loads from processed envelopes.
    """
    pdir = processed_dir or PROCESSED_DIR

    # Load data from envelopes if not provided
    if portfolio_data is None or technical_data is None or sentiment_data is None or options_data is None or earnings_data is None:
        from lib import data_envelope
        original_dir = data_envelope.PROCESSED_DIR
        data_envelope.PROCESSED_DIR = pdir

        if portfolio_data is None:
            portfolio_data = load_envelope("portfolio.json").get("data", {})
        if technical_data is None:
            technical_data = load_envelope("technical_signals.json").get("data", {})
        if sentiment_data is None:
            sentiment_data = load_envelope("news_sentiment.json").get("data", {})
        if options_data is None:
            options_data = load_envelope("options.json").get("data", {})
        if earnings_data is None:
            earnings_data = load_envelope("earnings_tone.json").get("data", {})

        data_envelope.PROCESSED_DIR = original_dir

    settings = load_settings()

    # Fetch market context
    vix = get_vix()
    ten_year_yield, ten_year_yield_30d = get_ten_year_yield()

    # Assess risks by horizon
    now_risks = assess_now_risks(portfolio_data, sentiment_data, settings, vix)
    short_risks = assess_short_risks(technical_data, options_data, portfolio_data)
    long_risks = assess_long_risks(technical_data, earnings_data, ten_year_yield, ten_year_yield_30d)

    # Calculate regime
    now_score, short_score, long_score, total_risk, regime, regime_note = \
        calculate_regime(now_risks, short_risks, long_risks)

    logger.info("Risk regime: %s (total=%.1f, now=%.1f, short=%.1f, long=%.1f)",
                regime, total_risk, now_score, short_score, long_score)

    return {
        "now_risks": now_risks,
        "short_risks": short_risks,
        "long_risks": long_risks,
        "now_score": now_score,
        "short_score": short_score,
        "long_score": long_score,
        "total_risk": total_risk,
        "regime": regime,
        "regime_note": regime_note,
        "vix": vix,
        "ten_year_yield": ten_year_yield,
    }


def main():
    setup_logging()
    logger.info("=== Risk Dashboard Module ===")

    data = analyze_risk()
    status = "success"
    error = None

    envelope = create_envelope("risk_dashboard", data, status=status, error=error)
    save_envelope(envelope, "risk_dashboard.json")
    logger.info("Risk dashboard complete: regime=%s, total_risk=%.1f",
                data["regime"], data["total_risk"])


if __name__ == "__main__":
    main()
