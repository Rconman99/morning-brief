"""Tests for scorecard module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
import pandas as pd
from unittest.mock import patch

from modules.scorecard import (
    load_verdict_log, get_price_on_date, evaluate_verdicts, main,
    EVALUATION_WINDOWS,
)


def _make_history(dates_prices: list[tuple[str, float]]) -> pd.DataFrame:
    """Build a minimal DataFrame with Close prices indexed by date."""
    dates = [pd.Timestamp(d) for d, _ in dates_prices]
    closes = [p for _, p in dates_prices]
    df = pd.DataFrame({"Close": closes}, index=dates)
    df.index.name = "Date"
    return df


# ── load_verdict_log ──

def test_load_verdict_log_empty(tmp_path):
    """No file → empty list."""
    assert load_verdict_log(tmp_path) == []


def test_load_verdict_log_with_data(tmp_path):
    """Valid file → list of entries."""
    log = {"entries": [
        {"date": "2026-01-15", "ticker": "NVDA", "verdict": "BUY",
         "reason": "Cheaper on 3 metrics", "price_at_verdict": 900.0,
         "spy_price_at_verdict": 520.0},
    ]}
    (tmp_path / "verdict_log.json").write_text(json.dumps(log))
    entries = load_verdict_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]["ticker"] == "NVDA"


def test_load_verdict_log_corrupt(tmp_path):
    """Corrupt JSON → empty list."""
    (tmp_path / "verdict_log.json").write_text("not json")
    assert load_verdict_log(tmp_path) == []


# ── get_price_on_date ──

def test_get_price_on_date_exact():
    """Exact date match returns that day's close."""
    df = _make_history([("2026-01-20", 100.0), ("2026-01-21", 102.0)])
    assert get_price_on_date(df, "2026-01-20") == 100.0


def test_get_price_on_date_weekend_fallforward():
    """Saturday falls forward to Monday."""
    df = _make_history([("2026-01-19", 98.0), ("2026-01-21", 101.0)])
    # Jan 20 is a Tuesday in 2026 — but our data skips it; next available is Jan 21
    assert get_price_on_date(df, "2026-01-20") == 101.0


def test_get_price_on_date_out_of_range():
    """Date after all data returns None."""
    df = _make_history([("2026-01-15", 100.0)])
    assert get_price_on_date(df, "2026-06-01") is None


def test_get_price_on_date_empty():
    """Empty DataFrame returns None."""
    assert get_price_on_date(pd.DataFrame(), "2026-01-15") is None


# ── evaluate_verdicts ──

def _mock_histories(tickers_prices: dict):
    """Return a patched yahoo_finance_price_history that serves canned data."""
    def _fetch(ticker, period="3mo"):
        return tickers_prices.get(ticker, pd.DataFrame())
    return _fetch


def test_evaluate_buy_win():
    """BUY wins when ticker outperforms SPY."""
    entries = [{
        "date": "2026-01-10", "ticker": "NVDA", "verdict": "BUY",
        "reason": "test", "price_at_verdict": 100.0, "spy_price_at_verdict": 500.0,
    }]
    # After windows: NVDA outperforms SPY → BUY wins
    histories = {
        "NVDA": _make_history([("2026-01-10", 100.0), ("2026-01-11", 103.0),
                               ("2026-01-13", 106.0), ("2026-01-15", 110.0),
                               ("2026-01-20", 112.0)]),
        "SPY": _make_history([("2026-01-10", 500.0), ("2026-01-11", 501.0),
                              ("2026-01-13", 502.0), ("2026-01-15", 510.0),
                              ("2026-01-20", 512.0)]),
    }
    with patch("modules.scorecard.yahoo_finance_price_history", side_effect=_mock_histories(histories)):
        result = evaluate_verdicts(entries, "2026-02-26")

    assert result["evaluated_verdicts"] == 1
    detail = result["details"][0]
    assert detail["windows"][5]["status"] == "win"
    assert result["summary"]["total_wins"] >= 1


def test_evaluate_sell_win():
    """SELL wins when ticker underperforms SPY."""
    entries = [{
        "date": "2026-01-10", "ticker": "TSLA", "verdict": "SELL",
        "reason": "test", "price_at_verdict": 200.0, "spy_price_at_verdict": 500.0,
    }]
    # After windows: TSLA drops, SPY up → SELL was correct
    histories = {
        "TSLA": _make_history([("2026-01-10", 200.0), ("2026-01-11", 197.0),
                                ("2026-01-13", 194.0), ("2026-01-15", 190.0),
                                ("2026-01-20", 188.0)]),
        "SPY": _make_history([("2026-01-10", 500.0), ("2026-01-11", 501.0),
                              ("2026-01-13", 503.0), ("2026-01-15", 510.0),
                              ("2026-01-20", 512.0)]),
    }
    with patch("modules.scorecard.yahoo_finance_price_history", side_effect=_mock_histories(histories)):
        result = evaluate_verdicts(entries, "2026-02-26")

    detail = result["details"][0]
    assert detail["windows"][5]["status"] == "win"


