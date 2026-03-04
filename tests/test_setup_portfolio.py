"""Tests for scripts/setup_portfolio.py."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
from unittest.mock import MagicMock, patch

import pytest

from scripts.setup_portfolio import (
    display_holdings,
    extract_portfolio,
    generate_watchlist,
    write_configs,
)
import scripts.setup_portfolio

HAS_ANTHROPIC = getattr(scripts.setup_portfolio, 'HAS_ANTHROPIC', False)

SAMPLE_HOLDINGS = [
    {"ticker": "AAPL", "shares": 100, "cost_basis": 178.50},
    {"ticker": "NVDA", "shares": 50, "cost_basis": 485.00},
    {"ticker": "MSFT", "shares": 30, "cost_basis": 310.00},
    {"ticker": "JPM", "shares": 20, "cost_basis": 155.00},
]


class TestGenerateWatchlist:
    def test_tickers_match_holdings(self):
        result = generate_watchlist(SAMPLE_HOLDINGS)
        assert result["tickers"] == ["AAPL", "NVDA", "MSFT", "JPM"]

    def test_earnings_watch_matches_holdings(self):
        result = generate_watchlist(SAMPLE_HOLDINGS)
        assert result["earnings_watch"] == ["AAPL", "NVDA", "MSFT", "JPM"]

    def test_pairs_are_same_sector(self):
        result = generate_watchlist(SAMPLE_HOLDINGS)
        from scripts.setup_portfolio import SECTORS

        ticker_to_sector = {}
        for sector, members in SECTORS.items():
            for t in members:
                ticker_to_sector[t] = sector

        for pair in result["comparison_pairs"]:
            s1 = ticker_to_sector.get(pair[0])
            s2 = ticker_to_sector.get(pair[1])
            assert s1 == s2, f"Pair {pair} crosses sectors: {s1} vs {s2}"

    def test_tech_tickers_form_pairs(self):
        """AAPL, NVDA, MSFT are all tech — should produce pairs."""
        result = generate_watchlist(SAMPLE_HOLDINGS)
        assert len(result["comparison_pairs"]) >= 2

    def test_no_pairs_for_lone_sector(self):
        """A single ticker in a sector should not create a pair."""
        holdings = [{"ticker": "XOM", "shares": 10, "cost_basis": 100}]
        result = generate_watchlist(holdings)
        assert result["comparison_pairs"] == []

    def test_unknown_tickers_get_no_pairs(self):
        """Tickers not in SECTORS should still appear in tickers list but get no pairs."""
        holdings = [{"ticker": "ZZZZ", "shares": 1, "cost_basis": 1}]
        result = generate_watchlist(holdings)
        assert result["tickers"] == ["ZZZZ"]
        assert result["comparison_pairs"] == []


class TestWriteConfigs:
    def test_creates_config_files(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        with patch("scripts.setup_portfolio.PROJECT_ROOT", tmp_path):
            write_configs(SAMPLE_HOLDINGS, cash=5000)

        portfolio = json.loads((config_dir / "portfolio.json").read_text())
        watchlist = json.loads((config_dir / "watchlist.json").read_text())

        assert len(portfolio["holdings"]) == 4
        assert portfolio["cash"] == 5000
        assert portfolio["options_positions"] == []
        assert portfolio["holdings"][0]["ticker"] == "AAPL"
        assert "date_acquired" in portfolio["holdings"][0]

        assert watchlist["tickers"] == ["AAPL", "NVDA", "MSFT", "JPM"]
        assert isinstance(watchlist["comparison_pairs"], list)
        assert isinstance(watchlist["earnings_watch"], list)

    def test_schema_matches_existing_format(self, tmp_path):
        """Generated portfolio.json should match the schema used by morning-brief modules."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        with patch("scripts.setup_portfolio.PROJECT_ROOT", tmp_path):
            write_configs(SAMPLE_HOLDINGS)

        portfolio = json.loads((config_dir / "portfolio.json").read_text())

        # Verify each holding has required keys
        for h in portfolio["holdings"]:
            assert set(h.keys()) == {"ticker", "shares", "cost_basis", "date_acquired"}
            assert isinstance(h["ticker"], str)
            assert isinstance(h["shares"], (int, float))
            assert isinstance(h["cost_basis"], (int, float))
            assert isinstance(h["date_acquired"], str)

    def test_backup_existing_config(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # Create existing configs
        (config_dir / "portfolio.json").write_text('{"old": true}')
        (config_dir / "watchlist.json").write_text('{"old": true}')

        with patch("scripts.setup_portfolio.PROJECT_ROOT", tmp_path):
            write_configs(SAMPLE_HOLDINGS)

        assert (config_dir / "portfolio.json.bak").exists()
        assert (config_dir / "watchlist.json.bak").exists()
        assert json.loads((config_dir / "portfolio.json.bak").read_text()) == {"old": True}


class TestDisplayHoldings:
    def test_smoke(self, capsys):
        display_holdings(SAMPLE_HOLDINGS)
        captured = capsys.readouterr()
        assert "AAPL" in captured.out
        assert "NVDA" in captured.out
        assert "Total holdings:" in captured.out
        assert "4" in captured.out


class TestExtractPortfolioMock:
    @pytest.mark.skipif(not HAS_ANTHROPIC, reason="anthropic SDK not installed")
    def test_parses_valid_response(self):
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text='[{"ticker": "AAPL", "shares": 100, "cost_basis": 178.50}]')
        ]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        # Create a small test image
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            # Minimal 1x1 PNG
            f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82')
            tmp_image = f.name

        try:
            with patch("scripts.setup_portfolio.anthropic") as mock_anthropic, \
                 patch("scripts.setup_portfolio._get_api_key", return_value="test-key"):
                mock_anthropic.Anthropic.return_value = mock_client

                result = extract_portfolio(tmp_image)

            assert len(result) == 1
            assert result[0]["ticker"] == "AAPL"
            assert result[0]["shares"] == 100
            assert result[0]["cost_basis"] == 178.50
        finally:
            Path(tmp_image).unlink(missing_ok=True)

    @pytest.mark.skipif(not HAS_ANTHROPIC, reason="anthropic SDK not installed")
    def test_handles_markdown_code_block(self):
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text='```json\n[{"ticker": "MSFT", "shares": 50, "cost_basis": 310.00}]\n```')
        ]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82')
            tmp_image = f.name

        try:
            with patch("scripts.setup_portfolio.anthropic") as mock_anthropic, \
                 patch("scripts.setup_portfolio._get_api_key", return_value="test-key"):
                mock_anthropic.Anthropic.return_value = mock_client

                result = extract_portfolio(tmp_image)

            assert result[0]["ticker"] == "MSFT"
        finally:
            Path(tmp_image).unlink(missing_ok=True)

    @pytest.mark.skipif(not HAS_ANTHROPIC, reason="anthropic SDK not installed")
    def test_normalizes_ticker_case(self):
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text='[{"ticker": "aapl", "shares": 10, "cost_basis": 150}]')
        ]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82')
            tmp_image = f.name

        try:
            with patch("scripts.setup_portfolio.anthropic") as mock_anthropic, \
                 patch("scripts.setup_portfolio._get_api_key", return_value="test-key"):
                mock_anthropic.Anthropic.return_value = mock_client

                result = extract_portfolio(tmp_image)

            assert result[0]["ticker"] == "AAPL"
        finally:
            Path(tmp_image).unlink(missing_ok=True)
