"""Tests for scripts/sync_portfolio.py — CSV parsing, portfolio writing, fallback logic."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch, MagicMock

from scripts.sync_portfolio import (
    sync_from_csv,
    write_portfolio,
    load_existing_portfolio,
    build_trading_window_map,
    report_changes,
    tokens_are_fresh,
)


@pytest.fixture
def sample_csv(project_root):
    return project_root / "data" / "sample" / "etrade_positions.csv"


@pytest.fixture
def portfolio_dir(tmp_path):
    """Create a temp config dir with a portfolio.json."""
    config = tmp_path / "config"
    config.mkdir()
    portfolio = {
        "holdings": [
            {"ticker": "NVDA", "shares": 50, "cost_basis": 485.00, "date_acquired": "2025-06-15"},
            {"ticker": "AAPL", "shares": 100, "cost_basis": 178.50, "date_acquired": "2025-03-20"},
            {"ticker": "VOO", "shares": 25, "cost_basis": 420.00, "date_acquired": "2024-11-01"},
        ],
        "cash": 15000,
        "options_positions": [],
    }
    (config / "portfolio.json").write_text(json.dumps(portfolio, indent=2))
    return tmp_path


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

class TestSyncFromCSV:
    def test_parse_sample_csv(self, sample_csv):
        """Parse the sample CSV and verify output schema."""
        holdings, source = sync_from_csv(sample_csv)

        assert source == "csv"
        assert len(holdings) == 3

        for h in holdings:
            assert "ticker" in h
            assert "shares" in h
            assert "cost_basis" in h
            assert isinstance(h["ticker"], str)
            assert isinstance(h["shares"], (int, float))
            assert isinstance(h["cost_basis"], (int, float))

    def test_specific_values(self, sample_csv):
        """Verify specific parsed values from sample CSV."""
        holdings, _ = sync_from_csv(sample_csv)
        by_ticker = {h["ticker"]: h for h in holdings}

        nvda = by_ticker["NVDA"]
        assert nvda["shares"] == 50
        assert nvda["cost_basis"] == 485.0

        aapl = by_ticker["AAPL"]
        assert aapl["shares"] == 100
        assert aapl["cost_basis"] == 178.5

    def test_csv_not_found(self, tmp_path):
        """FileNotFoundError on missing CSV."""
        with pytest.raises(FileNotFoundError):
            sync_from_csv(tmp_path / "nonexistent.csv")

    def test_empty_csv(self, tmp_path):
        """ValueError on CSV with headers but no data rows."""
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("Symbol,Description,Quantity,Last Price,Value,Cost Basis Total\n")
        with pytest.raises(ValueError, match="no valid positions"):
            sync_from_csv(csv_file)

    def test_csv_with_bad_rows(self, tmp_path):
        """Rows with invalid data are skipped, valid ones kept."""
        csv_file = tmp_path / "mixed.csv"
        csv_file.write_text(
            "Symbol,Description,Quantity,Last Price,Value,Cost Basis Total\n"
            "AAPL,APPLE INC,100,195.00,19500.00,17850.00\n"
            "BAD,BAD CORP,not_a_number,0,0,0\n"
            "NVDA,NVIDIA CORP,50,135.50,6775.00,24250.00\n"
        )
        holdings, _ = sync_from_csv(csv_file)
        assert len(holdings) == 2
        tickers = {h["ticker"] for h in holdings}
        assert tickers == {"AAPL", "NVDA"}


# ---------------------------------------------------------------------------
# Trading window preservation
# ---------------------------------------------------------------------------

class TestTradingWindowPreservation:
    def test_preserves_trading_window(self, tmp_path):
        """A ticker's trading_window survives a portfolio overwrite."""
        config = tmp_path / "config"
        config.mkdir()
        portfolio = {
            "holdings": [
                {"ticker": "NVDA", "shares": 50, "cost_basis": 485.00},
                {"ticker": "AAPL", "shares": 100, "cost_basis": 178.50,
                 "trading_window": {"open": "2026-02-27", "close": "2026-04-15",
                                    "note": "Example trading restriction"}},
            ],
            "cash": 15000,
            "options_positions": [],
        }
        pf = config / "portfolio.json"
        pf.write_text(json.dumps(portfolio, indent=2))

        with patch("scripts.sync_portfolio.PORTFOLIO_FILE", pf):
            new_holdings = [
                {"ticker": "NVDA", "shares": 60, "cost_basis": 490.00},
                {"ticker": "AAPL", "shares": 100, "cost_basis": 178.50},
            ]
            write_portfolio(new_holdings, "csv")

            result = json.loads(pf.read_text())
            aapl = next(h for h in result["holdings"] if h["ticker"] == "AAPL")
            assert "trading_window" in aapl
            assert aapl["trading_window"]["note"] == "Example trading restriction"

    def test_no_window_for_new_tickers(self, portfolio_dir):
        """New tickers without existing trading_window don't get one."""
        pf = portfolio_dir / "config" / "portfolio.json"

        with patch("scripts.sync_portfolio.PORTFOLIO_FILE", pf):
            new_holdings = [
                {"ticker": "MSFT", "shares": 50, "cost_basis": 400.00},
            ]
            write_portfolio(new_holdings, "csv")

            result = json.loads(pf.read_text())
            msft = result["holdings"][0]
            assert "trading_window" not in msft


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