def test_evaluate_review_unscored():
    """REVIEW verdicts are tracked but not scored."""
    entries = [{
        "date": "2026-01-10", "ticker": "AMD", "verdict": "REVIEW",
        "reason": "test", "price_at_verdict": 150.0, "spy_price_at_verdict": 500.0,
    }]
    histories = {
        "AMD": _make_history([("2026-01-10", 150.0), ("2026-01-11", 151.0),
                              ("2026-01-13", 153.0), ("2026-01-15", 155.0),
                              ("2026-01-20", 158.0)]),
        "SPY": _make_history([("2026-01-10", 500.0), ("2026-01-11", 500.5),
                              ("2026-01-13", 502.0), ("2026-01-15", 510.0),
                              ("2026-01-20", 512.0)]),
    }
    with patch("modules.scorecard.yahoo_finance_price_history", side_effect=_mock_histories(histories)):
        result = evaluate_verdicts(entries, "2026-02-26")

    detail = result["details"][0]
    assert detail["windows"][5]["status"] == "unscored"
    assert result["summary"]["total_scored"] == 0
    assert "REVIEW" in result["summary"]["by_verdict"]


def test_pending_window():
    """Window not yet elapsed → status 'pending'."""
    entries = [{
        "date": "2026-02-24", "ticker": "AAPL", "verdict": "HOLD",
        "reason": "test", "price_at_verdict": 195.0, "spy_price_at_verdict": 520.0,
    }]
    histories = {
        "AAPL": _make_history([("2026-02-24", 195.0), ("2026-02-25", 195.5), ("2026-02-26", 196.0)]),
        "SPY": _make_history([("2026-02-24", 520.0), ("2026-02-25", 520.3), ("2026-02-26", 521.0)]),
    }
    with patch("modules.scorecard.yahoo_finance_price_history", side_effect=_mock_histories(histories)):
        result = evaluate_verdicts(entries, "2026-02-26")

    detail = result["details"][0]
    # 1 day from Feb 24 = Feb 25, which is before "today" Feb 26
    # 3 days from Feb 24 = Feb 27, which is after "today" Feb 26
    assert detail["windows"][1]["status"] in ("win", "loss", "no_data")
    assert detail["windows"][3]["status"] == "pending"
    assert detail["windows"][5]["status"] == "pending"
    assert detail["windows"][10]["status"] == "pending"


def test_skips_today():
    """Entries dated today are excluded from evaluation."""
    entries = [
        {"date": "2026-02-26", "ticker": "NVDA", "verdict": "BUY",
         "reason": "test", "price_at_verdict": 900.0, "spy_price_at_verdict": 520.0},
        {"date": "2026-01-10", "ticker": "AAPL", "verdict": "HOLD",
         "reason": "test", "price_at_verdict": 195.0, "spy_price_at_verdict": 500.0},
    ]
    histories = {
        "AAPL": _make_history([("2026-01-10", 195.0), ("2026-01-11", 195.5),
                               ("2026-01-13", 196.0), ("2026-01-15", 197.0),
                               ("2026-01-20", 198.0)]),
        "SPY": _make_history([("2026-01-10", 500.0), ("2026-01-11", 500.5),
                              ("2026-01-13", 501.0), ("2026-01-15", 502.0),
                              ("2026-01-20", 503.0)]),
    }
    with patch("modules.scorecard.yahoo_finance_price_history", side_effect=_mock_histories(histories)):
        result = evaluate_verdicts(entries, "2026-02-26")

    # Only the AAPL entry should be evaluated
    assert result["evaluated_verdicts"] == 1
    assert result["details"][0]["ticker"] == "AAPL"


# ── main() ──

def test_main_first_run(tmp_path):
    """No verdict log → writes partial scorecard with zero results."""
    processed = tmp_path / "processed"
    processed.mkdir()

    with patch("modules.scorecard.SCORECARD_DIR", tmp_path), \
         patch("lib.data_envelope.PROCESSED_DIR", processed):
        main()

    sc = json.loads((processed / "scorecard.json").read_text())
    assert sc["status"] == "partial"
    assert sc["data"]["evaluated_verdicts"] == 0


def test_main_with_log(tmp_path):
    """With verdict log → evaluates and writes scorecard."""
    scorecard_dir = tmp_path / "scorecard"
    scorecard_dir.mkdir()
    processed = tmp_path / "processed"
    processed.mkdir()

    log = {"entries": [{
        "date": "2026-01-10", "ticker": "NVDA", "verdict": "BUY",
        "reason": "test", "price_at_verdict": 100.0, "spy_price_at_verdict": 500.0,
    }]}
    (scorecard_dir / "verdict_log.json").write_text(json.dumps(log))

    histories = {
        "NVDA": _make_history([("2026-01-10", 100.0), ("2026-01-11", 103.0),
                               ("2026-01-13", 106.0), ("2026-01-15", 110.0),
                               ("2026-01-20", 112.0)]),
        "SPY": _make_history([("2026-01-10", 500.0), ("2026-01-11", 501.0),
                              ("2026-01-13", 502.0), ("2026-01-15", 510.0),
                              ("2026-01-20", 512.0)]),
    }

    with patch("modules.scorecard.SCORECARD_DIR", scorecard_dir), \
         patch("modules.scorecard.yahoo_finance_price_history",
               side_effect=_mock_histories(histories)), \
         patch("lib.data_envelope.PROCESSED_DIR", processed):
        main()

    sc = json.loads((processed / "scorecard.json").read_text())
    assert sc["module"] == "scorecard"
    assert sc["data"]["evaluated_verdicts"] == 1
    assert sc["data"]["summary"]["total_wins"] >= 1
