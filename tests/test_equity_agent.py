"""Comprehensive tests for the equity trading agent modules.

Tests equity_config, equity_risk_gate, equity_db, and equity_strategies
using mocks for all external dependencies (Alpaca, yfinance, signal files).
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import sqlite3
import pytest
from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock


# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture
def equity_db(tmp_path, monkeypatch):
    """Create a fresh SQLite database in tmp_path for each test."""
    db_path = tmp_path / "equity_trades.db"
    import agent.equity_db as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)
    db_mod.init_db()
    return db_mod


@pytest.fixture
def sample_proposal():
    """A valid trade proposal that should pass all risk checks."""
    return {
        "strategy": "congressional_conviction",
        "ticker": "AAPL",
        "side": "BUY",
        "limit_price": 195.0,
        "conviction": 5,
        "sector": "Technology",
        "reason": "test proposal",
    }


@pytest.fixture
def mock_envelope_dir(tmp_path):
    """Create mock signal envelopes for strategy tests."""
    processed = tmp_path / "processed"
    processed.mkdir()

    signals = {
        "technical_signals.json": {
            "module": "technical_signals",
            "generated_at": "2026-04-12T08:00:00-07:00",
            "status": "success",
            "error_message": None,
            "data": {
                "results": [
                    {
                        "ticker": "AAPL",
                        "composite_score": -0.5,
                        "rsi_14": 25.0,  # oversold
                        "macd_signal": "bullish_crossover",
                        "bb_position": "below_lower",
                        "sma_trend": "above_200",
                        "volume_ratio": 0.9,
                        "vwap": 193.20,
                        "stochastic_signal": "oversold",
                    },
                    {
                        "ticker": "NVDA",
                        "composite_score": 0.1,
                        "rsi_14": 75.0,  # overbought
                        "macd_signal": "bearish_crossover",
                        "bb_position": "above_upper",
                        "sma_trend": "golden_cross",
                        "volume_ratio": 1.2,
                        "vwap": 900.0,
                        "stochastic_signal": "overbought",
                    },
                    {
                        "ticker": "MSFT",
                        "composite_score": 0.3,
                        "rsi_14": 50.0,  # neutral — should NOT generate a proposal
                        "macd_signal": "neutral",
                        "bb_position": "middle",
                        "sma_trend": "above_200",
                        "volume_ratio": 1.0,
                        "vwap": 420.0,
                    },
                    # ETF prices for sector rotation strategy
                    {"ticker": "XLK", "composite_score": 0.5, "rsi_14": 55.0, "vwap": 220.50,
                     "macd_signal": "neutral", "bb_position": "middle"},
                    {"ticker": "XLY", "composite_score": 0.3, "rsi_14": 52.0, "vwap": 185.00,
                     "macd_signal": "neutral", "bb_position": "middle"},
                    {"ticker": "XLC", "composite_score": 0.2, "rsi_14": 50.0, "vwap": 82.00,
                     "macd_signal": "neutral", "bb_position": "middle"},
                    {"ticker": "XLE", "composite_score": -0.3, "rsi_14": 40.0, "vwap": 65.20,
                     "macd_signal": "neutral", "bb_position": "middle"},
                    {"ticker": "XLU", "composite_score": -0.1, "rsi_14": 45.0, "vwap": 70.00,
                     "macd_signal": "neutral", "bb_position": "middle"},
                ]
            },
        },
        "congress_tracker.json": {
            "module": "congress_tracker",
            "generated_at": "2026-04-12T08:00:00-07:00",
            "status": "success",
            "error_message": None,
            "data": {
                "cluster_signals": [
                    {
                        "ticker": "AAPL",
                        "direction": "buy",
                        "signal": "strong_bullish",
                        "politician_count": 3,
                        "estimated_total": 500000,
                        "politicians": ["Rep A", "Rep B", "Rep C"],
                    },
                ]
            },
        },
        "earnings_tone.json": {
            "module": "earnings_tone",
            "generated_at": "2026-04-12T08:00:00-07:00",
            "status": "success",
            "error_message": None,
            "data": {
                "results": [
                    {"ticker": "AAPL", "tone_score": 2.0},
                ]
            },
        },
        "valuation.json": {
            "module": "valuation",
            "generated_at": "2026-04-12T08:00:00-07:00",
            "status": "success",
            "error_message": None,
            "data": {
                "comparisons": [
                    {"stock_a": "AAPL", "stock_b": "MSFT", "cheaper": "AAPL"},
                ]
            },
        },
        "sector_rotation.json": {
            "module": "sector_rotation",
            "generated_at": "2026-04-12T08:00:00-07:00",
            "status": "success",
            "error_message": None,
            "data": {
                "signal": "growth_momentum",
                "leaders_21d": [
                    {"etf": "XLK", "sector": "Technology", "relative_strength": 3.1, "return_21d": 5.2, "trend": "uptrend"},
                    {"etf": "XLY", "sector": "Consumer Discretionary", "relative_strength": 2.7, "return_21d": 4.8, "trend": "uptrend"},
                    {"etf": "XLC", "sector": "Communication Services", "relative_strength": 1.9, "return_21d": 4.1, "trend": "uptrend"},
                ],
                "laggards_21d": [
                    {"etf": "XLE", "sector": "Energy", "relative_strength": -4.4, "return_21d": -2.3, "trend": "downtrend"},
                    {"etf": "XLU", "sector": "Utilities", "relative_strength": -2.0, "return_21d": 0.1, "trend": "downtrend"},
                ],
            },
        },
        "portfolio.json": {
            "module": "portfolio",
            "generated_at": "2026-04-12T08:00:00-07:00",
            "status": "success",
            "error_message": None,
            "data": {
                "holdings": [
                    {"ticker": "NVDA", "current_price": 900.0},
                    {"ticker": "AAPL", "current_price": 195.0},
                ],
                "correlations": {"NVDA_AAPL": 0.72},
            },
        },
    }

    for filename, envelope in signals.items():
        (processed / filename).write_text(json.dumps(envelope, indent=2))

    return processed


def _patch_load_envelope(processed_dir):
    """Return a mock load_envelope that reads from processed_dir."""
    def _mock_load(filename):
        path = processed_dir / filename
        if path.exists():
            return json.loads(path.read_text())
        return {
            "module": "unknown",
            "generated_at": None,
            "status": "missing",
            "error_message": "File not found",
            "data": {},
        }
    return _mock_load


# ============================================================
# 1. EQUITY CONFIG TESTS
# ============================================================

class TestEquityConfig:
    """Tests for agent/equity_config.py"""

    def test_hard_limits_values(self):
        """Verify hard limits have expected values."""
        from agent.equity_config import (
            MAX_SINGLE_POSITION_PCT, MAX_TOTAL_EXPOSURE_PCT,
            MAX_CONCURRENT_POSITIONS, MIN_CONVICTION_SCORE,
            MAX_SECTOR_EXPOSURE_PCT, MAX_CORRELATION,
            MAX_DAILY_LOSS_PCT, MAX_DRAWDOWN_PCT,
        )
        assert MAX_SINGLE_POSITION_PCT == 0.05
        assert MAX_TOTAL_EXPOSURE_PCT == 0.80
        assert MAX_CONCURRENT_POSITIONS == 5
        assert MIN_CONVICTION_SCORE == 3
        assert MAX_SECTOR_EXPOSURE_PCT == 0.30
        assert MAX_CORRELATION == 0.80
        assert MAX_DAILY_LOSS_PCT == 0.015
        assert MAX_DRAWDOWN_PCT == 0.05

    def test_load_equity_config_defaults(self, tmp_path, monkeypatch):
        """load_equity_config returns defaults when no override file exists."""
        import agent.equity_config as cfg
        monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
        params = cfg.load_equity_config()
        assert "congressional_conviction" in params
        assert "technical_reversion" in params
        assert "sector_rotation" in params
        assert params["congressional_conviction"]["min_cluster_size"] == 2
        assert params["technical_reversion"]["rsi_oversold"] == 30

    def test_load_equity_config_with_overrides(self, tmp_path, monkeypatch):
        """load_equity_config merges saved overrides on top of defaults."""
        import agent.equity_config as cfg
        monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        overrides = {"congressional_conviction": {"min_cluster_size": 5, "weight": 1.5}}
        (agent_dir / "equity_strategy_params.json").write_text(json.dumps(overrides))

        params = cfg.load_equity_config()
        assert params["congressional_conviction"]["min_cluster_size"] == 5
        assert params["congressional_conviction"]["weight"] == 1.5
        # Other defaults should still be present
        assert params["congressional_conviction"]["min_amount"] == 100000

    def test_load_equity_config_bad_json(self, tmp_path, monkeypatch):
        """load_equity_config gracefully handles corrupt override file."""
        import agent.equity_config as cfg
        monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "equity_strategy_params.json").write_text("not valid json{{{")

        params = cfg.load_equity_config()
        # Should return defaults without crashing
        assert params["congressional_conviction"]["min_cluster_size"] == 2

    def test_load_equity_config_returns_all_strategy_keys(self):
        """Every default strategy should be present in config output."""
        from agent.equity_config import load_equity_config, DEFAULT_EQUITY_STRATEGY_PARAMS
        params = load_equity_config()
        for key in DEFAULT_EQUITY_STRATEGY_PARAMS:
            assert key in params

    def test_ticker_sector_mapping(self):
        """Verify major tickers have sector mappings."""
        from agent.equity_config import TICKER_SECTOR
        assert TICKER_SECTOR["AAPL"] == "Technology"
        assert TICKER_SECTOR["GOOGL"] == "Communication Services"
        assert TICKER_SECTOR["AMZN"] == "Consumer Discretionary"
        assert TICKER_SECTOR["VOO"] == "Broad Market"

    def test_save_equity_params(self, tmp_path, monkeypatch):
        """save_equity_params writes valid JSON."""
        import agent.equity_config as cfg
        monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        params = {"congressional_conviction": {"weight": 2.0}}
        cfg.save_equity_params(params)
        saved = json.loads((agent_dir / "equity_strategy_params.json").read_text())
        assert saved["congressional_conviction"]["weight"] == 2.0


# ============================================================
# 2. EQUITY DB TESTS
# ============================================================

class TestEquityDb:
    """Tests for agent/equity_db.py"""

    def test_init_db_creates_tables(self, equity_db, tmp_path):
        """init_db creates trades, daily_equity, and wash_sales tables."""
        db_path = tmp_path / "equity_trades.db"
        conn = sqlite3.connect(str(db_path))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        conn.close()
        assert "trades" in table_names
        assert "daily_equity" in table_names
        assert "wash_sales" in table_names

    def test_init_db_idempotent(self, equity_db):
        """Calling init_db twice doesn't raise."""
        equity_db.init_db()  # Second call should be safe

    def test_record_trade_returns_id(self, equity_db):
        """record_trade inserts a trade and returns a positive integer ID."""
        trade = {
            "ticker": "AAPL",
            "side": "BUY",
            "quantity": 10,
            "price": 195.0,
            "strategy": "test",
            "reason": "unit test",
            "conviction": 5,
            "sector": "Technology",
        }
        row_id = equity_db.record_trade(trade)
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_record_trade_multiple(self, equity_db):
        """Multiple trades get distinct IDs."""
        ids = []
        for ticker in ["AAPL", "NVDA", "MSFT"]:
            row_id = equity_db.record_trade({
                "ticker": ticker, "side": "BUY", "quantity": 5, "price": 100.0,
            })
            ids.append(row_id)
        assert len(set(ids)) == 3

    def test_get_trades_recent(self, equity_db):
        """get_trades returns trades from within the lookback window."""
        equity_db.record_trade({
            "ticker": "AAPL", "side": "BUY", "quantity": 10, "price": 195.0,
        })
        trades = equity_db.get_trades(days=30)
        assert len(trades) == 1
        assert trades[0]["ticker"] == "AAPL"

    def test_get_trades_empty(self, equity_db):
        """get_trades returns empty list on clean db."""
        trades = equity_db.get_trades(days=30)
        assert trades == []

    def test_get_todays_trades(self, equity_db):
        """get_todays_trades only returns trades from today."""
        equity_db.record_trade({
            "ticker": "AAPL", "side": "BUY", "quantity": 10, "price": 195.0,
            "timestamp": datetime.now().astimezone().isoformat(),
        })
        todays = equity_db.get_todays_trades()
        assert len(todays) == 1
        assert todays[0]["ticker"] == "AAPL"

    def test_check_wash_sale_clean(self, equity_db):
        """check_wash_sale returns None when no wash sale exists."""
        result = equity_db.check_wash_sale("AAPL")
        assert result is None

    def test_check_wash_sale_blocked(self, equity_db):
        """check_wash_sale returns dict when ticker is in wash sale window."""
        equity_db.record_wash_sale("AAPL", 180.0, -500.0)
        result = equity_db.check_wash_sale("AAPL")
        assert result is not None
        assert result["ticker"] == "AAPL"
        assert "rebuy_window_end" in result

    def test_record_wash_sale_no_loss(self, equity_db):
        """record_wash_sale does nothing when loss_amount >= 0 (no actual loss)."""
        equity_db.record_wash_sale("AAPL", 200.0, 100.0)
        result = equity_db.check_wash_sale("AAPL")
        assert result is None

    def test_wash_sale_different_tickers(self, equity_db):
        """Wash sale for AAPL doesn't block NVDA."""
        equity_db.record_wash_sale("AAPL", 180.0, -500.0)
        assert equity_db.check_wash_sale("AAPL") is not None
        assert equity_db.check_wash_sale("NVDA") is None

    def test_get_open_positions_from_trades(self, equity_db):
        """Net positions calculated correctly from buy/sell history."""
        equity_db.record_trade({
            "ticker": "AAPL", "side": "BUY", "quantity": 10, "price": 190.0,
            "sector": "Technology", "status": "filled",
        })
        equity_db.record_trade({
            "ticker": "AAPL", "side": "SELL", "quantity": 3, "price": 200.0,
            "sector": "Technology", "status": "filled",
        })
        positions = equity_db.get_open_positions_from_trades()
        assert "AAPL" in positions
        assert positions["AAPL"]["qty"] == 7  # 10 bought - 3 sold

    def test_closed_position_not_in_open(self, equity_db):
        """Fully closed position doesn't appear in open positions."""
        equity_db.record_trade({
            "ticker": "NVDA", "side": "BUY", "quantity": 5, "price": 800.0,
            "sector": "Technology", "status": "filled",
        })
        equity_db.record_trade({
            "ticker": "NVDA", "side": "SELL", "quantity": 5, "price": 850.0,
            "sector": "Technology", "status": "filled",
        })
        positions = equity_db.get_open_positions_from_trades()
        assert "NVDA" not in positions

    def test_get_current_drawdown_empty(self, equity_db):
        """get_current_drawdown returns 0 with no equity history."""
        assert equity_db.get_current_drawdown() == 0

    def test_get_drawdown_pause_until_none(self, equity_db):
        """get_drawdown_pause_until returns None when no breaches exist."""
        assert equity_db.get_drawdown_pause_until() is None

    def test_record_daily_equity(self, equity_db):
        """record_daily_equity inserts a row successfully."""
        equity_db.record_daily_equity(100000, 20000, 80000)
        conn = equity_db.get_conn()
        row = conn.execute("SELECT * FROM daily_equity").fetchone()
        conn.close()
        assert row is not None
        assert row["portfolio_value"] == 100000

    def test_get_performance_metrics_empty(self, equity_db):
        """Performance metrics return zeros on empty db."""
        metrics = equity_db.get_performance_metrics()
        assert metrics["trades"] == 0
        assert metrics["sharpe"] == 0


