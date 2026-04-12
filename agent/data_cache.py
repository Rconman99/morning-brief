"""SQLite cache for yfinance OHLCV data.

Eliminates live API dependency for backtesting by storing
historical price data locally. yfinance is unreliable and
rate-limited — caching makes backtests deterministic and fast.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import sqlite3
import logging
from datetime import datetime, date

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

DB_PATH = PROJECT_ROOT / "agent" / "market_data.db"


def _get_conn() -> sqlite3.Connection:
    """Get a connection to the cache database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_cache_db() -> None:
    """Create the OHLCV cache table if it doesn't exist."""
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                adjusted_close REAL,
                PRIMARY KEY (date, ticker)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker
            ON ohlcv (ticker, date)
        """)
        conn.commit()
        logger.info("Cache DB initialized at %s", DB_PATH)
    finally:
        conn.close()


def cache_ohlcv(ticker: str, period: str = "2y") -> int:
    """Download OHLCV data from yfinance and store to SQLite.

    Returns the number of rows inserted/updated.
    """
    try:
        df = yf.download(ticker, period=period, progress=False, auto_adjust=False)
    except Exception as e:
        logger.error("yfinance download failed for %s: %s", ticker, e)
        return 0

    if df.empty:
        logger.warning("No data returned for %s", ticker)
        return 0

    # yfinance returns MultiIndex columns when downloading single ticker
    # with newer versions — flatten if needed
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    conn = _get_conn()
    try:
        rows = 0
        for idx, row in df.iterrows():
            dt = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
            conn.execute("""
                INSERT OR REPLACE INTO ohlcv
                    (date, ticker, open, high, low, close, volume, adjusted_close)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                dt, ticker,
                float(row.get("Open", 0)),
                float(row.get("High", 0)),
                float(row.get("Low", 0)),
                float(row.get("Close", 0)),
                float(row.get("Volume", 0)),
                float(row.get("Adj Close", row.get("Close", 0))),
            ))
            rows += 1
        conn.commit()
        logger.info("Cached %d rows for %s", rows, ticker)
        return rows
    finally:
        conn.close()


def get_cached_ohlcv(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Load cached OHLCV data for a ticker between start_date and end_date.

    Args:
        ticker: Stock symbol
        start_date: YYYY-MM-DD format
        end_date: YYYY-MM-DD format

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume, Adj Close
        indexed by date. Empty DataFrame if no data found.
    """
    conn = _get_conn()
    try:
        query = """
            SELECT date, open, high, low, close, volume, adjusted_close
            FROM ohlcv
            WHERE ticker = ? AND date >= ? AND date <= ?
            ORDER BY date
        """
        df = pd.read_sql_query(query, conn, params=(ticker, start_date, end_date))

        if df.empty:
            return pd.DataFrame()

        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df.columns = ["Open", "High", "Low", "Close", "Volume", "Adj Close"]
        return df
    finally:
        conn.close()


def refresh_cache(tickers: list[str], period: str = "2y") -> dict:
    """Update cache for all tickers. Returns {ticker: rows_cached}."""
    results = {}
    for ticker in tickers:
        rows = cache_ohlcv(ticker, period=period)
        results[ticker] = rows
        logger.info("Refreshed %s: %d rows", ticker, rows)
    return results


def get_cache_age(ticker: str) -> float | None:
    """Return days since the most recent cached data point for a ticker.

    Returns None if ticker has no cached data.
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT MAX(date) FROM ohlcv WHERE ticker = ?", (ticker,)
        ).fetchone()
        if not row or not row[0]:
            return None
        last_date = datetime.strptime(row[0], "%Y-%m-%d").date()
        delta = date.today() - last_date
        return delta.days
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    init_cache_db()
    tickers = ["AAPL", "NVDA", "AMD", "MSFT", "AMZN"]
    results = refresh_cache(tickers)
    for t, n in results.items():
        age = get_cache_age(t)
        print(f"  {t}: {n} rows cached, {age} days old")
