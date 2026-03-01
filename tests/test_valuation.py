"""Tests for valuation module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch

from modules.valuation import get_metrics, compare_pair, scenario_valuation, main


MOCK_INFO_NVDA = {
    "trailingPE": 65.2,
    "enterpriseToEbitda": 55.0,
    "marketCap": 2200000000000,
    "freeCashflow": 30000000000,
    "pegRatio": 1.8,
    "priceToBook": 42.0,
}

MOCK_INFO_AMD = {
    "trailingPE": 45.1,
    "enterpriseToEbitda": 32.0,
    "marketCap": 230000000000,
    "freeCashflow": 5000000000,
    "pegRatio": 1.2,
    "priceToBook": 5.5,
}


@patch("modules.valuation.alpha_vantage_call", return_value={})
@patch("modules.valuation.yahoo_finance_info")
def test_get_metrics(mock_yf, mock_av):
    mock_yf.return_value = MOCK_INFO_NVDA
    metrics = get_metrics("NVDA")
    assert metrics["ticker"] == "NVDA"
    assert metrics["pe_ttm"] == 65.2
    assert metrics["price_fcf"] is not None


@patch("modules.valuation.alpha_vantage_call", return_value={})
@patch("modules.valuation.yahoo_finance_info")
def test_compare_pair_cheaper(mock_yf, mock_av):
    mock_yf.side_effect = lambda t: MOCK_INFO_NVDA if t == "NVDA" else MOCK_INFO_AMD
    result = compare_pair("NVDA", "AMD")
    assert result["cheaper"] == "AMD"
    assert result["b_wins"] > result["a_wins"]


@patch("modules.valuation.alpha_vantage_call", return_value={})
@patch("modules.valuation.yahoo_finance_info", return_value={})
def test_compare_pair_inconclusive(mock_yf, mock_av):
    result = compare_pair("NVDA", "AMD")
    assert result["cheaper"] == "inconclusive"


@patch("modules.valuation.yahoo_finance_info")
def test_scenario_valuation_with_analyst_targets(mock_yf):
    """Scenario valuation should use analyst targets when available."""
    mock_yf.return_value = {
        "forwardPE": 35.0,
        "forwardEps": 27.14,
        "currentPrice": 900.0,
        "targetHighPrice": 1100.0,
        "targetLowPrice": 750.0,
        "targetMeanPrice": 950.0,
    }
    result = scenario_valuation("NVDA")
    assert result is not None
    assert result["bull_price"] == 1100.0
    assert result["base_price"] == 950.0
    assert result["bear_price"] == 750.0
    assert result["bull_upside"] > 0
    assert result["bear_downside"] < 0
    assert result["risk_reward"] is not None


@patch("modules.valuation.yahoo_finance_info")
def test_scenario_valuation_from_pe(mock_yf):
    """Scenario valuation should estimate from PE when no analyst targets."""
    mock_yf.return_value = {
        "forwardPE": 30.0,
        "forwardEps": 10.0,
        "currentPrice": 300.0,
    }
    result = scenario_valuation("TEST")
    assert result is not None
    # bull = 10 * 30 * 1.25 = 375
    assert result["bull_price"] == 375.0
    # base = 10 * 30 = 300
    assert result["base_price"] == 300.0
    # bear = 10 * 30 * 0.75 = 225
    assert result["bear_price"] == 225.0


@patch("modules.valuation.yahoo_finance_info")
def test_scenario_valuation_insufficient_data(mock_yf):
    """Should return None when forward PE is missing."""
    mock_yf.return_value = {"currentPrice": 100.0}
    result = scenario_valuation("TEST")
    assert result is None


@patch("modules.valuation.yahoo_finance_info")
def test_scenario_valuation_risk_reward(mock_yf):
    """Risk/reward ratio should be abs(bull_upside / bear_downside)."""
    mock_yf.return_value = {
        "forwardPE": 20.0,
        "forwardEps": 5.0,
        "currentPrice": 100.0,
    }
    result = scenario_valuation("TEST")
    assert result is not None
    # bull = 5 * 20 * 1.25 = 125 → upside = 0.25
    # bear = 5 * 20 * 0.75 = 75 → downside = -0.25
    # R/R = 0.25 / 0.25 = 1.0
    assert result["risk_reward"] == 1.0


@patch("modules.valuation.alpha_vantage_call", return_value={})
@patch("modules.valuation.yahoo_finance_info")
def test_main_creates_output(mock_yf, mock_av, tmp_path):
    mock_yf.side_effect = lambda t: MOCK_INFO_NVDA if t == "NVDA" else MOCK_INFO_AMD

    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)
    config = tmp_path / "config"
    config.mkdir()
    (config / "watchlist.json").write_text(json.dumps({
        "tickers": ["NVDA", "AMD"],
        "comparison_pairs": [["NVDA", "AMD"]],
        "earnings_watch": [],
    }))

    with patch("modules.valuation.PROJECT_ROOT", tmp_path), \
         patch("lib.data_envelope.PROCESSED_DIR", processed):
        main()

    output_file = processed / "valuation.json"
    assert output_file.exists()
    envelope = json.loads(output_file.read_text())
    assert envelope["module"] == "valuation"
    assert envelope["status"] in ("success", "partial")
    assert len(envelope["data"]["comparisons"]) == 1
    assert "scenarios" in envelope["data"]
