"""Social sentiment analysis: yfinance news + keyword-based retail sentiment scoring."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime, timedelta
from collections import defaultdict

from lib.data_envelope import create_envelope, save_envelope

logger = logging.getLogger(__name__)


def setup_logging():
    """Call this ONLY inside main(). NEVER at module level."""
    log_dir = PROJECT_ROOT / "data" / "outputs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_dir / "run.log", mode="a"),
            ],
        )


def get_all_tickers() -> list[str]:
    """Get union of watchlist tickers and portfolio holdings, deduped."""
    tickers = set()
    watchlist_path = PROJECT_ROOT / "config" / "watchlist.json"
    portfolio_path = PROJECT_ROOT / "config" / "portfolio.json"

    if watchlist_path.exists():
        try:
            wl = json.loads(watchlist_path.read_text())
            tickers.update(wl.get("tickers", []))
        except (json.JSONDecodeError, OSError):
            pass

    if portfolio_path.exists():
        try:
            pf = json.loads(portfolio_path.read_text())
            for h in pf.get("holdings", []):
                tickers.add(h["ticker"])
        except (json.JSONDecodeError, OSError):
            pass

    return sorted(tickers)


def fetch_news(ticker: str) -> list[dict]:
    """Fetch news articles for a ticker via yfinance.
    
    Returns list of dicts with 'title', 'summary', 'publisher', 'link' fields.
    Returns empty list on failure.
    """
    try:
        import yfinance as yf
        ticker_obj = yf.Ticker(ticker)
        news = ticker_obj.news
        if not news:
            return []
        
        articles = []
        for item in news:
            # yfinance news structure varies; handle both formats
            if isinstance(item, dict):
                content = item.get("content", {}) if isinstance(item.get("content"), dict) else {}
                title = content.get("title", item.get("title", ""))
                summary = content.get("summary", item.get("summary", ""))
                publisher = content.get("publisher", item.get("publisher", ""))
                link = content.get("link", item.get("link", ""))
            else:
                # Fallback if item structure is unexpected
                continue
            
            if title:
                articles.append({
                    "title": title,
                    "summary": summary,
                    "publisher": publisher,
                    "link": link,
                })
        
        return articles
    except Exception as e:
        logger.warning("Failed to fetch news for %s: %s", ticker, e)
        return []


# Sentiment scoring keywords
BULLISH_KEYWORDS = [
    "buy", "bullish", "upgrade", "beat", "surge", "rally", "breakout",
    "moon", "squeeze", "calls", "accumulate", "outperform", "upside",
    "strong", "growth", "record", "profit", "revenue beat", "exceeds",
    "positive", "gain", "rise", "boost", "momentum", "buyback",
]

BEARISH_KEYWORDS = [
    "sell", "bearish", "downgrade", "miss", "crash", "dump", "short",
    "puts", "overvalued", "underperform", "downside", "warning", "decline",
    "weak", "loss", "drop", "fall", "cut", "layoff", "investigation",
    "lawsuit", "recall", "risk", "concern", "negative", "slump",
]

NEUTRAL_KEYWORDS = [
    "hold", "mixed", "flat", "stable", "sideways", "range", "consolidate",
    "neutral", "unchanged", "steady",
]


def score_sentiment(text: str) -> str:
    """Score a single text snippet (title or summary) for sentiment.
    
    Returns "bullish", "bearish", or "neutral".
    """
    text_lower = text.lower()
    
    bullish_count = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
    bearish_count = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)
    neutral_count = sum(1 for kw in NEUTRAL_KEYWORDS if kw in text_lower)
    
    # Determine dominant sentiment
    if bullish_count > bearish_count and bullish_count > neutral_count:
        return "bullish"
    elif bearish_count > bullish_count and bearish_count > neutral_count:
        return "bearish"
    else:
        return "neutral"


def analyze_ticker_sentiment(ticker: str, articles: list[dict]) -> dict:
    """Analyze sentiment metrics for a single ticker.
    
    Returns dict with buzz_score, sentiment_score, trending, retail_interest, etc.
    """
    if not articles:
        return {
            "ticker": ticker,
            "buzz_score": 0,
            "sentiment_score": 0.0,
            "trending": False,
            "retail_interest": "low",
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "sentiment_trend": "stable",
            "top_headlines": [],
        }
    
    # Count sentiment per article
    bullish_count = 0
    bearish_count = 0
    neutral_count = 0
    headlines_with_sentiment = []
    
    for article in articles:
        title = article.get("title", "")
        sentiment = score_sentiment(title + " " + article.get("summary", ""))
        
        if sentiment == "bullish":
            bullish_count += 1
        elif sentiment == "bearish":
            bearish_count += 1
        else:
            neutral_count += 1
        
        headlines_with_sentiment.append({
            "title": title,
            "sentiment": sentiment,
        })
    
    # buzz_score = number of articles (proxy for attention)
    buzz_score = len(articles)
    
    # sentiment_score: (bullish - bearish) / total, scaled to [-1, +1]
    total = bullish_count + bearish_count + neutral_count
    if total == 0:
        sentiment_score = 0.0
    else:
        sentiment_score = (bullish_count - bearish_count) / total
    sentiment_score = max(-1.0, min(1.0, round(sentiment_score, 2)))
    
    # trending: True if buzz_score >= 5
    trending = buzz_score >= 5
    
    # retail_interest: classification based on buzz
    if buzz_score >= 8:
        retail_interest = "high"
    elif buzz_score >= 4:
        retail_interest = "moderate"
    else:
        retail_interest = "low"
    
    # sentiment_trend: compare newest 3 vs oldest 3 articles
    # Articles are returned by yfinance in reverse chronological order (newest first)
    sentiment_trend = "stable"
    if len(headlines_with_sentiment) >= 6:
        newest_3 = headlines_with_sentiment[:3]
        oldest_3 = headlines_with_sentiment[-3:]
        
        newest_score = sum(1 if h["sentiment"] == "bullish" else -1 if h["sentiment"] == "bearish" else 0
                          for h in newest_3) / 3
        oldest_score = sum(1 if h["sentiment"] == "bullish" else -1 if h["sentiment"] == "bearish" else 0
                          for h in oldest_3) / 3
        
        if newest_score > oldest_score + 0.3:
            sentiment_trend = "improving"
        elif newest_score < oldest_score - 0.3:
            sentiment_trend = "declining"
    
    # Top 3 headlines
    top_headlines = headlines_with_sentiment[:3]
    
    return {
        "ticker": ticker,
        "buzz_score": buzz_score,
        "sentiment_score": sentiment_score,
        "trending": trending,
        "retail_interest": retail_interest,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "neutral_count": neutral_count,
        "sentiment_trend": sentiment_trend,
        "top_headlines": top_headlines,
    }


def analyze_all_sentiment(tickers: list[str] = None) -> dict:
    """Run sentiment analysis on all tickers."""
    if tickers is None:
        tickers = get_all_tickers()
    
    results = []
    sentiment_scores = []
    buzz_scores = {}
    bullish_tickers = {}
    bearish_tickers = {}
    
    for ticker in tickers:
        articles = fetch_news(ticker)
        analysis = analyze_ticker_sentiment(ticker, articles)
        results.append(analysis)
        
        if analysis["buzz_score"] > 0:
            sentiment_scores.append(analysis["sentiment_score"])
            buzz_scores[ticker] = analysis["buzz_score"]
            
            if analysis["bullish_count"] > analysis["bearish_count"]:
                bullish_tickers[ticker] = analysis["bullish_count"]
            elif analysis["bearish_count"] > analysis["bullish_count"]:
                bearish_tickers[ticker] = analysis["bearish_count"]
        
        logger.info("%s: buzz=%d, sentiment=%.2f, trend=%s, interest=%s",
                   ticker, analysis["buzz_score"],
                   analysis["sentiment_score"],
                   analysis["sentiment_trend"],
                   analysis["retail_interest"])
    
    # Market summary
    overall_mood = 0.0
    if sentiment_scores:
        overall_mood = round(sum(sentiment_scores) / len(sentiment_scores), 2)
    
    # Mood label
    if overall_mood > 0.3:
        mood_label = "bullish"
    elif overall_mood > 0.1:
        mood_label = "slightly_bullish"
    elif overall_mood > -0.1:
        mood_label = "neutral"
    elif overall_mood > -0.3:
        mood_label = "slightly_bearish"
    else:
        mood_label = "bearish"
    
    # Find most_discussed: ticker with highest buzz_score
    most_discussed = max(buzz_scores, key=buzz_scores.get) if buzz_scores else None
    
    # Find most_bullish: ticker with highest bullish count
    most_bullish = max(bullish_tickers, key=bullish_tickers.get) if bullish_tickers else None
    
    # Find most_bearish: ticker with highest bearish count
    most_bearish = max(bearish_tickers, key=bearish_tickers.get) if bearish_tickers else None
    
    market_summary = {
        "overall_mood": overall_mood,
        "mood_label": mood_label,
        "most_discussed": most_discussed,
        "most_bullish": most_bullish,
        "most_bearish": most_bearish,
        "total_articles_analyzed": sum(r["buzz_score"] for r in results),
    }
    
    return {
        "results": results,
        "market_summary": market_summary,
    }


def main():
    setup_logging()
    logger.info("=== Social Sentiment Module ===")
    
    data = analyze_all_sentiment()
    has_results = len(data["results"]) > 0 and any(r["buzz_score"] > 0 for r in data["results"])
    
    status = "success" if has_results else "partial"
    error = None if has_results else "No news data available for any ticker"
    
    envelope = create_envelope("social_sentiment", data, status=status, error=error)
    save_envelope(envelope, "social_sentiment.json")
    logger.info("Social sentiment complete: %d tickers analyzed, %d with news",
               len(data["results"]),
               sum(1 for r in data["results"] if r["buzz_score"] > 0))


if __name__ == "__main__":
    main()
