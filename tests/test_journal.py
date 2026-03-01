"""Tests for journal module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch

from modules.journal import load_trades, analyze_trades, main
from lib.data_envelope import PROCESSED_DIR


def test_load_trades_from_sample(sample_dir):
    """Loads trades from sample CSV."""
    with patch("modules.journal.PROJECT_ROOT", sample_dir.parent.parent):
        # Just test with actual sample data
        trades = load_trades()
    assert len(trades) == 30


def test_analyze_trades_win_rate(sample_dir):
    trades = load_trades()
    result = analyze_trades(trades)
    assert result["total_trades"] == 30
    assert 0.5 <= result["win_rate"] <= 0.7  # ~18/30 = 0.6
    # New profit metrics
    assert "total_pnl" in result
    assert "profit_factor" in result
    assert "largest_winner" in result
    assert "largest_loser" in result
    assert "max_consecutive_losses" in result
    assert result["largest_winner"] > 0
    assert result["largest_loser"] < 0


def test_analyze_trades_tuesday_flag(sample_dir):
    trades = load_trades()
    result = analyze_trades(trades)
    day_flags = [p for p in result["patterns"] if p["type"] == "day_of_week"]
    tuesday_flags = [d for d in day_flags if d.get("day") == "Tuesday"]
    assert len(tuesday_flags) >= 1, "Should flag Tuesday as poor performance day"
    assert tuesday_flags[0]["win_rate"] < 0.30


def test_analyze_trades_revenge_detection(sample_dir):
    trades = load_trades()
    result = analyze_trades(trades)
    revenge = [p for p in result["patterns"] if p["type"] == "revenge_trading"]
    tsla_revenge = [r for r in revenge if r["ticker"] == "TSLA"]
    assert len(tsla_revenge) >= 1, "Should detect TSLA revenge trading pattern"
    assert tsla_revenge[0]["count"] >= 3


def test_checklist_generated(sample_dir):
    trades = load_trades()
    result = analyze_trades(trades)
    assert len(result["checklist"]) >= 2, "Should generate at least 2 checklist rules"


def test_main_creates_output(tmp_path):
    """Test that main() produces valid envelope JSON."""
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)

    with patch("modules.journal.PROJECT_ROOT", tmp_path), \
         patch("lib.data_envelope.PROCESSED_DIR", processed):
        # Copy sample data to tmp
        sample_src = PROJECT_ROOT / "data" / "sample" / "trades.csv"
        sample_dst = tmp_path / "data" / "sample"
        sample_dst.mkdir(parents=True)
        import shutil
        shutil.copy(sample_src, sample_dst / "trades.csv")

        main()

    output_file = processed / "journal.json"
    assert output_file.exists(), "journal.json should be created"
    envelope = json.loads(output_file.read_text())
    assert envelope["module"] == "journal"
    assert envelope["status"] in ("success", "partial")
    assert "data" in envelope
    assert envelope["data"]["total_trades"] == 30
