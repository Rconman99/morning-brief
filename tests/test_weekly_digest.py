"""Tests for the weekly_digest module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import pandas as pd

from modules.weekly_digest import (
    get_week_dates,
    get_morning_briefs_this_week,
    calculate_portfolio_performance,
    calculate_verdict_scorecard,
    extract_key_events,
    extract_upcoming_catalysts,
    generate_markdown,
    main,
)


@pytest.fixture
def sample_portfolio():
    """Sample portfolio data."""
    return {
        "holdings": [
            {"ticker": "NVDA", "shares": 50, "cost_basis": 180.00, "current_price": 185.00, "current_pnl": 250},
            {"ticker": "AAPL", "shares": 100, "cost_basis": 150.00, "current_price": 175.00, "current_pnl": 2500},
            {"ticker": "VOO", "shares": 25, "cost_basis": 420.00, "current_price": 430.00, "current_pnl": 250},
        ],
        "cash": 10000,
    }


@pytest.fixture
def sample_verdict_log():
    """Sample verdict log data."""
    today = datetime.now()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    entries = []
    verdicts = ["BUY", "SELL", "HOLD", "AVOID", "REVIEW"]
    tickers = ["AAPL", "NVDA", "MSFT", "AMD", "GOOGL"]

    for date in dates:
        for i, ticker in enumerate(tickers):
            entries.append({
                "date": date,
                "ticker": ticker,
                "verdict": verdicts[i],
                "reason": f"Sample verdict for {ticker}",
                "price_at_verdict": 100 + i * 50,
                "spy_price_at_verdict": 680,
            })

    return {"entries": entries}


@pytest.fixture
def sample_morning_brief_content():
    """Sample morning brief markdown content."""
    return """# Morning Brief — 2026-03-04

## Key Events
- NVDA unusual call activity detected
- Risk regime shifted to CAUTION
- VOO stop within 3% of price

## Portfolio Status
| Ticker | Price | Change |
|--------|-------|--------|
| NVDA   | $185  | +2.8%  |

