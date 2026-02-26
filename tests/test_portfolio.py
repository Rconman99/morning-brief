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

from modules.portfolio import compute_atr, analyze_portfolio, main


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
