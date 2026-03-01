"""News sentiment analysis: yfinance news + Claude Haiku (or keyword fallback)."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import os
import logging

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
    """Fetch news articles for a ticker via yfinance."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        news = t.news
        if not news:
            return []
        articles = []
        for item in news:
            content = item.get("content", {}) if isinstance(item, dict) else {}
            title = content.get("title", item.get("title", ""))
            summary = content.get("summary", item.get("summary", ""))
            articles.append({"title": title, "summary": summary})
        return articles
    except Exception as e:
        logger.warning("Failed to fetch news for %s: %s", ticker, e)
        return []


BULLISH_KEYWORDS = [
    "beat", "surge", "rally", "upgrade", "bullish", "growth", "record",
    "strong", "outperform", "buy", "positive", "gain", "rise", "boost",
    "breakout", "momentum", "profit", "revenue beat", "exceeds",
]

BEARISH_KEYWORDS = [
    "miss", "decline", "crash", "downgrade", "bearish", "loss", "weak",
    "underperform", "sell", "negative", "drop", "fall", "cut", "warning",
    "layoff", "investigation", "lawsuit", "recall", "risk", "concern",
]


def keyword_sentiment(articles: list[dict]) -> dict:
    """Simple keyword-based sentiment scoring. Fallback when no API key."""
    if not articles:
        return None

    bullish_count = 0
    bearish_count = 0
    key_headline = articles[0]["title"] if articles else ""

    for article in articles:
        text = (article.get("title", "") + " " + article.get("summary", "")).lower()
        for kw in BULLISH_KEYWORDS:
            if kw in text:
                bullish_count += 1
        for kw in BEARISH_KEYWORDS:
            if kw in text:
                bearish_count += 1

    total = bullish_count + bearish_count
    if total == 0:
        score = 0.0
    else:
        score = round((bullish_count - bearish_count) / total, 2)
    score = max(-1.0, min(1.0, score))

    return {
        "sentiment_score": score,
        "article_count": len(articles),
        "key_headline": key_headline,
        "summary": f"Keyword analysis: {bullish_count} bullish, {bearish_count} bearish signals.",
        "method": "keyword",
    }


def ai_sentiment(ticker: str, articles: list[dict]) -> dict | None:
    """Use Claude Haiku for nuanced sentiment analysis."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    headlines = "\n".join(
        f"- {a['title']}" + (f": {a['summary'][:100]}" if a.get("summary") else "")
        for a in articles[:15]
    )

    prompt = f"""Analyze these news headlines for {ticker}. Return ONLY valid JSON, no other text:
{{
  "sentiment_score": <float from -1.0 to +1.0>,
  "article_count": {len(articles)},
  "key_headline": "<most impactful headline>",
  "summary": "<1 sentence sentiment summary>"
}}

Headlines:
{headlines}"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Extract JSON from response
        if text.startswith("{"):
            result = json.loads(text)
        else:
            # Try to find JSON in the response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(text[start:end])
            else:
                logger.warning("Could not parse AI response for %s", ticker)
                return None

        result["sentiment_score"] = max(-1.0, min(1.0, float(result.get("sentiment_score", 0))))
        result["method"] = "claude"
        return result
    except Exception as e:
        logger.warning("AI sentiment failed for %s: %s", ticker, e)
        return None


def analyze_sentiment(ticker: str, articles: list[dict] = None) -> dict | None:
    """Analyze news sentiment for a single ticker."""
    if articles is None:
        articles = fetch_news(ticker)

    if not articles:
        logger.info("%s: no news articles found", ticker)
        return None

    # Try AI first, fall back to keywords
    result = ai_sentiment(ticker, articles)
    if result is None:
        result = keyword_sentiment(articles)

    if result:
        result["ticker"] = ticker
    return result


def analyze_all_sentiment(tickers: list[str] = None) -> dict:
    """Run sentiment analysis on all tickers."""
    if tickers is None:
        tickers = get_all_tickers()

    results = []
    for ticker in tickers:
        result = analyze_sentiment(ticker)
        if result:
            results.append(result)
            logger.info("%s: sentiment=%.2f (%s) articles=%d",
                        ticker, result["sentiment_score"],
                        result.get("method", "unknown"),
                        result["article_count"])

    return {"results": results}


def main():
    setup_logging()
    logger.info("=== News Sentiment Module ===")

    data = analyze_all_sentiment()
    has_results = len(data["results"]) > 0
    all_tickers = get_all_tickers()
    status = "success" if has_results else "partial"
    if has_results and len(data["results"]) < len(all_tickers):
        status = "partial"
    error = None if has_results else "No news data available for any ticker"

    envelope = create_envelope("news_sentiment", data, status=status, error=error)
    save_envelope(envelope, "news_sentiment.json")
    logger.info("News sentiment complete: %d tickers analyzed", len(data["results"]))


if __name__ == "__main__":
    main()
