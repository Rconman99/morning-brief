"""Tests for backtester module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch
from datetime import datetime, timedelta

from modules.backtester import (
    load_verdict_log,
    get_price_on_date,
    get_returns_window,
    score_verdict,
    backtest_verdicts,
    main,
)


def make_mock_history(days: int = 100, base_price: float = 500.0, start_date: str = "2026-02-01") -> pd.DataFrame:
    """Generate mock price history DataFrame."""
    dates = pd.date_range(start=start_date, periods=days, freq="B")
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


def test_load_verdict_log_missing(tmp_path):
    """Missing verdict_log.json returns empty entries."""
    result = load_verdict_log(tmp_path / "missing.json")
    assert result == {"entries": []}


def test_load_verdict_log_corrupt(tmp_path):
    """Corrupt verdict_log.json returns empty entries."""
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{invalid json")
    result = load_verdict_log(bad_file)
    assert result == {"entries": []}


def test_load_verdict_log_valid(tmp_path):
    """Valid verdict_log.json loads correctly."""
    verdict_file = tmp_path / "verdicts.json"
    data = {
        "entries": [
            {
                "date": "2026-02-20",
                "ticker": "NVDA",
                "verdict": "BUY",
                "reason": "Test",
                "price_at_verdict": 180.0,
                "spy_price_at_verdict": 690.0,
            }
        ]
    }
    verdict_file.write_text(json.dumps(data))
    result = load_verdict_log(verdict_file)
    assert len(result["entries"]) == 1
    assert result["entries"][0]["ticker"] == "NVDA"


def test_get_price_on_date_exact():
    """Get price on exact date."""
    history = make_mock_history(30, 500.0, "2026-02-01")
    price = get_price_on_date(history, "2026-02-01")
    assert price is not None
    assert 450 < price < 550


def test_get_price_on_date_nearest():
    """Get price on nearest date within 5 days."""
    history = make_mock_history(30, 500.0, "2026-02-01")
    # Try a date not in the history but within 5 days
    price = get_price_on_date(history, "2026-02-02")  # Next day
    assert price is not None
    assert 450 < price < 550


def test_get_price_on_date_too_far():
    """Return None if target date is >5 days away."""
    history = make_mock_history(30, 500.0, "2026-02-01")
    # Try a date way beyond the 5-day window (more than 5 days after the last date)
    last_date = history.index[-1]
    far_date = last_date + timedelta(days=10)
    price = get_price_on_date(history, far_date.strftime("%Y-%m-%d"))
    assert price is None


def test_get_price_on_date_empty():
    """Return None for empty history."""
    history = pd.DataFrame()
    price = get_price_on_date(history, "2026-02-01")
    assert price is None


def test_get_returns_window_basic():
    """Calculate 5-day return from history."""
    history = make_mock_history(100, 500.0, "2026-02-01")
    # Get return starting from 2026-02-01 over 5 trading days
    ret = get_returns_window(history, "2026-02-01", 5)
    assert ret is not None
    assert isinstance(ret, float)
    # Should be reasonable (not extreme)
    assert -0.2 < ret < 0.2


def test_get_returns_window_empty():
    """Return None for empty history."""
    history = pd.DataFrame()
    ret = get_returns_window(history, "2026-02-01", 5)
    assert ret is None


def test_score_verdict_buy_win():
    """BUY verdict wins if ticker_return > 0."""
    assert score_verdict("BUY", 0.05, 0.01) == True
    assert score_verdict("BUY", 0.00, 0.01) == False  # 0 is not > 0
    assert score_verdict("BUY", -0.01, 0.01) == False


def test_score_verdict_buy_none_return():
    """BUY verdict with None return is a loss."""
    assert score_verdict("BUY", None, 0.01) == False


def test_score_verdict_sell_win():
    """SELL verdict wins if ticker_return < 0."""
    assert score_verdict("SELL", -0.05, -0.01) == True
    assert score_verdict("SELL", 0.01, 0.01) == False
    assert score_verdict("SELL", -0.01, 0.01) == True


def test_score_verdict_sell_none_return():
    """SELL verdict with None return is a loss."""
    assert score_verdict("SELL", None, -0.01) == False


def test_score_verdict_avoid_win():
    """AVOID verdict wins if ticker_return < spy_return."""
    assert score_verdict("AVOID", 0.01, 0.05) == True
    assert score_verdict("AVOID", 0.05, 0.01) == False
    assert score_verdict("AVOID", 0.05, 0.05) == False


def test_score_verdict_avoid_none_returns():
    """AVOID verdict with None returns is a loss."""
    assert score_verdict("AVOID", None, 0.05) == False
    assert score_verdict("AVOID", 0.01, None) == False


def test_score_verdict_hold():
    """HOLD verdict never wins (always False)."""
    assert score_verdict("HOLD", 0.05, 0.01) == False
    assert score_verdict("HOLD", -0.05, -0.01) == False


def test_score_verdict_review():
    """REVIEW verdict never wins (always False)."""
    assert score_verdict("REVIEW", 0.05, 0.01) == False
    assert score_verdict("REVIEW", -0.05, -0.01) == False


@patch("modules.backtester.yahoo_finance_price_history")
def test_backtest_verdicts_empty(mock_yf):
    """Backtest with no entries returns empty results."""
    analysis = backtest_verdicts([])
    assert analysis["total_verdicts_tested"] == 0
    assert analysis["summary"]["win_rate"] == 0.0
    assert analysis["summary"]["total_pnl"] == 0.0


@patch("modules.backtester.yahoo_finance_price_history")
def test_backtest_verdicts_single_buy_win(mock_yf):
    """Backtest single BUY verdict that wins."""
    # Mock returns positive return
    hist = make_mock_history(100, 500.0, "2026-02-01")
    # Manually set a positive return from 2026-02-01
    hist.loc[hist.index[5], "Close"] = 550.0  # Up 10% over 5 days
    mock_yf.return_value = hist

    entries = [
        {
            "date": "2026-02-01",
            "ticker": "NVDA",
            "verdict": "BUY",
            "reason": "Test",
            "price_at_verdict": 500.0,
            "spy_price_at_verdict": 690.0,
        }
    ]
    analysis = backtest_verdicts(entries, trade_size=10000)
    
    assert analysis["total_verdicts_tested"] == 1
    assert analysis["by_verdict"]["BUY"]["count"] == 1
    assert analysis["by_verdict"]["BUY"]["wins"] == 1
    assert analysis["summary"]["win_rate"] == 1.0


@patch("modules.backtester.yahoo_finance_price_history")
def test_backtest_verdicts_single_buy_loss(mock_yf):
    """Backtest single BUY verdict that loses."""
    hist = make_mock_history(100, 500.0, "2026-02-01")
    # Manually set a negative return from 2026-02-01
    hist.loc[hist.index[5], "Close"] = 450.0  # Down 10% over 5 days
    mock_yf.return_value = hist

    entries = [
        {
            "date": "2026-02-01",
            "ticker": "NVDA",
            "verdict": "BUY",
            "reason": "Test",
            "price_at_verdict": 500.0,
            "spy_price_at_verdict": 690.0,
        }
    ]
    analysis = backtest_verdicts(entries, trade_size=10000)
    
    assert analysis["total_verdicts_tested"] == 1
    assert analysis["by_verdict"]["BUY"]["count"] == 1
    assert analysis["by_verdict"]["BUY"]["wins"] == 0
    assert analysis["summary"]["win_rate"] == 0.0
    assert analysis["summary"]["total_pnl"] < 0


@patch("modules.backtester.yahoo_finance_price_history")
def test_backtest_verdicts_sell(mock_yf):
    """Backtest SELL verdict."""
    hist = make_mock_history(100, 500.0, "2026-02-01")
    hist.loc[hist.index[5], "Close"] = 450.0  # Down 10% (good for SELL)
    mock_yf.return_value = hist

    entries = [
        {
            "date": "2026-02-01",
            "ticker": "NVDA",
            "verdict": "SELL",
            "reason": "Test",
            "price_at_verdict": 500.0,
            "spy_price_at_verdict": 690.0,
        }
    ]
    analysis = backtest_verdicts(entries, trade_size=10000)
    
    assert analysis["by_verdict"]["SELL"]["count"] == 1
    assert analysis["by_verdict"]["SELL"]["wins"] == 1


@patch("modules.backtester.yahoo_finance_price_history")
def test_backtest_verdicts_avoid(mock_yf):
    """Backtest AVOID verdict."""
    def mock_history(ticker, **kwargs):
        if ticker == "NVDA":
            hist = make_mock_history(100, 500.0, "2026-02-01")
            hist.loc[hist.index[5], "Close"] = 520.0  # +4%
            return hist
        else:  # SPY
            hist = make_mock_history(100, 690.0, "2026-02-01")
            hist.loc[hist.index[5], "Close"] = 700.0  # +1.4%
            return hist
    
    mock_yf.side_effect = mock_history

    entries = [
        {
            "date": "2026-02-01",
            "ticker": "NVDA",
            "verdict": "AVOID",
            "reason": "Test",
            "price_at_verdict": 500.0,
            "spy_price_at_verdict": 690.0,
        }
    ]
    analysis = backtest_verdicts(entries, trade_size=10000)
    
    # NVDA +4% > SPY +1.4%, so AVOID loses (stock outperforms benchmark)
    assert analysis["by_verdict"]["AVOID"]["count"] == 1
    assert analysis["by_verdict"]["AVOID"]["wins"] == 0


@patch("modules.backtester.yahoo_finance_price_history")
def test_backtest_verdicts_hold(mock_yf):
    """Backtest HOLD verdict (not scored)."""
    hist = make_mock_history(100, 500.0, "2026-02-01")
    mock_yf.return_value = hist

    entries = [
        {
            "date": "2026-02-01",
            "ticker": "NVDA",
            "verdict": "HOLD",
            "reason": "Test",
            "price_at_verdict": 500.0,
            "spy_price_at_verdict": 690.0,
        }
    ]
    analysis = backtest_verdicts(entries, trade_size=10000)
    
    # HOLD is counted but not scored
    assert analysis["by_verdict"]["HOLD"]["count"] == 1
    assert analysis["by_verdict"]["HOLD"]["wins"] == 0


@patch("modules.backtester.yahoo_finance_price_history")
def test_backtest_verdicts_equity_curve(mock_yf):
    """Test equity curve generation for BUY/SELL verdicts."""
    def mock_history(ticker, **kwargs):
        if ticker in ["NVDA", "AAPL"]:
            hist = make_mock_history(100, 500.0, "2026-02-01")
            hist.loc[hist.index[5], "Close"] = 550.0  # +10%
            return hist
        return make_mock_history(100, 690.0, "2026-02-01")
    
    mock_yf.side_effect = mock_history

    entries = [
        {
            "date": "2026-02-01",
            "ticker": "NVDA",
            "verdict": "BUY",
            "reason": "Test",
            "price_at_verdict": 500.0,
            "spy_price_at_verdict": 690.0,
        },
        {
            "date": "2026-02-05",
            "ticker": "AAPL",
            "verdict": "BUY",
            "reason": "Test",
            "price_at_verdict": 500.0,
            "spy_price_at_verdict": 690.0,
        },
    ]
    analysis = backtest_verdicts(entries, trade_size=10000)
    
    # Should have equity curve entries for BUY verdicts
    assert len(analysis["equity_curve"]) > 0
    # Cumulative P&L should be positive (both BUY winners)
    final_pnl = analysis["equity_curve"][-1]["cumulative_pnl"]
    assert final_pnl > 0


@patch("modules.backtester.yahoo_finance_price_history")
def test_backtest_verdicts_best_worst_trades(mock_yf):
    """Test best and worst trade tracking."""
    def mock_history(ticker, **kwargs):
        if ticker == "NVDA":
            # NVDA has big win
            hist = make_mock_history(100, 500.0, "2026-02-01")
            hist.loc[hist.index[5], "Close"] = 600.0  # +20%
            return hist
        elif ticker == "AAPL":
            # AAPL has big loss
            hist = make_mock_history(100, 500.0, "2026-02-05")
            hist.loc[hist.index[5], "Close"] = 400.0  # -20%
            return hist
        return make_mock_history(100, 690.0, "2026-02-01")
    
    mock_yf.side_effect = mock_history

    entries = [
        {
            "date": "2026-02-01",
            "ticker": "NVDA",
            "verdict": "BUY",
            "reason": "Test",
            "price_at_verdict": 500.0,
            "spy_price_at_verdict": 690.0,
        },
        {
            "date": "2026-02-05",
            "ticker": "AAPL",
            "verdict": "BUY",
            "reason": "Test",
            "price_at_verdict": 500.0,
            "spy_price_at_verdict": 690.0,
        },
    ]
    analysis = backtest_verdicts(entries, trade_size=10000)
    
    assert analysis["summary"]["best_trade"] is not None
    assert analysis["summary"]["worst_trade"] is not None
    assert analysis["summary"]["best_trade"]["return_5d"] >= analysis["summary"]["worst_trade"]["return_5d"]


@patch("modules.backtester.yahoo_finance_price_history")
def test_backtest_verdicts_missing_data(mock_yf):
    """Test handling of missing price data."""
    mock_yf.return_value = pd.DataFrame()  # Empty history

    entries = [
        {
            "date": "2026-02-01",
            "ticker": "NVDA",
            "verdict": "BUY",
            "reason": "Test",
            "price_at_verdict": 500.0,
            "spy_price_at_verdict": 690.0,
        }
    ]
    analysis = backtest_verdicts(entries)
    
    # Should handle gracefully, skipping bad entries
    assert analysis["total_verdicts_tested"] == 0


@patch("modules.backtester.yahoo_finance_price_history")
def test_backtest_verdicts_incomplete_entry(mock_yf):
    """Test handling of incomplete verdict entry."""
    entries = [
        {
            "date": "2026-02-01",
            "ticker": "NVDA",
            # Missing verdict, price_at_verdict
            "reason": "Test",
        }
    ]
    analysis = backtest_verdicts(entries)
    
    # Should skip incomplete entry
    assert analysis["total_verdicts_tested"] == 0


@patch("modules.backtester.yahoo_finance_price_history")
@patch("modules.backtester.save_envelope")
def test_main_no_verdicts(mock_save, mock_yf, tmp_path):
    """Test main() with no verdict entries."""
    verdict_file = tmp_path / "verdicts.json"
    verdict_file.write_text(json.dumps({"entries": []}))
    
    with patch("modules.backtester.VERDICT_LOG_PATH", verdict_file):
        main()
    
    # Should have called save_envelope with error status
    mock_save.assert_called_once()
    call_args = mock_save.call_args
    envelope = call_args[0][0]
    assert envelope["status"] == "error"


@patch("modules.backtester.yahoo_finance_price_history")
@patch("modules.backtester.save_envelope")
def test_main_with_verdicts(mock_save, mock_yf, tmp_path):
    """Test main() with verdict entries."""
    hist = make_mock_history(100, 500.0, "2026-02-01")
    hist.loc[hist.index[5], "Close"] = 550.0
    mock_yf.return_value = hist

    verdict_file = tmp_path / "verdicts.json"
    verdict_file.write_text(json.dumps({
        "entries": [
            {
                "date": "2026-02-01",
                "ticker": "NVDA",
                "verdict": "BUY",
                "reason": "Test",
                "price_at_verdict": 500.0,
                "spy_price_at_verdict": 690.0,
            }
        ]
    }))
    
    with patch("modules.backtester.VERDICT_LOG_PATH", verdict_file):
        main()
    
    # Should have called save_envelope with success status
    mock_save.assert_called_once()
    call_args = mock_save.call_args
    envelope = call_args[0][0]
    assert envelope["status"] == "success"
    assert envelope["data"]["total_verdicts_tested"] == 1
