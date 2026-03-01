"""Tests for insider_tracker module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import date, timedelta

from modules.insider_tracker import (
    get_insider_transactions, analyze_insider_activity,
    analyze_all_insiders, main,
)


# ── edgartools fallback tests ──

def test_returns_empty_when_edgartools_unavailable():
    """When Company is None, should return empty list."""
    with patch("modules.insider_tracker.Company", None):
        result = get_insider_transactions("NVDA", 30)
        assert result == []


def test_analyze_returns_no_data_when_no_transactions():
    """When no transactions found, signal should be no_data."""
    with patch("modules.insider_tracker.get_insider_transactions", return_value=[]):
        result = analyze_insider_activity("NVDA", {})
        assert result["ticker"] == "NVDA"
        assert result["transaction_count"] == 0
        assert result["signal"] == "no_data"
        assert result["cluster_buy"] is False


# ── Cluster detection ──

def test_cluster_buy_detected():
    """Should detect cluster buy when filings >= threshold."""
    mock_txns = [
        {"filed_date": "2026-02-20", "filer": "CEO", "form": "4"},
        {"filed_date": "2026-02-21", "filer": "CFO", "form": "4"},
        {"filed_date": "2026-02-22", "filer": "CTO", "form": "4"},
        {"filed_date": "2026-02-23", "filer": "VP", "form": "4"},
    ]
    with patch("modules.insider_tracker.get_insider_transactions", return_value=mock_txns):
        result = analyze_insider_activity("NVDA", {"insider_cluster_threshold": 3})
        assert result["cluster_buy"] is True
        assert result["signal"] == "active"
        assert result["transaction_count"] == 4


def test_no_cluster_below_threshold():
    """Should not detect cluster when filings < threshold."""
    mock_txns = [
        {"filed_date": "2026-02-20", "filer": "CEO", "form": "4"},
    ]
    with patch("modules.insider_tracker.get_insider_transactions", return_value=mock_txns):
        result = analyze_insider_activity("NVDA", {"insider_cluster_threshold": 3})
        assert result["cluster_buy"] is False
        assert result["signal"] == "normal"


# ── Lookback filtering ──

def test_lookback_default():
    """Should use default 30-day lookback when not specified."""
    with patch("modules.insider_tracker.get_insider_transactions", return_value=[]) as mock_get:
        analyze_insider_activity("NVDA", {})
        mock_get.assert_called_once_with("NVDA", 30)


def test_lookback_custom():
    """Should use custom lookback when specified."""
    with patch("modules.insider_tracker.get_insider_transactions", return_value=[]) as mock_get:
        analyze_insider_activity("NVDA", {"insider_lookback_days": 60})
        mock_get.assert_called_once_with("NVDA", 60)


# ── Recent filings truncation ──

def test_recent_filings_limited_to_5():
    """Should include at most 5 recent filings in output."""
    mock_txns = [{"filed_date": f"2026-02-{i:02d}", "filer": f"Exec{i}", "form": "4"} for i in range(1, 11)]
    with patch("modules.insider_tracker.get_insider_transactions", return_value=mock_txns):
        result = analyze_insider_activity("NVDA", {"insider_cluster_threshold": 3})
        assert len(result["recent_filings"]) == 5


# ── Full analysis ──

def test_analyze_all_insiders():
    """Should analyze all tickers."""
    with patch("modules.insider_tracker.get_all_tickers", return_value=["NVDA", "AAPL"]), \
         patch("modules.insider_tracker.get_insider_transactions", return_value=[]):
        data = analyze_all_insiders()
        assert len(data["results"]) == 2


# ── Output envelope ──

def test_main_creates_output(tmp_path):
    """main() should create insider_tracker.json."""
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)
    config = tmp_path / "config"
    config.mkdir()
    (config / "watchlist.json").write_text(json.dumps({"tickers": ["NVDA"]}))
    (config / "portfolio.json").write_text(json.dumps({"holdings": []}))
    (config / "settings.json").write_text(json.dumps({}))

    with patch("modules.insider_tracker.PROJECT_ROOT", tmp_path), \
         patch("lib.data_envelope.PROCESSED_DIR", processed), \
         patch("modules.insider_tracker.get_insider_transactions", return_value=[]):
        main()

    output_file = processed / "insider_tracker.json"
    assert output_file.exists()
    envelope = json.loads(output_file.read_text())
    assert envelope["module"] == "insider_tracker"
    assert envelope["status"] in ("success", "partial")


# ── edgartools Company mock ──

def test_get_insider_transactions_with_mock_company():
    """Test with mocked edgartools Company."""
    mock_filing = MagicMock()
    mock_filing.filing_date = date.today() - timedelta(days=5)
    mock_filing.filer = "John Smith"
    mock_filing.accession_no = "0001234567-26-000001"

    mock_filings = MagicMock()
    mock_filings.latest.return_value = [mock_filing]

    mock_company = MagicMock()
    mock_company.get_filings.return_value = mock_filings

    mock_company_cls = MagicMock(return_value=mock_company)

    with patch("modules.insider_tracker.Company", mock_company_cls):
        result = get_insider_transactions("NVDA", 30)
        assert len(result) == 1
        assert result[0]["filer"] == "John Smith"


def test_get_insider_transactions_exception_returns_empty():
    """If edgartools raises, should return empty list."""
    mock_company_cls = MagicMock(side_effect=Exception("API error"))

    with patch("modules.insider_tracker.Company", mock_company_cls):
        result = get_insider_transactions("NVDA", 30)
        assert result == []
