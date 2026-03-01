"""Tests for news_sentiment module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch, MagicMock

from modules.news_sentiment import (
    keyword_sentiment, ai_sentiment, analyze_sentiment,
    analyze_all_sentiment, main, fetch_news,
)


MOCK_ARTICLES = [
    {"title": "NVDA beats earnings expectations with strong AI revenue growth",
     "summary": "Record quarterly revenue driven by data center demand surge"},
    {"title": "AI spending concerns weigh on chip stocks",
     "summary": "Analysts warn of potential decline in GPU demand"},
    {"title": "NVIDIA announces new GPU architecture",
     "summary": "Next-gen chips boost performance, positive momentum continues"},
]

MOCK_BEARISH_ARTICLES = [
    {"title": "Tech sell-off crashes major stocks",
     "summary": "Market decline accelerates amid weak earnings and layoff concerns"},
    {"title": "Analyst downgrades stock with sell warning",
     "summary": "Investigation reveals risks that could underperform expectations"},
]


# ── keyword_sentiment ──

def test_keyword_sentiment_mixed():
    result = keyword_sentiment(MOCK_ARTICLES)
    assert result is not None
    assert -1.0 <= result["sentiment_score"] <= 1.0
    assert result["article_count"] == 3
    assert result["method"] == "keyword"
    assert result["key_headline"] != ""


def test_keyword_sentiment_bearish():
    result = keyword_sentiment(MOCK_BEARISH_ARTICLES)
    assert result is not None
    assert result["sentiment_score"] < 0


def test_keyword_sentiment_empty():
    assert keyword_sentiment([]) is None


def test_keyword_sentiment_no_keywords():
    articles = [{"title": "Company holds annual meeting", "summary": "Standard agenda discussed"}]
    result = keyword_sentiment(articles)
    assert result is not None
    assert result["sentiment_score"] == 0.0


# ── ai_sentiment ──

@patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""})
def test_ai_sentiment_no_key():
    """No API key → returns None."""
    result = ai_sentiment("NVDA", MOCK_ARTICLES)
    assert result is None


@patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
def test_ai_sentiment_success():
    """Successful AI call returns parsed JSON."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps({
        "sentiment_score": 0.6,
        "article_count": 3,
        "key_headline": "NVDA beats earnings",
        "summary": "Positive sentiment overall."
    }))]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    mock_anthropic = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client

    import sys
    with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
        result = ai_sentiment("NVDA", MOCK_ARTICLES)
    assert result is not None
    assert result["sentiment_score"] == 0.6
    assert result["method"] == "claude"


@patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
def test_ai_sentiment_clamped():
    """Sentiment score should be clamped to [-1, 1]."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps({
        "sentiment_score": 5.0,
        "article_count": 3,
        "key_headline": "test",
        "summary": "test"
    }))]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    mock_anthropic = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client

    import sys
    with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
        result = ai_sentiment("NVDA", MOCK_ARTICLES)
    assert result["sentiment_score"] == 1.0


# ── analyze_sentiment ──

def test_analyze_sentiment_with_articles():
    """Should return result when articles provided."""
    result = analyze_sentiment("NVDA", articles=MOCK_ARTICLES)
    assert result is not None
    assert result["ticker"] == "NVDA"
    assert -1.0 <= result["sentiment_score"] <= 1.0


def test_analyze_sentiment_no_articles():
    """Should return None when no articles."""
    result = analyze_sentiment("NVDA", articles=[])
    assert result is None


# ── analyze_all_sentiment ──

@patch("modules.news_sentiment.fetch_news")
def test_analyze_all_sentiment(mock_fetch):
    mock_fetch.return_value = MOCK_ARTICLES
    data = analyze_all_sentiment(tickers=["NVDA", "AAPL"])
    assert len(data["results"]) == 2
    for r in data["results"]:
        assert r["ticker"] in ("NVDA", "AAPL")
        assert -1.0 <= r["sentiment_score"] <= 1.0


@patch("modules.news_sentiment.fetch_news")
def test_analyze_all_no_news(mock_fetch):
    """No news for any ticker → empty results."""
    mock_fetch.return_value = []
    data = analyze_all_sentiment(tickers=["NVDA"])
    assert len(data["results"]) == 0


# ── main ──

@patch("modules.news_sentiment.fetch_news")
@patch("modules.news_sentiment.get_all_tickers")
def test_main_creates_output(mock_tickers, mock_fetch, tmp_path):
    mock_tickers.return_value = ["NVDA"]
    mock_fetch.return_value = MOCK_ARTICLES

    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)

    with patch("modules.news_sentiment.PROJECT_ROOT", tmp_path), \
         patch("lib.data_envelope.PROCESSED_DIR", processed):
        main()

    output_file = processed / "news_sentiment.json"
    assert output_file.exists()
    envelope = json.loads(output_file.read_text())
    assert envelope["module"] == "news_sentiment"
    assert envelope["status"] in ("success", "partial")
    assert len(envelope["data"]["results"]) == 1
