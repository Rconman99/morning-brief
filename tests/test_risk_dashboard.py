"""Tests for risk_dashboard module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch, MagicMock

from modules.risk_dashboard import (
    assess_now_risks, assess_short_risks, assess_long_risks,
    calculate_regime, analyze_risk, main,
)


DEFAULT_SETTINGS = {
    "risk_vix_high": 20,
    "risk_vix_extreme": 30,
    "risk_concentration_threshold": 0.40,
    "risk_correlation_alert": 0.85,
    "risk_stop_close_pct": 0.07,
    "risk_stop_imminent_pct": 0.03,
}


# ── NOW risk tests ──

def test_vix_extreme():
    """VIX > 30 should trigger high risk."""
    risks = assess_now_risks({}, {}, DEFAULT_SETTINGS, vix=35.0)
    vix_risks = [r for r in risks if r["risk"] == "VIX_EXTREME"]
    assert len(vix_risks) == 1
    assert vix_risks[0]["level"] == "high"


def test_vix_elevated():
    """VIX > 20 should trigger medium risk."""
    risks = assess_now_risks({}, {}, DEFAULT_SETTINGS, vix=25.0)
    vix_risks = [r for r in risks if r["risk"] == "VIX_ELEVATED"]
    assert len(vix_risks) == 1
    assert vix_risks[0]["level"] == "medium"


def test_vix_normal():
    """VIX < 20 should not trigger."""
    risks = assess_now_risks({}, {}, DEFAULT_SETTINGS, vix=15.0)
    vix_risks = [r for r in risks if "VIX" in r["risk"]]
    assert len(vix_risks) == 0


def test_concentration_risk():
    """Single holding > 40% should trigger."""
    portfolio = {
        "holdings": [
            {"ticker": "NVDA", "current_price": 900, "shares": 100},  # 90k
            {"ticker": "AAPL", "current_price": 200, "shares": 50},   # 10k
        ]
    }
    risks = assess_now_risks(portfolio, {}, DEFAULT_SETTINGS, vix=None)
    conc_risks = [r for r in risks if r["risk"] == "CONCENTRATION"]
    assert len(conc_risks) == 1
    assert "NVDA" in conc_risks[0]["detail"]


def test_correlation_risk():
    """Correlation > 0.85 should trigger."""
    portfolio = {
        "holdings": [],
        "correlations": {"NVDA_AAPL": 0.92},
    }
    risks = assess_now_risks(portfolio, {}, DEFAULT_SETTINGS, vix=None)
    corr_risks = [r for r in risks if r["risk"] == "CORRELATED_PAIR"]
    assert len(corr_risks) == 1


def test_correlation_below_threshold():
    """Correlation <= 0.85 should not trigger."""
    portfolio = {
        "holdings": [],
        "correlations": {"NVDA_AAPL": 0.80},
    }
    risks = assess_now_risks(portfolio, {}, DEFAULT_SETTINGS, vix=None)
    corr_risks = [r for r in risks if r["risk"] == "CORRELATED_PAIR"]
    assert len(corr_risks) == 0


def test_stop_imminent():
    """Stop within 3% should trigger high risk."""
    portfolio = {
        "holdings": [{"ticker": "NVDA", "current_price": 100, "trailing_stop": 98, "shares": 10}],
    }
    risks = assess_now_risks(portfolio, {}, DEFAULT_SETTINGS, vix=None)
    stop_risks = [r for r in risks if r["risk"] == "STOP_IMMINENT"]
    assert len(stop_risks) == 1
    assert stop_risks[0]["level"] == "high"


def test_stop_close():
    """Stop within 7% but > 3% should trigger medium risk."""
    portfolio = {
        "holdings": [{"ticker": "NVDA", "current_price": 100, "trailing_stop": 95, "shares": 10}],
    }
    risks = assess_now_risks(portfolio, {}, DEFAULT_SETTINGS, vix=None)
    stop_risks = [r for r in risks if r["risk"] == "STOP_CLOSE"]
    assert len(stop_risks) == 1
    assert stop_risks[0]["level"] == "medium"


def test_sentiment_shock():
    """Sentiment < -0.7 should trigger high risk."""
    sentiment = {"results": [{"ticker": "NVDA", "sentiment_score": -0.8}]}
    risks = assess_now_risks({}, sentiment, DEFAULT_SETTINGS, vix=None)
    sent_risks = [r for r in risks if r["risk"] == "SENTIMENT_SHOCK"]
    assert len(sent_risks) == 1


# ── SHORT risk tests ──

def test_overbought_risk():
    """RSI > 75 should trigger overbought."""
    technical = {"results": [{"ticker": "NVDA", "rsi_14": 80, "bb_position": "middle"}]}
    risks = assess_short_risks(technical, {"tickers": []}, {"holdings": []})
    ob_risks = [r for r in risks if r["risk"] == "OVERBOUGHT"]
    assert len(ob_risks) == 1


def test_oversold_bounce():
    """RSI < 25 should trigger oversold bounce (low risk)."""
    technical = {"results": [{"ticker": "NVDA", "rsi_14": 20, "bb_position": "middle"}]}
    risks = assess_short_risks(technical, {"tickers": []}, {"holdings": []})
    os_risks = [r for r in risks if r["risk"] == "OVERSOLD_BOUNCE"]
    assert len(os_risks) == 1
    assert os_risks[0]["level"] == "low"


def test_bb_squeeze():
    """Bollinger squeeze should trigger medium risk."""
    technical = {"results": [{"ticker": "NVDA", "rsi_14": 50, "bb_position": "squeeze"}]}
    risks = assess_short_risks(technical, {"tickers": []}, {"holdings": []})
    sq_risks = [r for r in risks if r["risk"] == "BB_SQUEEZE"]
    assert len(sq_risks) == 1


# ── LONG risk tests ──

def test_death_cross_risk():
    """Death cross should trigger high risk."""
    technical = {"results": [{"ticker": "NVDA", "sma_trend": "death_cross"}]}
    risks = assess_long_risks(technical, {"results": []}, None, None)
    dc_risks = [r for r in risks if r["risk"] == "DEATH_CROSS"]
    assert len(dc_risks) == 1
    assert dc_risks[0]["level"] == "high"


def test_rising_rates():
    """10Y yield up > 0.25% in 30 days should trigger."""
    risks = assess_long_risks({"results": []}, {"results": []}, 4.5, 4.1)
    rate_risks = [r for r in risks if r["risk"] == "RISING_RATES"]
    assert len(rate_risks) == 1


def test_rising_rates_stable():
    """10Y yield stable should not trigger."""
    risks = assess_long_risks({"results": []}, {"results": []}, 4.3, 4.2)
    rate_risks = [r for r in risks if r["risk"] == "RISING_RATES"]
    assert len(rate_risks) == 0


def test_weak_fundamentals():
    """Negative earnings tone should trigger."""
    earnings = {"results": [{"ticker": "NVDA", "tone_score": -1.5}]}
    risks = assess_long_risks({"results": []}, earnings, None, None)
    weak_risks = [r for r in risks if r["risk"] == "WEAK_FUNDAMENTALS"]
    assert len(weak_risks) == 1


# ── Regime tests ──

def test_regime_danger():
    """High aggregate score should be DANGER."""
    now = [{"level": "high"}, {"level": "high"}, {"level": "high"},
           {"level": "high"}, {"level": "high"}, {"level": "high"}]  # 18 points
    _, _, _, total, regime, _ = calculate_regime(now, [], [])
    assert regime == "DANGER"
    assert total > 15


def test_regime_caution():
    """Moderate score should be CAUTION."""
    now = [{"level": "high"}, {"level": "high"}, {"level": "high"}]  # 9 points
    _, _, _, total, regime, _ = calculate_regime(now, [], [])
    assert regime == "CAUTION"
    assert 8 < total <= 15


def test_regime_normal():
    """Medium risk should be NORMAL."""
    now = [{"level": "medium"}, {"level": "medium"},
           {"level": "medium"}, {"level": "medium"}]  # 4 points
    _, _, _, total, regime, _ = calculate_regime(now, [], [])
    assert regime == "NORMAL"
    assert 3 < total <= 8


def test_regime_low_risk():
    """Low risks only should be LOW_RISK."""
    now = [{"level": "low"}]  # 0.5 points
    _, _, _, total, regime, _ = calculate_regime(now, [], [])
    assert regime == "LOW_RISK"
    assert total <= 3


def test_aggregate_scoring_math():
    """Verify discount factors: SHORT * 0.7, LONG * 0.4."""
    now = [{"level": "high"}]  # 3
    short = [{"level": "high"}]  # 3 * 0.7 = 2.1
    long = [{"level": "high"}]  # 3 * 0.4 = 1.2
    now_s, short_s, long_s, total, _, _ = calculate_regime(now, short, long)
    assert now_s == 3
    assert short_s == 3
    assert long_s == 3
    assert total == pytest.approx(3 + 2.1 + 1.2, abs=0.1)


# ── Full analyze_risk test ──

@patch("modules.risk_dashboard.get_ten_year_yield")
@patch("modules.risk_dashboard.get_vix")
@patch("modules.risk_dashboard.get_earnings_dates")
def test_analyze_risk_full(mock_earnings, mock_vix, mock_tny):
    """Full risk analysis with mocked market data."""
    mock_vix.return_value = 25.0
    mock_tny.return_value = (4.5, 4.1)
    mock_earnings.return_value = None

    portfolio = {
        "holdings": [
            {"ticker": "NVDA", "current_price": 900, "shares": 50, "trailing_stop": 850},
        ],
        "correlations": {},
    }
    technical = {"results": [
        {"ticker": "NVDA", "rsi_14": 65, "bb_position": "middle", "sma_trend": "golden_cross"},
    ]}
    sentiment = {"results": [{"ticker": "NVDA", "sentiment_score": 0.3}]}
    options = {"tickers": []}
    earnings = {"results": []}

    with patch("modules.risk_dashboard.load_settings", return_value=DEFAULT_SETTINGS):
        data = analyze_risk(portfolio, technical, sentiment, options, earnings)

    assert "regime" in data
    assert "total_risk" in data
    assert "now_risks" in data
    assert "short_risks" in data
    assert "long_risks" in data
    assert data["vix"] == 25.0
    assert data["ten_year_yield"] == 4.5


# ── main() test ──

@patch("modules.risk_dashboard.get_ten_year_yield")
@patch("modules.risk_dashboard.get_vix")
@patch("modules.risk_dashboard.get_earnings_dates")
def test_main_creates_output(mock_earnings, mock_vix, mock_tny, tmp_path):
    """main() should create risk_dashboard.json."""
    mock_vix.return_value = 15.0
    mock_tny.return_value = (4.0, 3.9)
    mock_earnings.return_value = None

    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(json.dumps(DEFAULT_SETTINGS))

    # Create mock envelopes
    for name, data in [
        ("portfolio", {"holdings": [], "correlations": {}}),
        ("technical_signals", {"results": []}),
        ("news_sentiment", {"results": []}),
        ("options", {"tickers": []}),
        ("earnings_tone", {"results": []}),
    ]:
        envelope = {"module": name, "generated_at": "2026-03-01T08:00:00-07:00",
                    "status": "success", "error_message": None, "data": data}
        (processed / f"{name}.json").write_text(json.dumps(envelope))

    with patch("modules.risk_dashboard.PROJECT_ROOT", tmp_path), \
         patch("modules.risk_dashboard.PROCESSED_DIR", processed), \
         patch("lib.data_envelope.PROCESSED_DIR", processed):
        main()

    output_file = processed / "risk_dashboard.json"
    assert output_file.exists()
    envelope = json.loads(output_file.read_text())
    assert envelope["module"] == "risk_dashboard"
    assert envelope["status"] == "success"
    assert envelope["data"]["regime"] in ("DANGER", "CAUTION", "NORMAL", "LOW_RISK")
