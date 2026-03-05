"""Tests for social_sentiment module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch, MagicMock
from modules.social_sentiment import (
    score_sentiment,
    analyze_ticker_sentiment,
    analyze_all_sentiment,
    get_all_tickers,
    BULLISH_KEYWORDS,
    BEARISH_KEYWORDS,
)


class TestScoreSentiment:
    """Test keyword-based sentiment scoring."""
    
    def test_bullish_text(self):
        """Test that bullish keywords trigger bullish sentiment."""
        text = "Stock surges on strong earnings beat and bullish guidance"
        assert score_sentiment(text) == "bullish"
    
    def test_bearish_text(self):
        """Test that bearish keywords trigger bearish sentiment."""
        text = "Stock crashes after earnings miss and downgrade warning"
        assert score_sentiment(text) == "bearish"
    
    def test_neutral_text(self):
        """Test neutral sentiment with hold/stable keywords."""
        text = "Stock holds steady in mixed trading"
        assert score_sentiment(text) == "neutral"
    
    def test_no_keywords(self):
        """Test text with no sentiment keywords defaults to neutral."""
        text = "Stock trading unchanged in quiet market"
        assert score_sentiment(text) == "neutral"
    
    def test_case_insensitive(self):
        """Test that sentiment scoring is case-insensitive."""
        text = "BULLISH SURGE AND UPGRADE"
        assert score_sentiment(text) == "bullish"
    
    def test_mixed_sentiment_dominant_bullish(self):
        """Test mixed keywords with dominant bullish sentiment."""
        text = "Stock surges and rallies with strong upgrade"  # 3 bullish
        assert score_sentiment(text) == "bullish"
    
    def test_mixed_sentiment_dominant_bearish(self):
        """Test mixed keywords with dominant bearish sentiment."""
        text = "Stock crashes and dumps with warning concern"  # 3 bearish
        assert score_sentiment(text) == "bearish"


class TestAnalyzeTickerSentiment:
    """Test single-ticker sentiment analysis."""
    
    def test_empty_articles(self):
        """Test handling of empty article list."""
        result = analyze_ticker_sentiment("AAPL", [])
        assert result["ticker"] == "AAPL"
        assert result["buzz_score"] == 0
        assert result["sentiment_score"] == 0.0
        assert result["trending"] is False
        assert result["retail_interest"] == "low"
        assert result["top_headlines"] == []
    
    def test_single_bullish_article(self):
        """Test single bullish article analysis."""
        articles = [
            {"title": "NVDA surges on strong AI demand", "summary": "Beat expectations"}
        ]
        result = analyze_ticker_sentiment("NVDA", articles)
        assert result["buzz_score"] == 1
        assert result["bullish_count"] == 1
        assert result["bearish_count"] == 0
        assert result["sentiment_score"] == 1.0
        assert result["trending"] is False
        assert result["retail_interest"] == "low"
        assert len(result["top_headlines"]) == 1
        assert result["top_headlines"][0]["sentiment"] == "bullish"
    
    def test_buzz_score_calculation(self):
        """Test that buzz_score equals article count."""
        articles = [
            {"title": "Article 1", "summary": "Summary 1"},
            {"title": "Article 2", "summary": "Summary 2"},
            {"title": "Article 3", "summary": "Summary 3"},
        ]
        result = analyze_ticker_sentiment("TEST", articles)
        assert result["buzz_score"] == 3
    
    def test_trending_detection_high(self):
        """Test trending=True when buzz_score >= 5."""
        articles = [
            {"title": f"Article {i}", "summary": f"Summary {i}"}
            for i in range(5)
        ]
        result = analyze_ticker_sentiment("TEST", articles)
        assert result["buzz_score"] == 5
        assert result["trending"] is True
    
    def test_trending_detection_low(self):
        """Test trending=False when buzz_score < 5."""
        articles = [
            {"title": f"Article {i}", "summary": f"Summary {i}"}
            for i in range(4)
        ]
        result = analyze_ticker_sentiment("TEST", articles)
        assert result["buzz_score"] == 4
        assert result["trending"] is False
    
    def test_retail_interest_high(self):
        """Test retail_interest='high' when buzz_score >= 8."""
        articles = [
            {"title": f"Article {i}", "summary": f"Summary {i}"}
            for i in range(8)
        ]
        result = analyze_ticker_sentiment("TEST", articles)
        assert result["retail_interest"] == "high"
    
    def test_retail_interest_moderate(self):
        """Test retail_interest='moderate' when 4 <= buzz_score < 8."""
        articles = [
            {"title": f"Article {i}", "summary": f"Summary {i}"}
            for i in range(4)
        ]
        result = analyze_ticker_sentiment("TEST", articles)
        assert result["retail_interest"] == "moderate"
    
    def test_retail_interest_low(self):
        """Test retail_interest='low' when buzz_score < 4."""
        articles = [
            {"title": "Article 1", "summary": "Summary 1"},
        ]
        result = analyze_ticker_sentiment("TEST", articles)
        assert result["retail_interest"] == "low"
    
    def test_sentiment_score_calculation(self):
        """Test sentiment_score = (bullish - bearish) / total."""
        # 3 bullish, 1 bearish, 0 neutral => (3-1)/4 = 0.5
        articles = [
            {"title": "Bullish surge", "summary": ""},
            {"title": "Bullish rally", "summary": ""},
            {"title": "Bullish momentum", "summary": ""},
            {"title": "Bearish crash", "summary": ""},
        ]
        result = analyze_ticker_sentiment("TEST", articles)
        assert result["sentiment_score"] == 0.5
    
    def test_sentiment_score_all_bearish(self):
        """Test sentiment_score = -1.0 when all bearish."""
        articles = [
            {"title": "Stock crashes hard", "summary": "Downgrade"},
            {"title": "Earnings miss", "summary": ""},
        ]
        result = analyze_ticker_sentiment("TEST", articles)
        assert result["sentiment_score"] == -1.0
    
    def test_top_headlines_limited_to_three(self):
        """Test that top_headlines includes up to 3 articles."""
        articles = [
            {"title": f"Article {i}", "summary": f"Summary {i}"}
            for i in range(10)
        ]
        result = analyze_ticker_sentiment("TEST", articles)
        assert len(result["top_headlines"]) == 3
    
    def test_sentiment_trend_improving(self):
        """Test sentiment_trend='improving' when newest 3 > oldest 3.
        
        Articles are in reverse chronological order (newest first).
        """
        articles = [
            # Newest 3: bullish
            {"title": "Bullish surge", "summary": ""},
            {"title": "Bullish rally", "summary": ""},
            {"title": "Bullish upgrade", "summary": ""},
            # Middle
            {"title": "Neutral article", "summary": ""},
            # Oldest 3: mixed/bearish
            {"title": "Bearish concern", "summary": ""},
            {"title": "Bearish risk", "summary": ""},
            {"title": "Mixed hold", "summary": ""},
        ]
        result = analyze_ticker_sentiment("TEST", articles)
        assert result["sentiment_trend"] == "improving"
    
    def test_sentiment_trend_declining(self):
        """Test sentiment_trend='declining' when newest 3 < oldest 3.
        
        Articles are in reverse chronological order (newest first).
        """
        articles = [
            # Newest 3: bearish/mixed
            {"title": "Bearish crash", "summary": ""},
            {"title": "Bearish warning", "summary": ""},
            {"title": "Mixed hold", "summary": ""},
            # Middle
            {"title": "Neutral article", "summary": ""},
            # Oldest 3: bullish
            {"title": "Bullish surge", "summary": ""},
            {"title": "Bullish rally", "summary": ""},
            {"title": "Bullish upgrade", "summary": ""},
        ]
        result = analyze_ticker_sentiment("TEST", articles)
        assert result["sentiment_trend"] == "declining"
    
    def test_sentiment_trend_stable_insufficient_data(self):
        """Test sentiment_trend='stable' when < 6 articles."""
        articles = [
            {"title": "Article 1", "summary": ""},
            {"title": "Article 2", "summary": ""},
            {"title": "Article 3", "summary": ""},
        ]
        result = analyze_ticker_sentiment("TEST", articles)
        assert result["sentiment_trend"] == "stable"


class TestAnalyzeAllSentiment:
    """Test market-wide sentiment aggregation."""
    
    @patch('modules.social_sentiment.fetch_news')
    @patch('modules.social_sentiment.get_all_tickers')
    def test_empty_tickers(self, mock_get_tickers, mock_fetch):
        """Test with no tickers."""
        mock_get_tickers.return_value = []
        result = analyze_all_sentiment([])
        assert result["results"] == []
        assert result["market_summary"]["overall_mood"] == 0.0
        assert result["market_summary"]["most_discussed"] is None
    
    @patch('modules.social_sentiment.fetch_news')
    def test_market_summary_creation(self, mock_fetch):
        """Test market summary aggregation."""
        # Setup: 2 tickers, one bullish, one bearish
        mock_fetch.side_effect = [
            # NVDA: bullish
            [
                {"title": "NVDA surges", "summary": "Beat earnings"},
                {"title": "NVDA rally", "summary": "Strong guidance"},
            ],
            # AMD: bearish
            [
                {"title": "AMD crashes", "summary": "Missed earnings"},
                {"title": "AMD downgrade", "summary": "Analyst warning"},
            ],
        ]
        
        result = analyze_all_sentiment(["NVDA", "AMD"])
        summary = result["market_summary"]
        
        assert "overall_mood" in summary
        assert "mood_label" in summary
        assert "most_discussed" in summary
        assert "most_bullish" in summary
        assert "most_bearish" in summary
        assert "total_articles_analyzed" in summary
        assert summary["total_articles_analyzed"] == 4
    
    @patch('modules.social_sentiment.fetch_news')
    def test_mood_label_bullish(self, mock_fetch):
        """Test mood_label='bullish' when overall_mood > 0.3."""
        mock_fetch.return_value = [
            {"title": "Bullish surge", "summary": ""},
            {"title": "Bullish rally", "summary": ""},
        ]
        
        result = analyze_all_sentiment(["TEST"])
        assert result["market_summary"]["mood_label"] == "bullish"
    
    @patch('modules.social_sentiment.fetch_news')
    def test_mood_label_bearish(self, mock_fetch):
        """Test mood_label='bearish' when overall_mood < -0.3."""
        mock_fetch.return_value = [
            {"title": "Bearish crash", "summary": ""},
            {"title": "Bearish downgrade", "summary": ""},
        ]
        
        result = analyze_all_sentiment(["TEST"])
        assert result["market_summary"]["mood_label"] == "bearish"
    
    @patch('modules.social_sentiment.fetch_news')
    def test_mood_label_neutral(self, mock_fetch):
        """Test mood_label='neutral' when overall_mood near 0."""
        mock_fetch.return_value = [
            {"title": "Bullish surge", "summary": ""},
            {"title": "Bearish crash", "summary": ""},
        ]
        
        result = analyze_all_sentiment(["TEST"])
        # Equal bullish and bearish => sentiment_score = 0
        assert result["market_summary"]["mood_label"] == "neutral"
    
    @patch('modules.social_sentiment.fetch_news')
    def test_most_discussed_selection(self, mock_fetch):
        """Test that most_discussed is ticker with highest buzz."""
        mock_fetch.side_effect = [
            [{"title": f"A{i}", "summary": ""} for i in range(3)],  # 3 articles
            [{"title": f"B{i}", "summary": ""} for i in range(8)],  # 8 articles
            [{"title": f"C{i}", "summary": ""} for i in range(1)],  # 1 article
        ]
        
        result = analyze_all_sentiment(["TICKER_A", "TICKER_B", "TICKER_C"])
        assert result["market_summary"]["most_discussed"] == "TICKER_B"
    
    @patch('modules.social_sentiment.fetch_news')
    def test_most_bullish_selection(self, mock_fetch):
        """Test that most_bullish is ticker with most bullish mentions."""
        mock_fetch.side_effect = [
            # NVDA: 2 bullish, 0 bearish
            [
                {"title": "NVDA surges", "summary": ""},
                {"title": "NVDA rally", "summary": ""},
            ],
            # AMD: 3 bullish, 0 bearish
            [
                {"title": "AMD upgrades", "summary": ""},
                {"title": "AMD bullish momentum", "summary": ""},
                {"title": "AMD buy calls", "summary": ""},
            ],
        ]
        
        result = analyze_all_sentiment(["NVDA", "AMD"])
        assert result["market_summary"]["most_bullish"] == "AMD"
    
    @patch('modules.social_sentiment.fetch_news')
    def test_most_bearish_selection(self, mock_fetch):
        """Test that most_bearish is ticker with most bearish mentions."""
        mock_fetch.side_effect = [
            # NVDA: 1 bearish
            [{"title": "NVDA warning", "summary": ""}],
            # AMD: 2 bearish
            [
                {"title": "AMD crash", "summary": ""},
                {"title": "AMD downgrade", "summary": ""},
            ],
        ]
        
        result = analyze_all_sentiment(["NVDA", "AMD"])
        assert result["market_summary"]["most_bearish"] == "AMD"


class TestEmptyNewsHandling:
    """Test behavior when fetch_news returns empty list."""
    
    @patch('modules.social_sentiment.fetch_news')
    def test_all_tickers_no_news(self, mock_fetch):
        """Test graceful handling when no ticker has news."""
        mock_fetch.return_value = []
        
        result = analyze_all_sentiment(["NVDA", "AMD"])
        
        assert len(result["results"]) == 2
        for r in result["results"]:
            assert r["buzz_score"] == 0
            assert r["sentiment_score"] == 0.0
            assert r["trending"] is False
        
        assert result["market_summary"]["overall_mood"] == 0.0
    
    @patch('modules.social_sentiment.fetch_news')
    def test_partial_news_availability(self, mock_fetch):
        """Test mixed: some tickers have news, some don't."""
        mock_fetch.side_effect = [
            [{"title": "NVDA surges", "summary": ""}],  # Has news
            [],  # No news
            [{"title": "MSFT beats", "summary": ""}],  # Has news
        ]
        
        result = analyze_all_sentiment(["NVDA", "AMD", "MSFT"])
        
        assert result["results"][0]["buzz_score"] == 1  # NVDA
        assert result["results"][1]["buzz_score"] == 0  # AMD
        assert result["results"][2]["buzz_score"] == 1  # MSFT
        
        # Market summary should only use tickers with news
        assert result["market_summary"]["total_articles_analyzed"] == 2


class TestIntegrationWithConfig:
    """Test integration with config files."""
    
    def test_get_all_tickers_from_config(self):
        """Test that get_all_tickers reads from config files."""
        tickers = get_all_tickers()
        assert isinstance(tickers, list)
        assert len(tickers) > 0
        # Should include watchlist and portfolio tickers
        assert "AAPL" in tickers or "NVDA" in tickers  # From either watchlist or portfolio
