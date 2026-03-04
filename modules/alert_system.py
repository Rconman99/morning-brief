"""Alert System: Detects state changes across modules and generates prioritized alerts.

Compares current outputs against alert history to identify critical changes:
- Stop breaches and near-breaches
- Verdict flips
- Risk regime changes
- Technical composite sign changes
- Earnings imminent
- Correlation spikes
- Sentiment shifts
- New opportunities
- Sector rotations
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lib.data_envelope import create_envelope, save_envelope, load_envelope

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


def get_now():
    """Get current time in Eastern timezone as ISO string."""
    try:
        return datetime.now(ZoneInfo("America/New_York")).isoformat()
    except Exception:
        # Fallback if zoneinfo unavailable
        from datetime import timezone, timedelta
        offset = timedelta(hours=-5)
        tz = timezone(offset)
        return datetime.now(tz).isoformat()


def load_alert_history() -> dict:
    """Load alert_history.json or return empty state."""
    path = PROCESSED_DIR / "alert_history.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception as e:
            logger.warning("Failed to load alert_history.json: %s", e)
    return {
        "last_run": None,
        "fired": {},
        "previous_verdicts": {},
        "previous_composites": {},
        "previous_regime": None,
        "previous_sentiments": {},
    }


def is_alert_suppressed(alert_id: str, history: dict, hours: int = 24) -> bool:
    """Check if alert was fired recently enough to suppress."""
    fired = history.get("fired", {})
    if alert_id not in fired:
        return False

    last_time_str = fired[alert_id]
    try:
        last_time = datetime.fromisoformat(last_time_str)
        now = datetime.fromisoformat(get_now())
        age = now - last_time
        return age < timedelta(hours=hours)
    except Exception:
        return False


def extract_verdicts(morning_brief_data: dict) -> dict:
    """Extract verdict verdicts from morning_brief output."""
    verdicts = {}
    watchlist = morning_brief_data.get("watchlist_actions", [])
    for item in watchlist:
        ticker = item.get("ticker")
        verdict = item.get("verdict")
        if ticker and verdict:
            verdicts[ticker] = verdict
    return verdicts


def extract_composites(technical_data: dict) -> dict:
    """Extract composite scores from technical_signals output."""
    composites = {}
    results = technical_data.get("results", [])
    for item in results:
        ticker = item.get("ticker")
        composite = item.get("composite_score")
        if ticker and composite is not None:
            composites[ticker] = composite
    return composites


def extract_sentiments(sentiment_data: dict) -> dict:
    """Extract sentiment scores from news_sentiment output."""
    sentiments = {}
    results = sentiment_data.get("results", [])
    for item in results:
        ticker = item.get("ticker")
        score = item.get("sentiment_score")
        if ticker and score is not None:
            sentiments[ticker] = score
    return sentiments


def get_ticker_earnings_days(ticker: str) -> int | None:
    """Check if ticker has earnings within 2 days. Returns days or None."""
    try:
        import yfinance as yf
        from datetime import date
        t = yf.Ticker(ticker)
        cal = t.calendar
        if cal is None:
            return None

        if isinstance(cal, dict):
            earnings_date = cal.get("Earnings Date")
            if isinstance(earnings_date, list) and earnings_date:
                earnings_date = earnings_date[0]
        else:
            try:
                earnings_date = cal.iloc[0, 0] if not cal.empty else None
            except Exception:
                return None

        if earnings_date is None:
            return None

        if hasattr(earnings_date, "date"):
            earnings_date = earnings_date.date()
        elif isinstance(earnings_date, str):
            from dateutil.parser import parse
            earnings_date = parse(earnings_date).date()

        days = (earnings_date - date.today()).days
        if 0 <= days <= 2:
            return days
    except Exception:
        pass
    return None


def detect_alerts(portfolio_data: dict, risk_data: dict, technical_data: dict,
                  sentiment_data: dict, morning_brief_data: dict,
                  unusual_options_data: dict, opportunities_data: dict,
                  previous_regime: str | None, history: dict) -> list[dict]:
    """Detect all alert conditions. Return list of alert dicts."""
    alerts = []
    now = get_now()

    # ── CRITICAL ALERTS ──

    # 1. STOP_BREACHED
    holdings = portfolio_data.get("holdings", [])
    for h in holdings:
        ticker = h.get("ticker")
        price = h.get("current_price")
        stop = h.get("trailing_stop")
        if ticker and price is not None and stop is not None and price > 0:
            if price < stop:
                alert_id = f"STOP_BREACHED_{ticker}_{get_now()[:10]}"
                if not is_alert_suppressed(alert_id, history):
                    alerts.append({
                        "id": alert_id,
                        "level": "critical",
                        "category": "STOP_BREACHED",
                        "ticker": ticker,
                        "message": f"{ticker} price ${price:.2f} breached trailing stop ${stop:.2f} — EXIT SIGNAL",
                        "timestamp": now,
                        "data": {"price": price, "stop": stop},
                    })

    # 2. REGIME_CHANGE_DANGER
    current_regime = risk_data.get("regime", "NORMAL")
    if current_regime == "DANGER" and previous_regime != "DANGER":
        alert_id = f"REGIME_CHANGE_DANGER_{get_now()[:10]}"
        if not is_alert_suppressed(alert_id, history):
            alerts.append({
                "id": alert_id,
                "level": "critical",
                "category": "REGIME_CHANGE_DANGER",
                "ticker": None,
                "message": f"Risk regime changed to DANGER — reduce position sizes, raise stop losses",
                "timestamp": now,
                "data": {"previous_regime": previous_regime, "current_regime": current_regime},
            })

    # 3. EARNINGS_IMMINENT
    for h in holdings:
        ticker = h.get("ticker")
        if ticker:
            days = get_ticker_earnings_days(ticker)
            if days is not None and 0 <= days <= 2:
                alert_id = f"EARNINGS_IMMINENT_{ticker}_{get_now()[:10]}"
                if not is_alert_suppressed(alert_id, history):
                    alerts.append({
                        "id": alert_id,
                        "level": "critical",
                        "category": "EARNINGS_IMMINENT",
                        "ticker": ticker,
                        "message": f"{ticker} earnings in {days} day(s) — plan exit or hedge strategy",
                        "timestamp": now,
                        "data": {"days_until_earnings": days},
                    })

    # ── HIGH ALERTS ──

    # 4. STOP_CLOSE (within 3%)
    for h in holdings:
        ticker = h.get("ticker")
        price = h.get("current_price")
        stop = h.get("trailing_stop")
        if ticker and price is not None and stop is not None and price > 0:
            pct_to_stop = (price - stop) / price
            if 0 < pct_to_stop <= 0.03:
                alert_id = f"STOP_CLOSE_{ticker}_{get_now()[:10]}"
                if not is_alert_suppressed(alert_id, history):
                    alerts.append({
                        "id": alert_id,
                        "level": "high",
                        "category": "STOP_CLOSE",
                        "ticker": ticker,
                        "message": f"{ticker} stop ${stop:.2f} is within 3% of price ${price:.2f} — prepare exit",
                        "timestamp": now,
                        "data": {"price": price, "stop": stop, "pct_to_stop": pct_to_stop},
                    })

    # 5. VERDICT_FLIP
    previous_verdicts = history.get("previous_verdicts", {})
    current_verdicts = extract_verdicts(morning_brief_data)
    for ticker in current_verdicts:
        prev_verdict = previous_verdicts.get(ticker)
        curr_verdict = current_verdicts[ticker]
        if prev_verdict and prev_verdict != curr_verdict:
            alert_id = f"VERDICT_FLIP_{ticker}_{get_now()[:10]}"
            if not is_alert_suppressed(alert_id, history):
                alerts.append({
                    "id": alert_id,
                    "level": "high",
                    "category": "VERDICT_FLIP",
                    "ticker": ticker,
                    "message": f"{ticker} verdict changed {prev_verdict} → {curr_verdict}",
                    "timestamp": now,
                    "data": {"previous_verdict": prev_verdict, "current_verdict": curr_verdict},
                })

    # 6. COMPOSITE_FLIP
    previous_composites = history.get("previous_composites", {})
    current_composites = extract_composites(technical_data)
    for ticker in current_composites:
        prev_composite = previous_composites.get(ticker)
        curr_composite = current_composites[ticker]
        if prev_composite is not None and curr_composite is not None:
            prev_sign = 1 if prev_composite > 0 else (-1 if prev_composite < 0 else 0)
            curr_sign = 1 if curr_composite > 0 else (-1 if curr_composite < 0 else 0)
            if prev_sign != 0 and curr_sign != 0 and prev_sign != curr_sign:
                alert_id = f"COMPOSITE_FLIP_{ticker}_{get_now()[:10]}"
                if not is_alert_suppressed(alert_id, history):
                    alerts.append({
                        "id": alert_id,
                        "level": "high",
                        "category": "COMPOSITE_FLIP",
                        "ticker": ticker,
                        "message": f"{ticker} technical composite flipped {prev_composite:.1f} → {curr_composite:.1f}",
                        "timestamp": now,
                        "data": {"previous_composite": prev_composite, "current_composite": curr_composite},
                    })

    # 7. UNUSUAL_OPTIONS_SURGE
    unusual_opts = unusual_options_data.get("unusual_options", [])
    if unusual_opts:
        for item in unusual_opts:
            ticker = item.get("ticker")
            signal = item.get("signal")
            if ticker and signal in ["call_surge", "put_surge"]:
                alert_id = f"UNUSUAL_OPTIONS_{ticker}_{get_now()[:10]}"
                if not is_alert_suppressed(alert_id, history):
                    msg_map = {
                        "call_surge": f"{ticker} unusual call volume surge detected",
                        "put_surge": f"{ticker} unusual put volume surge detected",
                    }
                    alerts.append({
                        "id": alert_id,
                        "level": "high",
                        "category": "UNUSUAL_OPTIONS_SURGE",
                        "ticker": ticker,
                        "message": msg_map.get(signal, f"{ticker} unusual options detected"),
                        "timestamp": now,
                        "data": {"signal": signal, "detail": item.get("detail")},
                    })

    # ── MEDIUM ALERTS ──

    # 8. REGIME_CHANGE (any, not to DANGER)
    if current_regime != previous_regime and current_regime != "DANGER":
        alert_id = f"REGIME_CHANGE_{get_now()[:10]}"
        if not is_alert_suppressed(alert_id, history, hours=24):
            alerts.append({
                "id": alert_id,
                "level": "medium",
                "category": "REGIME_CHANGE",
                "ticker": None,
                "message": f"Risk regime changed {previous_regime} → {current_regime}",
                "timestamp": now,
                "data": {"previous_regime": previous_regime, "current_regime": current_regime},
            })

    # 9. CORRELATION_SPIKE
    previous_fired = history.get("fired", {})
    correlations = portfolio_data.get("correlations", {})
    for pair_str, corr_val in correlations.items():
        if isinstance(corr_val, dict):
            corr_val = corr_val.get("correlation")
        if corr_val is not None and corr_val > 0.85:
            alert_id = f"CORRELATION_SPIKE_{pair_str}_{get_now()[:10]}"
            if alert_id not in previous_fired:
                alerts.append({
                    "id": alert_id,
                    "level": "medium",
                    "category": "CORRELATION_SPIKE",
                    "ticker": pair_str,
                    "message": f"{pair_str} correlation {corr_val:.2f} — positions move in sync, diversification risk",
                    "timestamp": now,
                    "data": {"correlation": corr_val},
                })

    # 10. SENTIMENT_SHIFT
    previous_sentiments = history.get("previous_sentiments", {})
    current_sentiments = extract_sentiments(sentiment_data)
    for ticker in current_sentiments:
        prev_sent = previous_sentiments.get(ticker)
        curr_sent = current_sentiments[ticker]
        if prev_sent is not None and curr_sent is not None:
            prev_sign = 1 if prev_sent > 0 else (-1 if prev_sent < 0 else 0)
            curr_sign = 1 if curr_sent > 0 else (-1 if curr_sent < 0 else 0)
            if prev_sign != 0 and curr_sign != 0 and prev_sign != curr_sign:
                alert_id = f"SENTIMENT_SHIFT_{ticker}_{get_now()[:10]}"
                if not is_alert_suppressed(alert_id, history):
                    alerts.append({
                        "id": alert_id,
                        "level": "medium",
                        "category": "SENTIMENT_SHIFT",
                        "ticker": ticker,
                        "message": f"{ticker} sentiment shifted {prev_sent:.2f} → {curr_sent:.2f}",
                        "timestamp": now,
                        "data": {"previous_sentiment": prev_sent, "current_sentiment": curr_sent},
                    })

    # ── LOW ALERTS ──

    # 11. NEW_OPPORTUNITY
    opps = opportunities_data.get("opportunities", [])
    if opps:
        for opp in opps:
            ticker = opp.get("ticker")
            alert_id = f"NEW_OPPORTUNITY_{ticker}_{get_now()[:10]}"
            if alert_id not in previous_fired:
                alerts.append({
                    "id": alert_id,
                    "level": "low",
                    "category": "NEW_OPPORTUNITY",
                    "ticker": ticker,
                    "message": f"Opportunity scanner flagged {ticker}: {opp.get('reason', 'Strong technicals')}",
                    "timestamp": now,
                    "data": {"composite_score": opp.get("composite_score"), "reason": opp.get("reason")},
                })

    # 12. SECTOR_ROTATION
    sector_read = opportunities_data.get("sector_read", {})
    for sector_ticker, data in sector_read.items():
        trend = data.get("trend")
        alert_id = f"SECTOR_ROTATION_{sector_ticker}_{get_now()[:10]}"
        if trend in ["bullish", "bearish"] and alert_id not in previous_fired:
            alerts.append({
                "id": alert_id,
                "level": "low",
                "category": "SECTOR_ROTATION",
                "ticker": sector_ticker,
                "message": f"{sector_ticker} sector rotation signal: {trend}",
                "timestamp": now,
                "data": {"trend": trend, "change_1d": data.get("change_1d"), "change_5d": data.get("change_5d")},
            })

    return alerts


def main():
    setup_logging()
    logger.info("=== Alert System Module ===")

    # Load all current envelopes
    portfolio_env = load_envelope("portfolio.json")
    risk_env = load_envelope("risk_dashboard.json")
    technical_env = load_envelope("technical_signals.json")
    sentiment_env = load_envelope("news_sentiment.json")
    morning_brief_env = load_envelope("morning_brief.json")
    unusual_opts_env = load_envelope("unusual_options.json")
    opportunities_env = load_envelope("opportunity_scanner.json")

    # Load alert history
    history = load_alert_history()
    previous_regime = history.get("previous_regime")

    # Extract data dicts
    portfolio_data = portfolio_env.get("data", {})
    risk_data = risk_env.get("data", {})
    technical_data = technical_env.get("data", {})
    sentiment_data = sentiment_env.get("data", {})
    morning_brief_data = morning_brief_env.get("data", {})
    unusual_opts_data = unusual_opts_env.get("data", {})
    opportunities_data = opportunities_env.get("data", {})

    # Detect alerts
    alerts = detect_alerts(
        portfolio_data, risk_data, technical_data, sentiment_data,
        morning_brief_data, unusual_opts_data, opportunities_data,
        previous_regime, history
    )

    # Count by level
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for alert in alerts:
        level = alert.get("level", "low")
        counts[level] += 1

    logger.info("Detected %d critical, %d high, %d medium, %d low alerts",
                counts["critical"], counts["high"], counts["medium"], counts["low"])

    # Persist alert_history
    now = get_now()
    new_history = {
        "last_run": now,
        "fired": history.get("fired", {}),
        "previous_verdicts": extract_verdicts(morning_brief_data),
        "previous_composites": extract_composites(technical_data),
        "previous_regime": risk_data.get("regime", "NORMAL"),
        "previous_sentiments": extract_sentiments(sentiment_data),
    }
    # Add newly fired alerts to history
    for alert in alerts:
        new_history["fired"][alert["id"]] = alert["timestamp"]

    history_path = PROCESSED_DIR / "alert_history.json"
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(new_history, indent=2, default=str))
    logger.info("Saved alert history to %s", history_path)

    # Create output envelope
    result = {
        "alerts": alerts,
        "summary": {
            "critical": counts["critical"],
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
            "total": len(alerts),
            "suppressed": len(history.get("fired", {})) - len(new_history.get("fired", {})),
        },
        "previous_regime": previous_regime,
        "current_regime": risk_data.get("regime", "NORMAL"),
    }

    status = "success" if alerts or not portfolio_data else "success"
    error = None

    envelope = create_envelope("alert_system", result, status=status, error=error)
    save_envelope(envelope, "alert_system.json")
    logger.info("Alert system analysis complete")


if __name__ == "__main__":
    main()
