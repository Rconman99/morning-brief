"""Tests for alert_system module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from modules.alert_system import (
    detect_alerts, load_alert_history, is_alert_suppressed, extract_verdicts,
    extract_composites, extract_sentiments, get_now, main,
)


def make_now():
    """Helper to get current ISO timestamp."""
    from zoneinfo import ZoneInfo
    try:
        return datetime.now(ZoneInfo("America/New_York")).isoformat()
    except Exception:
        from datetime import timezone, timedelta
        offset = timedelta(hours=-5)
        tz = timezone(offset)
        return datetime.now(tz).isoformat()


def test_extract_verdicts():
    """Test extracting verdicts from morning_brief data."""
    data = {
        "watchlist_actions": [
            {"ticker": "NVDA", "verdict": "BUY"},
            {"ticker": "AAPL", "verdict": "HOLD"},
            {"ticker": "MSFT", "verdict": "SELL"},
        ]
    }
    verdicts = extract_verdicts(data)
    assert verdicts["NVDA"] == "BUY"
    assert verdicts["AAPL"] == "HOLD"
    assert verdicts["MSFT"] == "SELL"


def test_extract_verdicts_empty():
    """Test extracting verdicts from empty data."""
    verdicts = extract_verdicts({})
    assert verdicts == {}


def test_extract_composites():
    """Test extracting composite scores from technical data."""
    data = {
        "results": [
            {"ticker": "NVDA", "composite_score": 2.5},
            {"ticker": "AAPL", "composite_score": -1.2},
        ]
    }
    composites = extract_composites(data)
    assert composites["NVDA"] == 2.5
    assert composites["AAPL"] == -1.2


def test_extract_sentiments():
    """Test extracting sentiment scores."""
    data = {
        "results": [
            {"ticker": "NVDA", "sentiment_score": 0.6},
            {"ticker": "AAPL", "sentiment_score": -0.3},
        ]
    }
    sentiments = extract_sentiments(data)
    assert sentiments["NVDA"] == 0.6
    assert sentiments["AAPL"] == -0.3


def test_is_alert_suppressed_not_fired():
    """Test that new alerts are not suppressed."""
    history = {"fired": {}}
    assert not is_alert_suppressed("NEW_ALERT_123", history)


def test_is_alert_suppressed_old_alert():
    """Test that old alerts (>24h) are not suppressed."""
    old_time = (datetime.fromisoformat(make_now()) - timedelta(hours=25)).isoformat()
    history = {"fired": {"ALERT_1": old_time}}
    assert not is_alert_suppressed("ALERT_1", history, hours=24)


def test_is_alert_suppressed_recent_alert():
    """Test that recent alerts (<24h) are suppressed."""
    recent_time = (datetime.fromisoformat(make_now()) - timedelta(hours=2)).isoformat()
    history = {"fired": {"ALERT_1": recent_time}}
    assert is_alert_suppressed("ALERT_1", history, hours=24)


def test_stop_breached_alert():
    """Test STOP_BREACHED alert fires when price < stop."""
    portfolio = {
        "holdings": [
            {"ticker": "NVDA", "current_price": 850.0, "trailing_stop": 880.0},
        ]
    }
    history = {"fired": {}, "previous_verdicts": {}, "previous_composites": {}, "previous_sentiments": {}}
    alerts = detect_alerts(portfolio, {}, {}, {}, {}, {}, {}, None, history)

    critical_alerts = [a for a in alerts if a["level"] == "critical" and a["category"] == "STOP_BREACHED"]
    assert len(critical_alerts) > 0
    assert "NVDA" in critical_alerts[0]["message"]
    assert "breached" in critical_alerts[0]["message"].lower()


def test_stop_breached_suppressed():
    """Test STOP_BREACHED alert is suppressed if fired recently."""
    today = make_now()[:10]
    alert_id = f"STOP_BREACHED_NVDA_{today}"
    recent_time = make_now()

    portfolio = {
        "holdings": [
            {"ticker": "NVDA", "current_price": 850.0, "trailing_stop": 880.0},
        ]
    }
    history = {
        "fired": {alert_id: recent_time},
        "previous_verdicts": {},
        "previous_composites": {},
        "previous_sentiments": {},
    }
    alerts = detect_alerts(portfolio, {}, {}, {}, {}, {}, {}, None, history)

    critical_alerts = [a for a in alerts if a["level"] == "critical" and a["category"] == "STOP_BREACHED"]
    assert len(critical_alerts) == 0, "Alert should be suppressed"


def test_stop_close_alert():
    """Test STOP_CLOSE alert fires when stop is within 3% of price."""
    portfolio = {
        "holdings": [
            {"ticker": "NVDA", "current_price": 900.0, "trailing_stop": 873.0},  # ~2.7% gap
        ]
    }
    history = {"fired": {}, "previous_verdicts": {}, "previous_composites": {}, "previous_sentiments": {}}
    alerts = detect_alerts(portfolio, {}, {}, {}, {}, {}, {}, None, history)

    high_alerts = [a for a in alerts if a["level"] == "high" and a["category"] == "STOP_CLOSE"]
    assert len(high_alerts) > 0
    assert "NVDA" in high_alerts[0]["message"]


def test_stop_close_not_triggered():
    """Test STOP_CLOSE alert does not fire when stop is >3% away."""
    portfolio = {
        "holdings": [
            {"ticker": "NVDA", "current_price": 900.0, "trailing_stop": 850.0},  # 5.6% gap
        ]
    }
    history = {"fired": {}, "previous_verdicts": {}, "previous_composites": {}, "previous_sentiments": {}}
    alerts = detect_alerts(portfolio, {}, {}, {}, {}, {}, {}, None, history)

    close_alerts = [a for a in alerts if a["category"] == "STOP_CLOSE"]
    assert len(close_alerts) == 0


def test_verdict_flip_alert():
    """Test VERDICT_FLIP alert when verdict changes."""
    morning_brief = {
        "watchlist_actions": [
            {"ticker": "NVDA", "verdict": "SELL"},
        ]
    }
    history = {
        "fired": {},
        "previous_verdicts": {"NVDA": "BUY"},
        "previous_composites": {},
        "previous_sentiments": {},
    }
    alerts = detect_alerts({}, {}, {}, {}, morning_brief, {}, {}, None, history)

    flip_alerts = [a for a in alerts if a["category"] == "VERDICT_FLIP"]
    assert len(flip_alerts) > 0
    assert "BUY" in flip_alerts[0]["message"]
    assert "SELL" in flip_alerts[0]["message"]


def test_verdict_flip_not_triggered():
    """Test VERDICT_FLIP does not fire if verdict unchanged."""
    morning_brief = {
        "watchlist_actions": [
            {"ticker": "NVDA", "verdict": "BUY"},
        ]
    }
    history = {
        "fired": {},
        "previous_verdicts": {"NVDA": "BUY"},
        "previous_composites": {},
        "previous_sentiments": {},
    }
    alerts = detect_alerts({}, {}, {}, {}, morning_brief, {}, {}, None, history)

    flip_alerts = [a for a in alerts if a["category"] == "VERDICT_FLIP"]
    assert len(flip_alerts) == 0


def test_composite_flip_alert():
    """Test COMPOSITE_FLIP alert when technical score changes sign."""
    technical = {
        "results": [
            {"ticker": "NVDA", "composite_score": -2.5},
        ]
    }
    history = {
        "fired": {},
        "previous_verdicts": {},
        "previous_composites": {"NVDA": 1.5},  # Was positive
        "previous_sentiments": {},
    }
    alerts = detect_alerts({}, {}, technical, {}, {}, {}, {}, None, history)

    flip_alerts = [a for a in alerts if a["category"] == "COMPOSITE_FLIP"]
    assert len(flip_alerts) > 0
    assert "NVDA" in flip_alerts[0]["message"]


def test_composite_flip_not_triggered():
    """Test COMPOSITE_FLIP does not fire if same sign."""
    technical = {
        "results": [
            {"ticker": "NVDA", "composite_score": 1.5},
        ]
    }
    history = {
        "fired": {},
        "previous_verdicts": {},
        "previous_composites": {"NVDA": 2.5},  # Also positive
        "previous_sentiments": {},
    }
    alerts = detect_alerts({}, {}, technical, {}, {}, {}, {}, None, history)

    flip_alerts = [a for a in alerts if a["category"] == "COMPOSITE_FLIP"]
    assert len(flip_alerts) == 0


def test_regime_change_danger_alert():
    """Test REGIME_CHANGE_DANGER alert when regime becomes DANGER."""
    risk = {"regime": "DANGER"}
    history = {"fired": {}, "previous_verdicts": {}, "previous_composites": {}, "previous_sentiments": {}}
    alerts = detect_alerts({}, risk, {}, {}, {}, {}, {}, "NORMAL", history)

    danger_alerts = [a for a in alerts if a["category"] == "REGIME_CHANGE_DANGER"]
    assert len(danger_alerts) > 0
    assert "DANGER" in danger_alerts[0]["message"]


def test_regime_change_normal_alert():
    """Test REGIME_CHANGE alert for non-DANGER regime changes."""
    risk = {"regime": "CAUTION"}
    history = {"fired": {}, "previous_verdicts": {}, "previous_composites": {}, "previous_sentiments": {}}
    alerts = detect_alerts({}, risk, {}, {}, {}, {}, {}, "NORMAL", history)

    change_alerts = [a for a in alerts if a["category"] == "REGIME_CHANGE" and a["level"] == "medium"]
    assert len(change_alerts) > 0
    assert "CAUTION" in change_alerts[0]["message"]


def test_correlation_spike_alert():
    """Test CORRELATION_SPIKE alert when pair correlation > 0.85."""
    portfolio = {
        "correlations": {
            "NVDA_AAPL": 0.88,
        }
    }
    history = {"fired": {}, "previous_verdicts": {}, "previous_composites": {}, "previous_sentiments": {}}
    alerts = detect_alerts(portfolio, {}, {}, {}, {}, {}, {}, None, history)

    corr_alerts = [a for a in alerts if a["category"] == "CORRELATION_SPIKE"]
    assert len(corr_alerts) > 0
    assert "0.88" in corr_alerts[0]["message"]


def test_correlation_spike_not_triggered():
    """Test CORRELATION_SPIKE does not fire for < 0.85 correlation."""
    portfolio = {
        "correlations": {
            "NVDA_AAPL": 0.72,
        }
    }
    history = {"fired": {}, "previous_verdicts": {}, "previous_composites": {}, "previous_sentiments": {}}
    alerts = detect_alerts(portfolio, {}, {}, {}, {}, {}, {}, None, history)

    corr_alerts = [a for a in alerts if a["category"] == "CORRELATION_SPIKE"]
    assert len(corr_alerts) == 0


def test_sentiment_shift_alert():
    """Test SENTIMENT_SHIFT alert when sentiment changes sign."""
    sentiment = {
        "results": [
            {"ticker": "NVDA", "sentiment_score": -0.4},
        ]
    }
    history = {
        "fired": {},
        "previous_verdicts": {},
        "previous_composites": {},
        "previous_sentiments": {"NVDA": 0.5},  # Was positive
    }
    alerts = detect_alerts({}, {}, {}, sentiment, {}, {}, {}, None, history)

    sent_alerts = [a for a in alerts if a["category"] == "SENTIMENT_SHIFT"]
    assert len(sent_alerts) > 0
    assert "NVDA" in sent_alerts[0]["message"]


def test_unusual_options_alert():
    """Test UNUSUAL_OPTIONS_SURGE alert."""
    unusual_opts = {
        "unusual_options": [
            {"ticker": "NVDA", "signal": "call_surge", "detail": "500k call volume"},
        ]
    }
    history = {"fired": {}, "previous_verdicts": {}, "previous_composites": {}, "previous_sentiments": {}}
    alerts = detect_alerts({}, {}, {}, {}, {}, unusual_opts, {}, None, history)

    opt_alerts = [a for a in alerts if a["category"] == "UNUSUAL_OPTIONS_SURGE"]
    assert len(opt_alerts) > 0
    assert "NVDA" in opt_alerts[0]["message"]


def test_new_opportunity_alert():
    """Test NEW_OPPORTUNITY alert."""
    opportunities = {
        "opportunities": [
            {"ticker": "AVGO", "reason": "Strong technicals, bullish MACD"},
        ]
    }
    history = {"fired": {}, "previous_verdicts": {}, "previous_composites": {}, "previous_sentiments": {}}
    alerts = detect_alerts({}, {}, {}, {}, {}, {}, opportunities, None, history)

    opp_alerts = [a for a in alerts if a["category"] == "NEW_OPPORTUNITY"]
    assert len(opp_alerts) > 0
    assert "AVGO" in opp_alerts[0]["message"]


def test_sector_rotation_alert():
    """Test SECTOR_ROTATION alert."""
    opportunities = {
        "sector_read": {
            "XLK": {"trend": "bullish", "change_1d": 1.2, "change_5d": 3.5},
        }
    }
    history = {"fired": {}, "previous_verdicts": {}, "previous_composites": {}, "previous_sentiments": {}}
    alerts = detect_alerts({}, {}, {}, {}, {}, {}, opportunities, None, history)

    sector_alerts = [a for a in alerts if a["category"] == "SECTOR_ROTATION"]
    assert len(sector_alerts) > 0
    assert "bullish" in sector_alerts[0]["message"]


@patch("modules.alert_system.load_envelope")
@patch("modules.alert_system.save_envelope")
@patch("modules.alert_system.load_alert_history")
def test_main_creates_output_envelope(mock_load_hist, mock_save, mock_load):
    """Test main() creates valid alert_system.json envelope."""
    # Mock envelope loads
    def load_side_effect(filename):
        envelopes = {
            "portfolio.json": {"data": {"holdings": []}, "status": "success"},
            "risk_dashboard.json": {"data": {"regime": "NORMAL"}, "status": "success"},
            "technical_signals.json": {"data": {"results": []}, "status": "success"},
            "news_sentiment.json": {"data": {"results": []}, "status": "success"},
            "morning_brief.json": {"data": {"watchlist_actions": []}, "status": "success"},
            "unusual_options.json": {"data": {"unusual_options": []}, "status": "success"},
            "opportunity_scanner.json": {"data": {"opportunities": []}, "status": "success"},
        }
        return envelopes.get(filename, {"data": {}, "status": "missing"})

    mock_load.side_effect = load_side_effect
    mock_load_hist.return_value = {
        "fired": {},
        "previous_verdicts": {},
        "previous_composites": {},
        "previous_regime": None,
        "previous_sentiments": {},
    }

    main()

    # Verify save_envelope was called with alert_system.json
    calls = [str(call) for call in mock_save.call_args_list]
    assert any("alert_system.json" in str(call) for call in calls), "alert_system.json should be saved"


def test_alert_envelope_structure(tmp_path):
    """Test that generated envelope has correct structure."""
    portfolio = {"holdings": []}
    history = {"fired": {}, "previous_verdicts": {}, "previous_composites": {}, "previous_sentiments": {}}
    alerts = detect_alerts(portfolio, {}, {}, {}, {}, {}, {}, None, history)

    # Verify each alert has required fields
    for alert in alerts:
        assert "id" in alert
        assert "level" in alert
        assert "category" in alert
        assert "message" in alert
        assert "timestamp" in alert
        assert "data" in alert
        assert alert["level"] in ["critical", "high", "medium", "low"]


def test_alert_levels_priority():
    """Test alerts are generated in priority order (critical and high)."""
    portfolio = {
        "holdings": [
            {"ticker": "NVDA", "current_price": 850.0, "trailing_stop": 880.0},  # Stop breached (critical)
            {"ticker": "AAPL", "current_price": 200.0, "trailing_stop": 194.5},  # Stop close ~2.75% (high)
        ]
    }
    history = {"fired": {}, "previous_verdicts": {}, "previous_composites": {}, "previous_sentiments": {}}
    alerts = detect_alerts(portfolio, {}, {}, {}, {}, {}, {}, None, history)

    assert len(alerts) >= 2
    critical = [a for a in alerts if a["level"] == "critical"]
    high = [a for a in alerts if a["level"] == "high"]
    assert len(critical) > 0
    assert len(high) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
