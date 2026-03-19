"""News sentiment analysis: yfinance news + RSS feed aggregation + Claude Haiku (or keyword fallback)."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import os
import re
import logging

import requests

from lib.data_envelope import create_envelope, save_envelope
from lib.cache import make_cache_key, get_cached, set_cached

logger = logging.getLogger(__name__)

# Try feedparser first, fall back to xml.etree.ElementTree
try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False

# ---------------------------------------------------------------------------
# RSS Feed Configuration
# ---------------------------------------------------------------------------

RSS_FEEDS = [
    # Tier 1 — major financial news
    ("https://feeds.content.dowjones.io/public/rss/mw_topstories", "MarketWatch"),
    ("https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC"),
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US", "Yahoo Finance"),
    ("https://seekingalpha.com/market_currents.xml", "Seeking Alpha"),
    ("https://www.investing.com/rss/news.rss", "Investing.com"),
]

# Common false positives for ticker extraction
TICKER_BLACKLIST = {
    "CEO", "CFO", "CTO", "COO", "IPO", "ETF", "SEC", "FDA", "GDP", "CPI",
    "FED", "NYSE", "NYSE", "API", "AI", "EV", "PE", "US", "UK", "EU",
    "THE", "AND", "FOR", "ARE", "NOT", "HAS", "WAS", "BUT", "ALL", "NEW",
    "ITS", "CAN", "HAD", "HER", "ONE", "OUR", "OUT", "YOU", "HIS", "HOW",
    "MAN", "OLD", "SAY", "SHE", "TOO", "WHO", "AM", "PM", "PT", "EST",
}

# Regex for extracting potential tickers (1-5 uppercase letters)
_TICKER_RE = re.compile(r'\b([A-Z]{1,5})\b')

RSS_REQUEST_TIMEOUT = 8  # seconds per feed


def _extract_tickers(text: str) -> list[str]:
    """Extract potential stock tickers from text, filtering out common false positives."""
    if not text:
        return []
    candidates = _TICKER_RE.findall(text)
    return [t for t in candidates if t not in TICKER_BLACKLIST]


def _parse_feed_feedparser(url: str, source: str, max_items: int) -> list[dict]:
    """Parse an RSS feed using feedparser."""
    feed = feedparser.parse(url)
    articles = []
    for entry in feed.entries[:max_items]:
        title = entry.get("title", "")
        published = entry.get("published", entry.get("updated", ""))
        tickers = _extract_tickers(title)
        articles.append({
            "title": title,
            "source": source,
            "tickers": tickers,
            "published": published,
        })
    return articles


def _parse_feed_xml(content: bytes, source: str, max_items: int) -> list[dict]:
    """Parse an RSS feed using xml.etree.ElementTree as fallback."""
    import xml.etree.ElementTree as ET
    articles = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []

    # Handle both RSS 2.0 (<channel><item>) and Atom (<entry>) formats
    items = root.findall(".//item")
    if not items:
        # Try Atom namespace
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//atom:entry", ns)

    for item in items[:max_items]:
        title_el = item.find("title")
        if title_el is None:
            # Try with atom namespace
            title_el = item.find("{http://www.w3.org/2005/Atom}title")
        title = title_el.text if title_el is not None and title_el.text else ""

        pub_el = item.find("pubDate")
        if pub_el is None:
            pub_el = item.find("published") or item.find("{http://www.w3.org/2005/Atom}published")
        published = pub_el.text if pub_el is not None and pub_el.text else ""

        tickers = _extract_tickers(title)
        articles.append({
            "title": title,
            "source": source,
            "tickers": tickers,
            "published": published,
        })
    return articles


def fetch_rss_articles(max_per_feed: int = 20) -> list[dict]:
    """Fetch articles from RSS feeds, extract mentioned tickers.

    Returns list of {"title": str, "source": str, "tickers": list[str], "published": str}.
    Uses feedparser if available, otherwise falls back to xml.etree.ElementTree.
    Each feed is wrapped in try/except — if a feed is down, it is skipped.
    """
    # Check cache first — RSS results are cached for the day
    cache_key = make_cache_key("rss", "all_feeds", {"max_per_feed": max_per_feed})
    cached = get_cached(cache_key, max_age_hours=4)
    if cached is not None:
        logger.info("Using cached RSS articles (%d articles)", len(cached))
        return cached

    all_articles = []

    for url, source in RSS_FEEDS:
        try:
            if HAS_FEEDPARSER:
                # feedparser can handle URL fetching directly, but we use
                # requests for consistent timeout control
                resp = requests.get(url, timeout=RSS_REQUEST_TIMEOUT, headers={
                    "User-Agent": "MorningBrief/1.0 (RSS aggregator)"
                })
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
                articles = []
                for entry in feed.entries[:max_per_feed]:
                    title = entry.get("title", "")
                    published = entry.get("published", entry.get("updated", ""))
                    tickers = _extract_tickers(title)
                    articles.append({
                        "title": title,
                        "source": source,
                        "tickers": tickers,
                        "published": published,
                    })
            else:
                resp = requests.get(url, timeout=RSS_REQUEST_TIMEOUT, headers={
                    "User-Agent": "MorningBrief/1.0 (RSS aggregator)"
                })
                resp.raise_for_status()
                articles = _parse_feed_xml(resp.content, source, max_per_feed)

            all_articles.extend(articles)
            logger.info("RSS: fetched %d articles from %s", len(articles), source)

        except Exception as e:
            logger.warning("RSS: failed to fetch %s: %s", source, e)
            continue

    # Cache the results
    if all_articles:
        set_cached(cache_key, all_articles)

    logger.info("RSS: total %d articles from %d feeds", len(all_articles), len(RSS_FEEDS))
    return all_articles


def calculate_mention_scores(rss_articles: list[dict], target_tickers: list[str]) -> dict:
    """Count how many times each target ticker appears across RSS sources.

    Returns {ticker: {"mentions": int, "sources": list[str], "buzz_score": float}}
    buzz_score = mentions / total_articles (normalized 0-1)
    """
    total_articles = len(rss_articles) if rss_articles else 1  # avoid div by zero

    scores = {}
    for ticker in target_tickers:
        mentions = 0
        sources = set()
        for article in rss_articles:
            if ticker in article.get("tickers", []):
                mentions += 1
                sources.add(article["source"])
        buzz_score = round(mentions / total_articles, 4)
        scores[ticker] = {
            "mentions": mentions,
            "sources": sorted(sources),
            "buzz_score": buzz_score,
        }
    return scores


# ---------------------------------------------------------------------------
# Ticker loading
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# yfinance news fetching
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Sentiment scoring
# ---------------------------------------------------------------------------

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


def analyze_sentiment(ticker: str, articles: list[dict] = None,
                      mention_data: dict = None) -> dict | None:
    """Analyze news sentiment for a single ticker.

    Merges yfinance news sentiment with RSS buzz data when available.
    Combined score: final_score = 0.6 * sentiment_score + 0.4 * buzz_adjustment
    where buzz_adjustment = +0.3 if buzz_score > 0.1, -0.1 if buzz_score == 0, else 0.
    """
    if articles is None:
        articles = fetch_news(ticker)

    if not articles:
        logger.info("%s: no news articles found", ticker)
        # Even without yfinance news, we can still report RSS buzz if available
        if mention_data and mention_data.get("mentions", 0) > 0:
            buzz = mention_data["buzz_score"]
            buzz_adj = 0.3 if buzz > 0.1 else 0.0
            return {
                "ticker": ticker,
                "sentiment_score": round(0.4 * buzz_adj, 2),
                "article_count": 0,
                "key_headline": "",
                "summary": f"No yfinance news. RSS buzz: {mention_data['mentions']} mentions across {len(mention_data['sources'])} sources.",
                "method": "rss_only",
                "rss_mentions": mention_data["mentions"],
                "rss_sources": mention_data["sources"],
                "buzz_score": buzz,
            }
        return None

    # Try AI first, fall back to keywords
    result = ai_sentiment(ticker, articles)
    if result is None:
        result = keyword_sentiment(articles)

    if result:
        result["ticker"] = ticker

        # Merge RSS buzz data
        rss_mentions = 0
        rss_sources = []
        buzz_score = 0.0

        if mention_data:
            rss_mentions = mention_data.get("mentions", 0)
            rss_sources = mention_data.get("sources", [])
            buzz_score = mention_data.get("buzz_score", 0.0)

        # Calculate combined score
        raw_sentiment = result["sentiment_score"]
        if buzz_score > 0.1:
            buzz_adjustment = 0.3
        elif buzz_score == 0:
            buzz_adjustment = -0.1
        else:
            buzz_adjustment = 0.0

        final_score = 0.6 * raw_sentiment + 0.4 * buzz_adjustment
        final_score = round(max(-1.0, min(1.0, final_score)), 2)

        result["sentiment_score"] = final_score
        result["raw_sentiment_score"] = raw_sentiment
        result["rss_mentions"] = rss_mentions
        result["rss_sources"] = rss_sources
        result["buzz_score"] = buzz_score

    return result


def analyze_all_sentiment(tickers: list[str] = None) -> dict:
    """Run sentiment analysis on all tickers.

    RSS feeds are fetched ONCE at the start and mention scores are
    calculated per ticker, then merged into each ticker's sentiment result.
    """
    if tickers is None:
        tickers = get_all_tickers()

    # Fetch RSS articles once for all tickers
    logger.info("Fetching RSS feeds for buzz analysis...")
    rss_articles = fetch_rss_articles()
    mention_scores = calculate_mention_scores(rss_articles, tickers)
    logger.info("RSS buzz scores calculated for %d tickers", len(mention_scores))

    results = []
    for ticker in tickers:
        mention_data = mention_scores.get(ticker)
        result = analyze_sentiment(ticker, mention_data=mention_data)
        if result:
            results.append(result)
            logger.info("%s: sentiment=%.2f (%s) articles=%d rss_mentions=%d buzz=%.4f",
                        ticker, result["sentiment_score"],
                        result.get("method", "unknown"),
                        result["article_count"],
                        result.get("rss_mentions", 0),
                        result.get("buzz_score", 0.0))

    return {
        "results": results,
        "rss_article_count": len(rss_articles),
        "rss_feeds_configured": len(RSS_FEEDS),
    }


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
    logger.info("News sentiment complete: %d tickers analyzed, %d RSS articles fetched",
                len(data["results"]), data.get("rss_article_count", 0))


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


if __name__ == "__main__":
    main()