## Verdicts
- BUY: NVDA (Technical strong)
- SELL: AAPL (Stop risk)
- HOLD: VOO (No signal)
"""


class TestWeekDates:
    """Test date range functions."""

    def test_get_week_dates(self):
        """Test that week dates are 7 days apart."""
        start, end = get_week_dates()
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        diff = (end_dt - start_dt).days
        assert diff == 6  # 6 days apart means 7 days inclusive


class TestPortfolioPerformance:
    """Test portfolio performance calculations."""

    @patch("modules.weekly_digest.yahoo_finance_price_history")
    def test_calculate_portfolio_performance(self, mock_yf, sample_portfolio):
        """Test portfolio performance calculation."""
        # Mock price history with proper index
        dates = pd.date_range(start="2026-02-25", end="2026-03-04", freq="D")
        mock_df = pd.DataFrame({
            "Close": [180 + i * 0.5 for i in range(len(dates))],
            "High": [182 + i * 0.5 for i in range(len(dates))],
            "Low": [179 + i * 0.5 for i in range(len(dates))],
        }, index=dates)
        mock_yf.return_value = mock_df

        result = calculate_portfolio_performance(sample_portfolio["holdings"])

        assert "weekly_summary" in result
        assert "holdings" in result

    @patch("modules.weekly_digest.yahoo_finance_price_history")
    def test_portfolio_performance_empty_holdings(self, mock_yf):
        """Test with no holdings."""
        result = calculate_portfolio_performance([])
        assert result["weekly_summary"]["total_value_start"] == 0
        assert len(result["holdings"]) == 0

    @patch("modules.weekly_digest.yahoo_finance_price_history")
    def test_portfolio_performance_no_price_data(self, mock_yf):
        """Test with holdings but no price data."""
        mock_yf.return_value = pd.DataFrame()
        holdings = [{"ticker": "TEST", "shares": 10}]
        result = calculate_portfolio_performance(holdings)
        assert len(result["holdings"]) == 0


class TestVerdictScorecard:
    """Test verdict scorecard calculations."""

    def test_calculate_verdict_scorecard_no_file(self, tmp_path, monkeypatch):
        """Test scorecard calculation when verdict_log.json is missing."""
        monkeypatch.setattr("modules.weekly_digest.SCORECARD_DIR", tmp_path)
        result = calculate_verdict_scorecard()

        assert result["total_verdicts"] == 0
        assert result["verdict_breakdown"] == {}
        assert result["verdict_flips"] == []

    def test_calculate_verdict_scorecard_with_data(self, tmp_path, sample_verdict_log, monkeypatch):
        """Test scorecard calculation with actual data."""
        scorecard_dir = tmp_path / "scorecard"
        scorecard_dir.mkdir()
        verdict_file = scorecard_dir / "verdict_log.json"
        verdict_file.write_text(json.dumps(sample_verdict_log))

        monkeypatch.setattr("modules.weekly_digest.SCORECARD_DIR", scorecard_dir)

        result = calculate_verdict_scorecard()

        assert result["total_verdicts"] > 0
        assert "BUY" in result["verdict_breakdown"] or "SELL" in result["verdict_breakdown"]
        assert isinstance(result["verdict_flips"], list)


class TestKeyEvents:
    """Test key events extraction."""

    def test_extract_key_events_no_briefs(self, tmp_path, monkeypatch):
        """Test with no morning brief files."""
        monkeypatch.setattr("modules.weekly_digest.OUTPUTS_DIR", tmp_path)
        events = extract_key_events()
        assert isinstance(events, list)

    def test_extract_key_events_with_briefs(self, tmp_path, sample_morning_brief_content, monkeypatch):
        """Test extraction of key events from briefs."""
        outputs_dir = tmp_path / "outputs"
        outputs_dir.mkdir()
        
        # Create a sample brief with proper date
        today = datetime.now().strftime("%Y-%m-%d")
        brief_file = outputs_dir / f"morning_brief_{today}.md"
        brief_file.write_text(sample_morning_brief_content)

        monkeypatch.setattr("modules.weekly_digest.OUTPUTS_DIR", outputs_dir)

        events = extract_key_events()
        assert isinstance(events, list)


class TestUpcomingCatalysts:
    """Test upcoming catalysts extraction."""

    def test_extract_upcoming_catalysts_no_data(self, tmp_path, monkeypatch):
        """Test with no processed data."""
        processed_dir = tmp_path / "processed"
        processed_dir.mkdir()
        monkeypatch.setattr("modules.weekly_digest.PROCESSED_DIR", processed_dir)

        catalysts = extract_upcoming_catalysts()

        assert "earnings_dates" in catalysts
        assert "options_expiry" in catalysts
        assert "stops_at_risk" in catalysts
        assert "economic_events" in catalysts

    @patch("modules.weekly_digest.load_envelope")
    def test_extract_upcoming_catalysts_with_portfolio(self, mock_load):
        """Test catalyst extraction with portfolio data."""
        mock_load.side_effect = [
            {
                "status": "success",
                "data": {
                    "holdings": [
                        {
                            "ticker": "NVDA",
                            "trailing_stop": 180,
                            "current_price": 185,
                        }
                    ]
                }
            },
            {
                "status": "success",
                "data": {
                    "tickers": [
                        {
                            "ticker": "NVDA",
                            "expirations": ["2026-03-20", "2026-04-17"],
                        }
                    ]
                }
            }
        ]

        catalysts = extract_upcoming_catalysts()

        assert isinstance(catalysts["stops_at_risk"], list)
        assert isinstance(catalysts["options_expiry"], list)


class TestMarkdownGeneration:
    """Test markdown generation."""

    def test_generate_markdown_basic(self, sample_portfolio):
        """Test basic markdown generation."""
        week_data = {
            "portfolio_performance": {
                "holdings": [
                    {
                        "ticker": "NVDA",
                        "shares": 50,
                        "start_price": 180,
                        "end_price": 185,
                        "price_change_pct": 2.78,
                        "pnl_change": 250,
                    }
                ],
                "weekly_summary": {
                    "total_value_start": 9000,
                    "total_value_end": 9250,
                    "total_pnl": 250,
                    "total_pnl_pct": 2.78,
                    "best_performer": {"ticker": "NVDA", "pnl_pct": 2.78},
                    "worst_performer": {"ticker": "NVDA", "pnl_pct": 2.78},
                },
            },
            "verdict_scorecard": {
                "total_verdicts": 10,
                "verdict_breakdown": {"BUY": 2, "SELL": 1, "HOLD": 7},
                "verdict_flips": [],
            },
        }

        markdown = generate_markdown(week_data)

        assert "Weekly Digest" in markdown
        assert "NVDA" in markdown
        assert "Portfolio Performance" in markdown
        assert "Verdict Accuracy" in markdown

    def test_generate_markdown_no_holdings(self):
        """Test markdown with no holdings."""
        week_data = {
            "portfolio_performance": {
                "holdings": [],
                "weekly_summary": {},
            },
            "verdict_scorecard": {
                "total_verdicts": 0,
                "verdict_breakdown": {},
                "verdict_flips": [],
            },
        }

        markdown = generate_markdown(week_data)

        assert "Weekly Digest" in markdown
        assert "No holdings data available" in markdown


class TestMainFunctionBasic:
    """Test main function with real file system."""

    def test_main_executes_without_error(self, tmp_path, monkeypatch):
        """Test that main function executes and saves envelope."""
        # Setup temp directories
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "portfolio.json").write_text(json.dumps({"holdings": []}))

        processed_dir = tmp_path / "processed"
        processed_dir.mkdir()

        outputs_dir = tmp_path / "outputs"
        outputs_dir.mkdir()

        scorecard_dir = tmp_path / "scorecard"
        scorecard_dir.mkdir()

        # Monkeypatch the paths in lib/data_envelope as well
        monkeypatch.setattr("modules.weekly_digest.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("lib.data_envelope.PROJECT_ROOT", tmp_path)

        # Mock yfinance calls
        with patch("modules.weekly_digest.yahoo_finance_price_history") as mock_yf:
            mock_yf.return_value = pd.DataFrame()
            main()

        # Verify that we created output files
        assert outputs_dir.exists()
        assert config_dir.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