# ============================================================
# 3. EQUITY RISK GATE TESTS
# ============================================================

class TestEquityRiskGate:
    """Tests for agent/equity_risk_gate.py"""

    def _check(self, proposal, portfolio_value=100000, force=True):
        """Helper to call check_proposal with common mocks."""
        with patch("agent.equity_risk_gate.get_positions", return_value=[]), \
             patch("agent.equity_risk_gate.get_account", return_value={"equity": portfolio_value}), \
             patch("agent.equity_risk_gate.get_todays_trades", return_value=[]), \
             patch("agent.equity_risk_gate.check_wash_sale", return_value=None), \
             patch("agent.equity_risk_gate.get_drawdown_pause_until", return_value=None), \
             patch("agent.equity_risk_gate._get_portfolio_correlation", return_value=None):
            from agent.equity_risk_gate import check_proposal
            return check_proposal(proposal, portfolio_value, force=force)

    def test_approved_valid_proposal(self, sample_proposal):
        """A valid proposal with force=True gets approved."""
        result = self._check(sample_proposal)
        assert result["approved"] is True

    def test_market_hours_rejected_when_closed(self, sample_proposal):
        """Proposal rejected when market closed and force=False."""
        with patch("agent.equity_risk_gate._is_market_hours", return_value=False), \
             patch("agent.equity_risk_gate.get_positions", return_value=[]), \
             patch("agent.equity_risk_gate.get_account", return_value={"equity": 100000}), \
             patch("agent.equity_risk_gate.get_todays_trades", return_value=[]), \
             patch("agent.equity_risk_gate.check_wash_sale", return_value=None), \
             patch("agent.equity_risk_gate.get_drawdown_pause_until", return_value=None), \
             patch("agent.equity_risk_gate._get_portfolio_correlation", return_value=None):
            from agent.equity_risk_gate import check_proposal
            result = check_proposal(sample_proposal, 100000, force=False)
        assert result["approved"] is False
        assert "Market closed" in result["reason"]

    def test_market_hours_force_bypasses(self, sample_proposal):
        """force=True bypasses market hours check."""
        with patch("agent.equity_risk_gate._is_market_hours", return_value=False):
            result = self._check(sample_proposal, force=True)
        assert result["approved"] is True

    def test_low_conviction_rejected(self, sample_proposal):
        """Conviction below MIN_CONVICTION_SCORE (3) is rejected."""
        sample_proposal["conviction"] = 2
        result = self._check(sample_proposal)
        assert result["approved"] is False
        assert "Conviction" in result["reason"]

    def test_conviction_at_minimum_passes(self, sample_proposal):
        """Conviction exactly at MIN_CONVICTION_SCORE passes."""
        sample_proposal["conviction"] = 3
        result = self._check(sample_proposal)
        assert result["approved"] is True

    def test_position_size_capped(self, sample_proposal):
        """Position exceeding 5% of portfolio is sized down, not rejected."""
        # 5% of 100k = $5000. Price 195 means max ~25.6 shares
        # Setting a large quantity_override should be capped
        sample_proposal["quantity_override"] = 100  # 100 * 195 = $19,500
        result = self._check(sample_proposal, portfolio_value=100000)
        assert result["approved"] is True
        # Adjusted quantity should be around 5000/195 ~ 25.64
        assert result["adjusted_quantity"] < 30

    def test_total_exposure_rejected(self, sample_proposal):
        """BUY rejected when total exposure already at 80% cap."""
        existing_positions = [
            {"ticker": "NVDA", "market_value": 40000, "qty": 50, "avg_cost": 800},
            {"ticker": "MSFT", "market_value": 40000, "qty": 100, "avg_cost": 400},
        ]
        with patch("agent.equity_risk_gate.get_positions", return_value=existing_positions), \
             patch("agent.equity_risk_gate.get_account", return_value={"equity": 100000}), \
             patch("agent.equity_risk_gate.get_todays_trades", return_value=[]), \
             patch("agent.equity_risk_gate.check_wash_sale", return_value=None), \
             patch("agent.equity_risk_gate.get_drawdown_pause_until", return_value=None), \
             patch("agent.equity_risk_gate._get_portfolio_correlation", return_value=None):
            from agent.equity_risk_gate import check_proposal
            result = check_proposal(sample_proposal, 100000, force=True)
        assert result["approved"] is False
        assert "exposure" in result["reason"].lower() or "cap" in result["reason"].lower()

    def test_max_concurrent_positions_rejected(self, sample_proposal):
        """BUY rejected when 5 different positions already open."""
        five_positions = [
            {"ticker": t, "market_value": 5000, "qty": 10, "avg_cost": 500}
            for t in ["NVDA", "MSFT", "GOOGL", "AMD", "META"]
        ]
        sample_proposal["ticker"] = "TSLA"  # New ticker not in positions
        with patch("agent.equity_risk_gate.get_positions", return_value=five_positions), \
             patch("agent.equity_risk_gate.get_account", return_value={"equity": 100000}), \
             patch("agent.equity_risk_gate.get_todays_trades", return_value=[]), \
             patch("agent.equity_risk_gate.check_wash_sale", return_value=None), \
             patch("agent.equity_risk_gate.get_drawdown_pause_until", return_value=None), \
             patch("agent.equity_risk_gate._get_portfolio_correlation", return_value=None):
            from agent.equity_risk_gate import check_proposal
            result = check_proposal(sample_proposal, 100000, force=True)
        assert result["approved"] is False
        assert "positions open" in result["reason"] or "max" in result["reason"].lower()

    def test_max_concurrent_allows_adding_to_existing(self, sample_proposal):
        """Adding to an existing position is OK even at max concurrent."""
        five_positions = [
            {"ticker": t, "market_value": 5000, "qty": 10, "avg_cost": 500}
            for t in ["AAPL", "MSFT", "GOOGL", "AMD", "META"]
        ]
        sample_proposal["ticker"] = "AAPL"  # Already held
        with patch("agent.equity_risk_gate.get_positions", return_value=five_positions), \
             patch("agent.equity_risk_gate.get_account", return_value={"equity": 100000}), \
             patch("agent.equity_risk_gate.get_todays_trades", return_value=[]), \
             patch("agent.equity_risk_gate.check_wash_sale", return_value=None), \
             patch("agent.equity_risk_gate.get_drawdown_pause_until", return_value=None), \
             patch("agent.equity_risk_gate._get_portfolio_correlation", return_value=None):
            from agent.equity_risk_gate import check_proposal
            result = check_proposal(sample_proposal, 100000, force=True)
        assert result["approved"] is True

    def test_sector_concentration_rejected(self, sample_proposal):
        """BUY rejected when sector already at 30% cap."""
        # 30% of 100k = $30k. Already $30k in Technology.
        tech_positions = [
            {"ticker": "NVDA", "market_value": 15000, "qty": 20, "avg_cost": 750},
            {"ticker": "MSFT", "market_value": 15000, "qty": 40, "avg_cost": 375},
        ]
        sample_proposal["ticker"] = "AAPL"
        sample_proposal["sector"] = "Technology"
        with patch("agent.equity_risk_gate.get_positions", return_value=tech_positions), \
             patch("agent.equity_risk_gate.get_account", return_value={"equity": 100000}), \
             patch("agent.equity_risk_gate.get_todays_trades", return_value=[]), \
             patch("agent.equity_risk_gate.check_wash_sale", return_value=None), \
             patch("agent.equity_risk_gate.get_drawdown_pause_until", return_value=None), \
             patch("agent.equity_risk_gate._get_portfolio_correlation", return_value=None):
            from agent.equity_risk_gate import check_proposal
            result = check_proposal(sample_proposal, 100000, force=True)
        assert result["approved"] is False
        assert "Sector" in result["reason"] or "sector" in result["reason"]

    def test_duplicate_same_ticker_side_rejected(self, sample_proposal):
        """Same ticker+side already traded today is rejected."""
        todays = [{"ticker": "AAPL", "side": "BUY"}]
        with patch("agent.equity_risk_gate.get_positions", return_value=[]), \
             patch("agent.equity_risk_gate.get_account", return_value={"equity": 100000}), \
             patch("agent.equity_risk_gate.get_todays_trades", return_value=todays), \
             patch("agent.equity_risk_gate.check_wash_sale", return_value=None), \
             patch("agent.equity_risk_gate.get_drawdown_pause_until", return_value=None), \
             patch("agent.equity_risk_gate._get_portfolio_correlation", return_value=None):
            from agent.equity_risk_gate import check_proposal
            result = check_proposal(sample_proposal, 100000, force=True)
        assert result["approved"] is False
        assert "duplicate" in result["reason"].lower() or "already" in result["reason"].lower()

    def test_duplicate_different_side_allowed(self, sample_proposal):
        """BUY AAPL today + SELL AAPL today is allowed (different side)."""
        todays = [{"ticker": "AAPL", "side": "SELL"}]
        with patch("agent.equity_risk_gate.get_positions", return_value=[]), \
             patch("agent.equity_risk_gate.get_account", return_value={"equity": 100000}), \
             patch("agent.equity_risk_gate.get_todays_trades", return_value=todays), \
             patch("agent.equity_risk_gate.check_wash_sale", return_value=None), \
             patch("agent.equity_risk_gate.get_drawdown_pause_until", return_value=None), \
             patch("agent.equity_risk_gate._get_portfolio_correlation", return_value=None):
            from agent.equity_risk_gate import check_proposal
            result = check_proposal(sample_proposal, 100000, force=True)
        assert result["approved"] is True

    def test_minimum_trade_size_rejected(self, sample_proposal):
        """Trade under $10 is rejected."""
        sample_proposal["limit_price"] = 0.50
        sample_proposal["quantity_override"] = 1  # $0.50 trade
        result = self._check(sample_proposal)
        assert result["approved"] is False
        assert "minimum" in result["reason"].lower() or "too small" in result["reason"].lower()

    def test_wash_sale_blocking(self, sample_proposal):
        """BUY rejected when wash sale window is active."""
        wash = {
            "ticker": "AAPL",
            "sell_date": "2026-04-01",
            "rebuy_window_end": "2026-05-01",
        }
        with patch("agent.equity_risk_gate.get_positions", return_value=[]), \
             patch("agent.equity_risk_gate.get_account", return_value={"equity": 100000}), \
             patch("agent.equity_risk_gate.get_todays_trades", return_value=[]), \
             patch("agent.equity_risk_gate.check_wash_sale", return_value=wash), \
             patch("agent.equity_risk_gate.get_drawdown_pause_until", return_value=None), \
             patch("agent.equity_risk_gate._get_portfolio_correlation", return_value=None):
            from agent.equity_risk_gate import check_proposal
            result = check_proposal(sample_proposal, 100000, force=True)
        assert result["approved"] is False
        assert "wash" in result["reason"].lower() or "Wash" in result["reason"]

    def test_drawdown_pause_blocks_buys(self, sample_proposal):
        """BUY rejected during drawdown pause period."""
        with patch("agent.equity_risk_gate.get_positions", return_value=[]), \
             patch("agent.equity_risk_gate.get_account", return_value={"equity": 100000}), \
             patch("agent.equity_risk_gate.get_todays_trades", return_value=[]), \
             patch("agent.equity_risk_gate.check_wash_sale", return_value=None), \
             patch("agent.equity_risk_gate.get_drawdown_pause_until", return_value="2026-04-15"), \
             patch("agent.equity_risk_gate._get_portfolio_correlation", return_value=None), \
             patch("agent.equity_risk_gate._is_market_hours", return_value=True):
            from agent.equity_risk_gate import check_proposal
            result = check_proposal(sample_proposal, 100000, force=True)
        assert result["approved"] is False
        assert "Drawdown" in result["reason"] or "drawdown" in result["reason"]

    def test_sell_allowed_during_drawdown_pause(self):
        """SELL orders should pass even during drawdown pause."""
        proposal = {
            "ticker": "AAPL", "side": "SELL", "limit_price": 195.0,
            "conviction": 5, "sector": "Technology", "reason": "stop loss",
            "quantity_override": 10,
        }
        with patch("agent.equity_risk_gate.get_positions", return_value=[]), \
             patch("agent.equity_risk_gate.get_account", return_value={"equity": 100000}), \
             patch("agent.equity_risk_gate.get_todays_trades", return_value=[]), \
             patch("agent.equity_risk_gate.check_wash_sale", return_value=None), \
             patch("agent.equity_risk_gate.get_drawdown_pause_until", return_value="2026-04-15"), \
             patch("agent.equity_risk_gate._get_portfolio_correlation", return_value=None), \
             patch("agent.equity_risk_gate._is_market_hours", return_value=True):
            from agent.equity_risk_gate import check_proposal
            result = check_proposal(proposal, 100000, force=True)
        assert result["approved"] is True

    def test_correlation_exceeds_limit_rejected(self, sample_proposal):
        """BUY rejected when correlation with existing positions > 0.80."""
        with patch("agent.equity_risk_gate.get_positions", return_value=[]), \
             patch("agent.equity_risk_gate.get_account", return_value={"equity": 100000}), \
             patch("agent.equity_risk_gate.get_todays_trades", return_value=[]), \
             patch("agent.equity_risk_gate.check_wash_sale", return_value=None), \
             patch("agent.equity_risk_gate.get_drawdown_pause_until", return_value=None), \
             patch("agent.equity_risk_gate._get_portfolio_correlation", return_value=0.92):
            from agent.equity_risk_gate import check_proposal
            result = check_proposal(sample_proposal, 100000, force=True)
        assert result["approved"] is False
        assert "correlation" in result["reason"].lower()

    def test_filter_proposals_returns_approved_only(self, sample_proposal):
        """filter_proposals only returns approved proposals."""
        proposals = [
            sample_proposal,
            {**sample_proposal, "ticker": "NVDA", "conviction": 1},  # Too low
        ]
        with patch("agent.equity_risk_gate.get_positions", return_value=[]), \
             patch("agent.equity_risk_gate.get_account", return_value={"equity": 100000}), \
             patch("agent.equity_risk_gate.get_todays_trades", return_value=[]), \
             patch("agent.equity_risk_gate.check_wash_sale", return_value=None), \
             patch("agent.equity_risk_gate.get_drawdown_pause_until", return_value=None), \
             patch("agent.equity_risk_gate._get_portfolio_correlation", return_value=None), \
             patch("agent.equity_risk_gate._is_market_hours", return_value=True):
            from agent.equity_risk_gate import filter_proposals
            approved = filter_proposals(proposals, 100000, force=True)
        assert len(approved) == 1
        assert approved[0]["ticker"] == "AAPL"


