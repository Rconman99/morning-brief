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
def test_trailing_stop_ratchets_up(mock_yf):
    """Trailing stop should only increase, never decrease when price drops."""
    # Run 1: price at 600
    history_high = make_mock_history(100, 500.0)
    # Override last close to 600
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

    # Run 2: price drops to 500
    history_low = make_mock_history(100, 500.0)
    history_low.iloc[-1, history_low.columns.get_loc("Close")] = 500.0
    mock_yf.return_value = history_low

    result2 = analyze_portfolio(holdings, settings, stop_tracker=tracker)
    stop2 = result2["holdings"][0]["trailing_stop"]
    max2 = result2["holdings"][0]["max_price"]

    # max_price should stay at 600 (not drop to 500)
    assert max2 == 600.0
    # trailing stop should never decrease
    assert stop2 >= stop1, f"Stop dropped from {stop1} to {stop2}"


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
