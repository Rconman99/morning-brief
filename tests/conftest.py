"""Shared pytest fixtures for trading intelligence tests."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import json


@pytest.fixture
def project_root():
    return PROJECT_ROOT


@pytest.fixture
def sample_dir(project_root):
    return project_root / "data" / "sample"


@pytest.fixture
def processed_dir(tmp_path):
    """Use tmp_path for test outputs so tests don't pollute real data."""
    d = tmp_path / "processed"
    d.mkdir()
    return d


@pytest.fixture
def mock_envelopes(tmp_path):
    """Create mock processed/*.json files for morning_brief tests."""
    processed = tmp_path / "processed"
    processed.mkdir()
    modules = {
        "journal": {"win_rate": 0.62, "total_trades": 30, "patterns": [
            {"type": "day_of_week", "detail": "Tuesday win rate 0% (5 trades)", "rule": "Avoid trading on Tuesdays"},
            {"type": "revenge_trading", "detail": "TSLA: 3 consecutive losses within 48hrs", "rule": "Step away after 2 consecutive losses on same ticker"},
        ]},
        "earnings_tone": {"results": [
            {"ticker": "AAPL", "tone_score": -0.5, "confidence_score": 0.4, "hedge_count": 8, "definitive_count": 5, "risk_factors": ["semiconductor supply constraints", "regulatory costs in EU"], "summary": "Mixed tone with confident services outlook but hedged guidance on AI returns and hardware margins."},
        ]},
        "valuation": {"comparisons": [
            {"stock_a": "NVDA", "stock_b": "AMD", "metrics": {"pe": {"NVDA": 65.2, "AMD": 45.1}, "ev_ebitda": {"NVDA": 55.0, "AMD": 32.0}}, "cheaper": "AMD", "thesis": "AMD trades at a significant discount on most valuation metrics despite strong data center growth."},
            {"stock_a": "AAPL", "stock_b": "MSFT", "metrics": {"pe": {"AAPL": 28.5, "MSFT": 34.2}}, "cheaper": "AAPL", "thesis": "AAPL is modestly cheaper on P/E but both are fairly valued."},
            {"stock_a": "GOOGL", "stock_b": "META", "metrics": {"pe": {"GOOGL": 22.1, "META": 24.5}}, "cheaper": "GOOGL", "thesis": "GOOGL is slightly cheaper with comparable growth prospects."},
        ], "scenarios": [
            {"ticker": "NVDA", "current_price": 900.0, "bull_price": 1100.0, "base_price": 950.0, "bear_price": 750.0, "bull_upside": 0.2222, "base_upside": 0.0556, "bear_downside": -0.1667, "risk_reward": 1.33, "forward_pe": 35.0, "forward_eps": 27.14},
            {"ticker": "AAPL", "current_price": 195.0, "bull_price": 230.0, "base_price": 210.0, "bear_price": 170.0, "bull_upside": 0.1795, "base_upside": 0.0769, "bear_downside": -0.1282, "risk_reward": 1.40, "forward_pe": 28.0, "forward_eps": 7.50},
        ]},
        "portfolio": {"holdings": [
            {"ticker": "NVDA", "shares": 50, "cost_basis": 485.00, "current_price": 900.0, "pnl": 20750.0, "atr": 25.0, "trailing_stop": 850.0},
            {"ticker": "AAPL", "shares": 100, "cost_basis": 178.50, "current_price": 195.0, "pnl": 1650.0, "atr": 4.5, "trailing_stop": 186.0},
            {"ticker": "VOO", "shares": 25, "cost_basis": 420.00, "current_price": 480.0, "pnl": 1500.0, "atr": 6.0, "trailing_stop": 468.0},
        ], "correlations": {"NVDA_AAPL": 0.72, "NVDA_VOO": 0.85, "AAPL_VOO": 0.90}},
        "options": {"tickers": [
            {"ticker": "NVDA", "max_pain": 880, "front_month_expiry": "2026-03-20", "pmcc": {"long_strike": 765, "short_strike": 945, "net_debit": 142.0}, "top_strikes": [{"strike": 900, "total_oi": 50000}, {"strike": 850, "total_oi": 45000}, {"strike": 950, "total_oi": 40000}]},
            {"ticker": "AAPL", "max_pain": 190, "front_month_expiry": "2026-03-20", "pmcc": {"long_strike": 166, "short_strike": 205, "net_debit": 22.0}, "top_strikes": [{"strike": 195, "total_oi": 80000}, {"strike": 190, "total_oi": 75000}, {"strike": 200, "total_oi": 70000}]},
            {"ticker": "VOO", "skipped": True, "reason": "No options data available for ETF"},
        ]},
        "technical_signals": {"results": [
            {"ticker": "NVDA", "composite_score": 1.5, "rsi_14": 62.3, "macd_signal": "bullish_crossover", "macd_histogram": 2.45, "bb_position": "middle", "sma_trend": "golden_cross", "volume_ratio": 1.2, "vwap": 875.50, "vwap_signal": "above", "volume_profile": {"poc": 880.0, "poc_volume": 5000000, "high_volume_nodes": [880.0, 860.0, 900.0]}, "indicators": [
                {"name": "RSI", "value": 62.3, "signal": "neutral", "score": 0},
                {"name": "MACD", "value": 2.45, "signal": "bullish_crossover", "score": 1},
                {"name": "Bollinger Bands", "value": "middle", "signal": "middle", "score": 0},
                {"name": "SMA", "value": "golden_cross", "signal": "golden_cross", "score": 0.5},
                {"name": "Volume", "value": 1.2, "signal": "normal", "score": 0},
            ]},
            {"ticker": "AAPL", "composite_score": -0.5, "rsi_14": 45.1, "macd_signal": "neutral", "macd_histogram": -0.8, "bb_position": "middle", "sma_trend": "above_200", "volume_ratio": 0.9, "vwap": 193.20, "vwap_signal": "above", "volume_profile": {"poc": 192.0, "poc_volume": 8000000, "high_volume_nodes": [192.0, 190.0, 195.0]}, "indicators": [
                {"name": "RSI", "value": 45.1, "signal": "neutral", "score": 0},
                {"name": "MACD", "value": -0.8, "signal": "neutral", "score": 0},
                {"name": "Bollinger Bands", "value": "middle", "signal": "middle", "score": 0},
                {"name": "SMA", "value": "above_200", "signal": "above_200", "score": 0.5},
                {"name": "Volume", "value": 0.9, "signal": "normal", "score": 0},
            ]},
        ]},
        "news_sentiment": {"results": [
            {"ticker": "NVDA", "sentiment_score": 0.3, "article_count": 8, "key_headline": "NVIDIA AI demand remains strong", "summary": "Positive sentiment driven by data center growth.", "method": "keyword"},
            {"ticker": "AAPL", "sentiment_score": -0.1, "article_count": 5, "key_headline": "Apple faces regulatory headwinds in EU", "summary": "Mixed sentiment with services strength offset by regulatory concerns.", "method": "keyword"},
        ]},
        "opportunities": {
            "scan_date": "2026-03-01",
            "universe_size": 22,
            "passed_filter": 2,
            "opportunities": [
                {
                    "ticker": "AVGO", "composite_score": 3.2, "rsi_14": 58.3,
                    "macd_signal": "bullish_crossover", "sma_trend": "golden_cross",
                    "volume_ratio": 1.8, "pe_ttm": 32.5, "market_cap_b": 850.0,
                    "reason": "Strong technicals (composite +3.2), bullish MACD crossover, 1.8x volume surge, golden cross active",
                    "risk_note": "RSI approaching overbought at 58, elevated PE",
                },
                {
                    "ticker": "MRVL", "composite_score": 2.8, "rsi_14": 42.1,
                    "macd_signal": "bullish_crossover", "sma_trend": "above_200",
                    "volume_ratio": 1.5, "pe_ttm": 45.2, "market_cap_b": 65.0,
                    "reason": "Strong technicals (composite +2.8), 1.5x volume",
                    "risk_note": "Elevated PE",
                },
            ],
            "sector_read": {
                "XLK": {"change_1d": 0.8, "change_5d": 2.1, "trend": "bullish"},
                "XLF": {"change_1d": -0.3, "change_5d": -1.2, "trend": "bearish"},
            },
        },
        "risk_dashboard": {
            "now_risks": [
                {"risk": "VIX_ELEVATED", "level": "medium", "detail": "VIX at 22.5 — elevated volatility"},
                {"risk": "CORRELATED_PAIR", "level": "medium", "detail": "AAPL_VOO correlation 0.90 — moves in sync"},
            ],
            "short_risks": [
                {"risk": "OVERBOUGHT", "level": "medium", "detail": "NVDA RSI 76 — pullback likely within days"},
            ],
            "long_risks": [],
            "now_score": 2, "short_score": 1, "long_score": 0,
            "total_risk": 2.7, "regime": "LOW_RISK",
            "regime_note": "Favorable conditions — lean into high-conviction setups",
            "vix": 22.5, "ten_year_yield": 4.25,
        },
        "insider_tracker": {"results": [
            {"ticker": "NVDA", "transaction_count": 4, "signal": "active", "cluster_buy": True, "detail": "4 Form 4 filings in last 30 days", "recent_filings": []},
            {"ticker": "AAPL", "transaction_count": 1, "signal": "normal", "cluster_buy": False, "detail": "1 Form 4 filing in last 30 days", "recent_filings": []},
        ]},
        "trade_memory": {"results": [
            {"ticker": "NVDA", "fingerprint": {"rsi_bucket": "neutral", "macd_direction": "bullish_crossover", "bb_position": "middle", "sma_trend": "golden_cross", "volume_bucket": "normal"}, "match_result": {"matches": 3, "wins": 2, "losses": 1, "win_rate": 0.67, "confidence": "mixed"}, "signal": "Mixed history: 2W/1L in 3 similar setups"},
        ]},
        "position_sizer": {"portfolio_value": 100000, "risk_per_trade_pct": 0.02, "max_position_pct": 0.15, "positions": [
            {"ticker": "NVDA", "recommended_shares": 12, "recommended_value": 10800, "stop_loss": 880.00, "risk_per_share": 16.67, "portfolio_pct": 0.108, "note": "Based on 2% risk with $33.34 ATR stop"},
        ]},
        "scorecard": {
            "evaluated_verdicts": 6,
            "evaluation_windows": [1, 3, 5, 10],
            "summary": {
                "total_scored": 8, "total_wins": 5, "total_losses": 3,
                "win_rate": 0.625,
                "by_verdict": {
                    "BUY": {"scored": 3, "wins": 2, "losses": 1, "win_rate": 0.667},
                    "HOLD": {"scored": 3, "wins": 2, "losses": 1, "win_rate": 0.667},
                    "SELL": {"scored": 2, "wins": 1, "losses": 1, "win_rate": 0.5},
                    "REVIEW": {"tracked": 1, "scored": 0},
                },
            },
            "details": [],
        },
        "economic_calendar": {
            "signal": "high_impact",
            "signal_detail": "Non-Farm Payroll (NFP) 2026-03-06",
            "upcoming_events": [
                {"name": "Non-Farm Payroll", "date": "2026-03-06", "impact": "high", "description": "Monthly US employment data"},
                {"name": "CPI Release", "date": "2026-03-13", "impact": "medium", "description": "Consumer Price Index"},
            ],
            "recent_indicators": {
                "fed_funds_rate": {"value": 4.50, "date": "2026-02-01", "series": "FEDFUNDS"},
                "cpi_yoy": {"value": 2.8, "date": "2026-01-01", "series": "CPIAUCSL"},
            },
            "fomc_next": {"date": "2026-03-18", "days_until": 14},
            "data_source": "sample_data",
            "analyzed_at": "2026-03-04T08:00:00-05:00",
        },
        "macro_dashboard": {
            "signal": "mixed",
            "signal_detail": "VIX elevated at 22.5 with stable yields",
            "regime_summary": "Mixed signals: moderate volatility with stable bond market",
            "indicators": {
                "VIX": {"value": 22.50, "change_1d": 1.2, "change_5d": 3.5, "change_20d": -2.1, "high_20d": 24.0, "low_20d": 18.5, "percentile_1y": 0.65, "trend": "rising", "interpretation": "Elevated — above normal range"},
                "DXY": {"value": 104.2, "change_1d": -0.1, "change_5d": -0.5, "change_20d": 0.8, "high_20d": 105.0, "low_20d": 103.0, "percentile_1y": 0.55, "trend": "flat", "interpretation": "Stable — neutral for equities"},
                "US10Y": {"value": 4.25, "change_1d": 0.02, "change_5d": -0.05, "change_20d": 0.10, "high_20d": 4.35, "low_20d": 4.10, "percentile_1y": 0.70, "trend": "flat", "interpretation": "Elevated — tightening conditions"},
                "OIL": {"value": 72.50, "change_1d": 0.8, "change_5d": 2.1, "change_20d": -3.0, "high_20d": 78.0, "low_20d": 68.0, "percentile_1y": 0.40, "trend": "rising", "interpretation": "Below average — energy costs contained"},
                "GOLD": {"value": 2050.0, "change_1d": 0.3, "change_5d": 1.0, "change_20d": 2.5, "high_20d": 2060.0, "low_20d": 1980.0, "percentile_1y": 0.80, "trend": "rising", "interpretation": "Near highs — safe haven demand"},
            },
            "data_source": "yfinance",
            "analyzed_at": "2026-03-04T08:00:00-05:00",
        },
        "sector_rotation": {
            "signal": "growth_momentum",
            "signal_detail": "Growth and discretionary sectors gaining leadership — risk-on environment",
            "leaders_21d": [
                {"etf": "XLK", "sector": "Technology", "return_21d": 5.2, "relative_strength": 3.1, "trend": "uptrend"},
                {"etf": "XLY", "sector": "Consumer Discretionary", "return_21d": 4.8, "relative_strength": 2.7, "trend": "uptrend"},
                {"etf": "XLC", "sector": "Communication Services", "return_21d": 4.1, "relative_strength": 1.9, "trend": "uptrend"},
            ],
            "laggards_21d": [
                {"etf": "XLE", "sector": "Energy", "return_21d": -2.3, "relative_strength": -4.4, "trend": "downtrend"},
                {"etf": "XLU", "sector": "Utilities", "return_21d": 0.1, "relative_strength": -2.0, "trend": "downtrend"},
                {"etf": "XLB", "sector": "Materials", "return_21d": 0.5, "relative_strength": -1.6, "trend": "downtrend"},
            ],
            "leaders_5d": [
                {"etf": "XLK", "sector": "Technology", "return_5d": 2.1, "relative_strength": 2.3, "trend": "uptrend"},
                {"etf": "XLY", "sector": "Consumer Discretionary", "return_5d": 1.8, "relative_strength": 2.0, "trend": "uptrend"},
                {"etf": "XLV", "sector": "Health Care", "return_5d": 0.8, "relative_strength": 1.0, "trend": "uptrend"},
            ],
            "laggards_5d": [
                {"etf": "XLE", "sector": "Energy", "return_5d": -1.2, "relative_strength": -1.4, "trend": "downtrend"},
                {"etf": "XLU", "sector": "Utilities", "return_5d": -0.8, "relative_strength": -1.0, "trend": "downtrend"},
                {"etf": "XLP", "sector": "Consumer Staples", "return_5d": -0.3, "relative_strength": -0.5, "trend": "downtrend"},
            ],
            "rotation_type": "growth_rotation",
            "breadth": {"positive_sectors": 8, "negative_sectors": 3, "total_sectors": 11, "label": "broad_rally"},
            "all_sectors": {
                "XLK": {"sector": "Technology", "price": 220.50, "return_1d": 0.3, "return_5d": 2.1, "return_21d": 5.2, "return_63d": 12.8, "relative_strength_1d": 0.2, "relative_strength_5d": 2.3, "relative_strength_21d": 3.1, "relative_strength_63d": 7.5, "trend": "uptrend", "volume_ratio": 1.15},
                "XLE": {"sector": "Energy", "price": 65.20, "return_1d": -0.4, "return_5d": -1.2, "return_21d": -2.3, "return_63d": -5.1, "relative_strength_1d": -0.5, "relative_strength_5d": -1.4, "relative_strength_21d": -4.4, "relative_strength_63d": -10.8, "trend": "downtrend", "volume_ratio": 1.08},
            },
            "spy_returns": {"return_1d": 0.1, "return_5d": -0.2, "return_21d": 2.1, "return_63d": 5.3},
            "data_source": "yfinance",
            "analyzed_at": "2026-02-26T08:00:00-07:00",
        },
    }
    for name, data in modules.items():
        envelope = {
            "module": name,
            "generated_at": "2026-02-26T08:00:00-07:00",
            "status": "success",
            "error_message": None,
            "data": data,
        }
        (processed / f"{name}.json").write_text(json.dumps(envelope, indent=2))
    return processed