# ============================================================
# 4. EQUITY STRATEGIES TESTS
# ============================================================

class TestEquityStrategies:
    """Tests for agent/equity_strategies.py"""

    def test_run_all_returns_list(self, mock_envelope_dir):
        """run_all_equity_strategies returns a list."""
        with patch("agent.equity_strategies.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)), \
             patch("lib.data_envelope.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)):
            from agent.equity_strategies import run_all_equity_strategies
            from agent.equity_config import DEFAULT_EQUITY_STRATEGY_PARAMS
            proposals = run_all_equity_strategies(DEFAULT_EQUITY_STRATEGY_PARAMS)
        assert isinstance(proposals, list)

    def test_proposals_have_required_keys(self, mock_envelope_dir):
        """Every proposal must have the required keys."""
        required = {"ticker", "side", "limit_price", "conviction", "strategy", "sector", "reason"}
        with patch("agent.equity_strategies.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)), \
             patch("lib.data_envelope.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)):
            from agent.equity_strategies import run_all_equity_strategies
            from agent.equity_config import DEFAULT_EQUITY_STRATEGY_PARAMS
            proposals = run_all_equity_strategies(DEFAULT_EQUITY_STRATEGY_PARAMS)
        for p in proposals:
            missing = required - set(p.keys())
            assert not missing, f"Proposal for {p.get('ticker', '?')} missing keys: {missing}"

    def test_technical_reversion_oversold_buy(self, mock_envelope_dir):
        """Oversold RSI generates a BUY proposal."""
        with patch("agent.equity_strategies.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)), \
             patch("lib.data_envelope.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)):
            from agent.equity_strategies import scan_technical_reversion
            proposals = scan_technical_reversion({"rsi_oversold": 30, "rsi_overbought": 70, "bb_confirmation": True})
        buys = [p for p in proposals if p["side"] == "BUY"]
        assert any(p["ticker"] == "AAPL" for p in buys), "AAPL (RSI 25) should generate oversold BUY"

    def test_technical_reversion_overbought_sell(self, mock_envelope_dir):
        """Overbought RSI generates a SELL proposal."""
        with patch("agent.equity_strategies.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)), \
             patch("lib.data_envelope.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)):
            from agent.equity_strategies import scan_technical_reversion
            proposals = scan_technical_reversion({"rsi_oversold": 30, "rsi_overbought": 70, "bb_confirmation": True})
        sells = [p for p in proposals if p["side"] == "SELL"]
        assert any(p["ticker"] == "NVDA" for p in sells), "NVDA (RSI 75) should generate overbought SELL"

    def test_technical_reversion_neutral_skipped(self, mock_envelope_dir):
        """Neutral RSI (50) should NOT generate a proposal."""
        with patch("agent.equity_strategies.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)), \
             patch("lib.data_envelope.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)):
            from agent.equity_strategies import scan_technical_reversion
            proposals = scan_technical_reversion({"rsi_oversold": 30, "rsi_overbought": 70})
        tickers = [p["ticker"] for p in proposals]
        assert "MSFT" not in tickers, "MSFT (RSI 50, neutral) should not generate a proposal"

    def test_congressional_conviction_generates_proposal(self, mock_envelope_dir):
        """Strong congressional buying cluster generates a BUY proposal."""
        with patch("agent.equity_strategies.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)), \
             patch("lib.data_envelope.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)):
            from agent.equity_strategies import scan_congressional_conviction
            proposals = scan_congressional_conviction({"min_cluster_size": 2, "min_amount": 100000})
        assert len(proposals) > 0
        assert proposals[0]["ticker"] == "AAPL"
        assert proposals[0]["side"] == "BUY"
        assert proposals[0]["conviction"] >= 3

    def test_congressional_no_data_returns_empty(self, mock_envelope_dir):
        """Congressional strategy returns [] when no congress data."""
        def _no_congress(filename):
            if "congress" in filename:
                return {"status": "missing", "data": {}}
            return _patch_load_envelope(mock_envelope_dir)(filename)

        with patch("agent.equity_strategies.load_envelope", side_effect=_no_congress), \
             patch("lib.data_envelope.load_envelope", side_effect=_no_congress):
            from agent.equity_strategies import scan_congressional_conviction
            proposals = scan_congressional_conviction({"min_cluster_size": 2, "min_amount": 100000})
        assert proposals == []

    def test_sector_rotation_generates_buy_for_leaders(self, mock_envelope_dir):
        """Sector rotation generates BUY for sector leaders with high RS."""
        with patch("agent.equity_strategies.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)), \
             patch("lib.data_envelope.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)), \
             patch("agent.equity_strategies._get_current_price", return_value=200.0):
            from agent.equity_strategies import scan_sector_rotation
            proposals = scan_sector_rotation({"top_n_sectors": 3, "min_relative_strength": 1.0, "position_per_sector_usd": 500})
        buys = [p for p in proposals if p["side"] == "BUY"]
        buy_tickers = [p["ticker"] for p in buys]
        assert "XLK" in buy_tickers, "XLK (RS 3.1) should be a leader buy"

    def test_sector_rotation_generates_sell_for_laggards(self, mock_envelope_dir):
        """Sector rotation generates SELL for deep laggards."""
        with patch("agent.equity_strategies.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)), \
             patch("lib.data_envelope.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)), \
             patch("agent.equity_strategies._get_current_price", return_value=50.0):
            from agent.equity_strategies import scan_sector_rotation
            proposals = scan_sector_rotation({"top_n_sectors": 3, "min_relative_strength": 1.0, "position_per_sector_usd": 500})
        sells = [p for p in proposals if p["side"] == "SELL"]
        sell_tickers = [p["ticker"] for p in sells]
        assert "XLE" in sell_tickers, "XLE (RS -4.4) should be a laggard sell"

    def test_deduplication_keeps_highest_conviction(self, mock_envelope_dir):
        """run_all deduplicates by (ticker, side), keeping highest conviction."""
        with patch("agent.equity_strategies.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)), \
             patch("lib.data_envelope.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)):
            from agent.equity_strategies import run_all_equity_strategies
            from agent.equity_config import DEFAULT_EQUITY_STRATEGY_PARAMS
            proposals = run_all_equity_strategies(DEFAULT_EQUITY_STRATEGY_PARAMS)

        # Check no duplicate (ticker, side) combos
        keys = [(p["ticker"], p["side"]) for p in proposals]
        assert len(keys) == len(set(keys)), "Proposals should be deduplicated by (ticker, side)"

    def test_proposals_sorted_by_conviction_desc(self, mock_envelope_dir):
        """Proposals are sorted by conviction descending."""
        with patch("agent.equity_strategies.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)), \
             patch("lib.data_envelope.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)):
            from agent.equity_strategies import run_all_equity_strategies
            from agent.equity_config import DEFAULT_EQUITY_STRATEGY_PARAMS
            proposals = run_all_equity_strategies(DEFAULT_EQUITY_STRATEGY_PARAMS)
        if len(proposals) >= 2:
            for i in range(len(proposals) - 1):
                assert proposals[i]["conviction"] >= proposals[i+1]["conviction"]

    def test_sector_rotation_no_data_returns_empty(self, mock_envelope_dir):
        """Sector rotation returns [] when no rotation data available."""
        def _no_rotation(filename):
            if "sector_rotation" in filename:
                return {"status": "missing", "data": {}}
            return _patch_load_envelope(mock_envelope_dir)(filename)

        with patch("agent.equity_strategies.load_envelope", side_effect=_no_rotation), \
             patch("lib.data_envelope.load_envelope", side_effect=_no_rotation):
            from agent.equity_strategies import scan_sector_rotation
            proposals = scan_sector_rotation({"top_n_sectors": 3, "min_relative_strength": 1.0})
        assert proposals == []

    def test_limit_price_always_positive(self, mock_envelope_dir):
        """Every proposal should have a positive limit_price."""
        with patch("agent.equity_strategies.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)), \
             patch("lib.data_envelope.load_envelope", side_effect=_patch_load_envelope(mock_envelope_dir)):
            from agent.equity_strategies import run_all_equity_strategies
            from agent.equity_config import DEFAULT_EQUITY_STRATEGY_PARAMS
            proposals = run_all_equity_strategies(DEFAULT_EQUITY_STRATEGY_PARAMS)
        for p in proposals:
            assert p["limit_price"] > 0, f"{p['ticker']} has non-positive limit_price"
