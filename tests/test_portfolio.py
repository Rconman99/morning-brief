"""Tests for portfolio module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch

from modules.portfolio import compute_atr, analyze_portfolio, main, load_stop_tracker, save_stop_tracker


def make_mock_history(days: int = 100, base_price: float = 500.0) -> pd.DataFrame:
    """Generate mock price history DataFrame."""
    dates = pd.date_range(end="2026-02-25", periods=days, freq="B")
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.02, days)
    prices = base_price * np.cumprod(1 + returns)
    high = prices * (1 + np.abs(np.random.normal(0, 0.01, days)))
    low = prices * (1 - np.abs(np.random.normal(0, 0.01, days)))
    return pd.DataFrame({
        "Open": prices * 0.999,
        "High": high,
        "Low": low,
        "Close": prices,
        "Volume": np.random.randint(1000000, 50000000, days),
    }, index=dates)


def test_compute_atr():
    history = make_mock_history(100, 500.0)
    current_price = float(history["Close"].iloc[-1])
    atr = compute_atr(history, current_price)
    assert atr is not None
    assert atr > 0


def test_compute_atr_empty():
    assert compute_atr(pd.DataFrame(), 100.0) is None


@patch("modules.portfolio.yahoo_finance_price_history")
def test_analyze_portfolio(mock_yf):
    mock_yf.side_effect = lambda t, **kw: make_mock_history(200, 500 if t == "NVDA" else 180)

    holdings = [
        {"ticker": "NVDA", "shares": 50, "cost_basis": 485.0},
        {"ticker": "AAPL", "shares": 100, "cost_basis": 178.5},
    ]
    settings = {"atr_multiplier_default": 2.0, "min_correlation_days": 60}

    result = analyze_portfolio(holdings, settings)
    assert len(result["holdings"]) == 2
    for h in result["holdings"]:
        assert h["current_price"] is not None
        assert h["atr"] is not None
        assert h["trailing_stop"] is not None
    assert "NVDA_AAPL" in result["correlations"]


@patch("modules.portfolio.yahoo_finance_price_history")
def test_trailing_stop_ratchets_up_when_above_stop(mock_yf):
    """Trailing stop ratchets up when price stays above the stop."""
    # Run 1: price at 600
    history_high = make_mock_history(100, 500.0)
    history_high.iloc[-1, history_high.columns.get_loc("Close")] = 600.0
    mock_yf.return_value = history_high

    holdings = [{"ticker": "NVDA", "shares": 50, "cost_basis": 485.0}]
    settings = {"atr_multiplier_default": 2.0, "min_correlation_days": 60}
    tracker = {}

    result1 = analyze_portfolio(holdings, settings, stop_tracker=tracker)
    stop1 = result1["holdings"][0]["trailing_stop"]
    max1 = result1["holdings"][0]["max_price"]
    assert stop1 is not None
    assert max1 == 600.0

    # Run 2: price rises to 620 (still above stop, higher than before)
    history_mid = make_mock_history(100, 500.0)
    history_mid.iloc[-1, history_mid.columns.get_loc("Close")] = 620.0
    mock_yf.return_value = history_mid

    result2 = analyze_portfolio(holdings, settings, stop_tracker=tracker)
    stop2 = result2["holdings"][0]["trailing_stop"]
    max2 = result2["holdings"][0]["max_price"]

    # max_price should update to 620
    assert max2 == 620.0
    # trailing stop should NOT decrease when price is still above stop
    assert stop2 >= stop1, f"Stop dropped from {stop1} to {stop2}"


@patch("modules.portfolio.yahoo_finance_price_history")
def test_trailing_stop_resets_when_breached(mock_yf):
    """Trailing stop resets to sensible level when price crashes through it."""
    # Run 1: price at 600, sets a high stop
    history_high = make_mock_history(100, 500.0)
    history_high.iloc[-1, history_high.columns.get_loc("Close")] = 600.0
    mock_yf.return_value = history_high

    holdings = [{"ticker": "NVDA", "shares": 50, "cost_basis": 485.0}]
    settings = {"atr_multiplier_default": 2.0, "min_correlation_days": 60}
    tracker = {}

    result1 = analyze_portfolio(holdings, settings, stop_tracker=tracker)
    stop1 = result1["holdings"][0]["trailing_stop"]
    assert stop1 is not None

    # Run 2: price crashes to 200 (well below stop)
    history_crash = make_mock_history(100, 500.0)
    history_crash.iloc[-1, history_crash.columns.get_loc("Close")] = 200.0
    mock_yf.return_value = history_crash

    result2 = analyze_portfolio(holdings, settings, stop_tracker=tracker)
    stop2 = result2["holdings"][0]["trailing_stop"]

    # Stop should reset to below current price, not stay frozen above it
    assert stop2 < 200.0, f"Stop {stop2} should be below crashed price 200.0"
    # Locked profit should be 0 for underwater position
    locked = result2["holdings"][0]["locked_profit"]
    assert locked == 0.0, f"Locked profit should be 0 for underwater position, got {locked}"


def test_stop_tracker_persistence(tmp_path):
    """Test load/save round-trip of stop tracker."""
    tracker = {"NVDA": {"max_price": 600.0, "trailing_stop": 550.0}}
    path = tmp_path / "stop_tracker.json"
    save_stop_tracker(tracker, path)
    loaded = load_stop_tracker(path)
    assert loaded["NVDA"]["max_price"] == 600.0
    assert loaded["NVDA"]["trailing_stop"] == 550.0


def test_stop_tracker_missing(tmp_path):
    """Missing tracker file returns empty dict."""
    path = tmp_path / "nonexistent.json"
    assert load_stop_tracker(path) == {}


@patch("modules.portfolio.yahoo_finance_price_history")
def test_main_creates_output(mock_yf, tmp_path):
    mock_yf.return_value = make_mock_history(200, 500.0)

    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)
    config = tmp_path / "config"
    config.mkdir()
    (config / "portfolio.json").write_text(json.dumps({
        "holdings": [{"ticker": "NVDA", "shares": 50, "cost_basis": 485.0}],
        "cash": 15000,
        "options_positions": [],
    }))
    (config / "settings.json").write_text(json.dumps({
        "atr_multiplier_default": 2.0,
        "min_correlation_days": 60,
    }))

    with patch("modules.portfolio.PROJECT_ROOT", tmp_path), \
         patch("lib.data_envelope.PROCESSED_DIR", processed):
        main()

    output_file = processed / "portfolio.json"
    assert output_file.exists()
    envelope = json.loads(output_file.read_text())
    assert envelope["module"] == "portfolio"
    assert envelope["status"] in ("success", "partial")
    assert len(envelope["data"]["holdings"]) == 1
