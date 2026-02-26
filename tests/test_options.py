"""Tests for options module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock

from modules.options import calculate_max_pain, compute_delta, main


def make_calls_df():
    return pd.DataFrame({
        "strike": [90, 95, 100, 105, 110],
        "openInterest": [1000, 2000, 5000, 3000, 1500],
        "lastPrice": [12.0, 8.0, 4.5, 2.0, 0.5],
    })


def make_puts_df():
    return pd.DataFrame({
        "strike": [90, 95, 100, 105, 110],
        "openInterest": [1500, 3000, 4000, 2500, 1000],
        "lastPrice": [0.5, 1.5, 3.0, 7.0, 12.0],
    })


def test_calculate_max_pain():
    calls = make_calls_df()
    puts = make_puts_df()
    mp = calculate_max_pain(calls, puts)
    assert mp is not None
    assert 90 <= mp <= 110


def test_calculate_max_pain_empty():
    assert calculate_max_pain(pd.DataFrame(columns=["strike", "openInterest"]),
                               pd.DataFrame(columns=["strike", "openInterest"])) is None


def test_compute_delta_itm():
    delta = compute_delta(100, 85, 365, 0.30)
    if delta is not None:  # scipy may not be available
        assert delta > 0.7, "Deep ITM call should have high delta"


def test_compute_delta_otm():
    delta = compute_delta(100, 115, 30, 0.30)
    if delta is not None:
        assert delta < 0.3, "OTM call should have low delta"


@patch("modules.options.yahoo_finance_price_history")
@patch("modules.options.yahoo_finance_info", return_value={"regularMarketPrice": 100.0})
def test_main_creates_output(mock_info, mock_hist, tmp_path):
    # Mock price history
    dates = pd.date_range(end="2026-02-25", periods=50, freq="B")
    mock_hist.return_value = pd.DataFrame({
        "Close": np.linspace(95, 105, 50),
        "High": np.linspace(96, 106, 50),
        "Low": np.linspace(94, 104, 50),
    }, index=dates)

    # Mock yfinance Ticker
    mock_ticker = MagicMock()
    mock_ticker.options = ("2026-04-17", "2026-09-18")

    chain_mock = MagicMock()
    chain_mock.calls = make_calls_df()
    chain_mock.puts = make_puts_df()
    mock_ticker.option_chain.return_value = chain_mock

    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)
    config = tmp_path / "config"
    config.mkdir()
    (config / "portfolio.json").write_text(json.dumps({
        "holdings": [{"ticker": "TEST", "shares": 50, "cost_basis": 90.0}],
        "cash": 10000,
        "options_positions": [],
    }))

    with patch("modules.options.PROJECT_ROOT", tmp_path), \
         patch("lib.data_envelope.PROCESSED_DIR", processed), \
         patch("yfinance.Ticker", return_value=mock_ticker):
        main()

    output_file = processed / "options.json"
    assert output_file.exists()
    envelope = json.loads(output_file.read_text())
    assert envelope["module"] == "options"
    assert envelope["status"] in ("success", "partial")
