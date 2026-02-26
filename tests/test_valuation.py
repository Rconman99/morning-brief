"""Tests for valuation module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch

from modules.valuation import get_metrics, compare_pair, main


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