class TestBackup:
    def test_creates_backup(self, portfolio_dir):
        """Writing portfolio backs up the old file with timestamp."""
        pf = portfolio_dir / "config" / "portfolio.json"

        with patch("scripts.sync_portfolio.PORTFOLIO_FILE", pf):
            write_portfolio([{"ticker": "AAPL", "shares": 10, "cost_basis": 100}], "csv")

            backups = list(pf.parent.glob("portfolio.json.bak.*"))
            assert len(backups) == 1
            old = json.loads(backups[0].read_text())
            assert any(h["ticker"] == "NVDA" for h in old["holdings"])

    def test_preserves_cash_and_options(self, portfolio_dir):
        """Cash and options_positions survive the overwrite."""
        pf = portfolio_dir / "config" / "portfolio.json"

        with patch("scripts.sync_portfolio.PORTFOLIO_FILE", pf):
            write_portfolio([{"ticker": "AAPL", "shares": 10, "cost_basis": 100}], "csv")

            result = json.loads(pf.read_text())
            assert result["cash"] == 15000
            assert result["options_positions"] == []


# ---------------------------------------------------------------------------
# Fallback logic
# ---------------------------------------------------------------------------

class TestFallbackLogic:
    def test_api_failure_falls_back_to_csv(self, sample_csv, tmp_path, capsys):
        """When API fails and a CSV exists at default path, CSV is used."""
        from scripts import sync_portfolio as sp

        pf = tmp_path / "portfolio.json"
        pf.write_text(json.dumps({"holdings": [], "cash": 0, "options_positions": []}))

        with (
            patch.object(sp, "sync_from_api", side_effect=ValueError("No tokens")),
            patch.object(sp, "DEFAULT_CSV", sample_csv),
            patch.object(sp, "PORTFOLIO_FILE", pf),
            patch("sys.argv", ["sync_portfolio.py"]),
        ):
            sp.main()

        result = json.loads(pf.read_text())
        assert len(result["holdings"]) == 3

    def test_both_fail_uses_existing(self, tmp_path, capsys):
        """When API and CSV both fail, existing portfolio.json is untouched."""
        from scripts import sync_portfolio as sp

        original = {"holdings": [{"ticker": "VOO", "shares": 25, "cost_basis": 420}],
                     "cash": 5000, "options_positions": []}
        pf = tmp_path / "portfolio.json"
        pf.write_text(json.dumps(original))

        with (
            patch.object(sp, "sync_from_api", side_effect=ValueError("No tokens")),
            patch.object(sp, "DEFAULT_CSV", tmp_path / "nonexistent.csv"),
            patch.object(sp, "PORTFOLIO_FILE", pf),
            patch("sys.argv", ["sync_portfolio.py"]),
        ):
            sp.main()

        result = json.loads(pf.read_text())
        assert result == original


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_no_write(self, portfolio_dir, capsys):
        """--dry-run prints output but doesn't modify portfolio.json."""
        pf = portfolio_dir / "config" / "portfolio.json"
        original = pf.read_text()

        with patch("scripts.sync_portfolio.PORTFOLIO_FILE", pf):
            new_holdings = [{"ticker": "TSLA", "shares": 10, "cost_basis": 200}]
            write_portfolio(new_holdings, "csv", dry_run=True)

        assert pf.read_text() == original

        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out
        assert "TSLA" in captured.out


# ---------------------------------------------------------------------------
# Token freshness
# ---------------------------------------------------------------------------

class TestTokenFreshness:
    def test_no_token_file(self, tmp_path):
        """Missing token file means not fresh."""
        with patch("scripts.sync_portfolio.TOKEN_FILE", tmp_path / "missing.json"):
            assert tokens_are_fresh() is False

    def test_stale_token(self, tmp_path):
        """Token from yesterday is not fresh."""
        tf = tmp_path / "tokens.json"
        tf.write_text(json.dumps({
            "access_token": "x",
            "access_token_secret": "y",
            "obtained_at": "2020-01-01T09:00:00-07:00",
        }))
        with patch("scripts.sync_portfolio.TOKEN_FILE", tf):
            assert tokens_are_fresh() is False

    def test_today_token(self, tmp_path):
        """Token from today is fresh."""
        from datetime import datetime
        tf = tmp_path / "tokens.json"
        tf.write_text(json.dumps({
            "access_token": "x",
            "access_token_secret": "y",
            "obtained_at": datetime.now().astimezone().isoformat(),
        }))
        with patch("scripts.sync_portfolio.TOKEN_FILE", tf):
            assert tokens_are_fresh() is True


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_build_trading_window_map(self):
        portfolio = {
            "holdings": [
                {"ticker": "AAPL", "shares": 100, "cost_basis": 178},
                {"ticker": "NVDA", "shares": 50, "cost_basis": 485,
                 "trading_window": {"open": "2026-02-27", "close": "2026-04-15"}},
            ]
        }
        windows = build_trading_window_map(portfolio)
        assert "NVDA" in windows
        assert "AAPL" not in windows
        assert windows["NVDA"]["open"] == "2026-02-27"

    def test_report_changes_added_removed(self, capsys):
        old = {"holdings": [
            {"ticker": "AAPL", "shares": 100, "cost_basis": 178},
            {"ticker": "NVDA", "shares": 50, "cost_basis": 485},
        ]}
        new = [
            {"ticker": "NVDA", "shares": 60, "cost_basis": 485},
            {"ticker": "TSLA", "shares": 10, "cost_basis": 200},
        ]
        report_changes(old, new)
        out = capsys.readouterr().out
        assert "TSLA" in out   # added
        assert "AAPL" in out   # removed
        assert "60" in out     # share change
