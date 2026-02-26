"""API wrappers for Alpha Vantage and Yahoo Finance."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import os
import time
import logging

import pandas as pd
import requests
from dotenv import load_dotenv

from lib.cache import make_cache_key, get_cached, set_cached

load_dotenv(PROJECT_ROOT / ".env")
logger = logging.getLogger(__name__)

AV_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "demo")
AV_BASE = "https://www.alphavantage.co/query"


def alpha_vantage_call(function: str, params: dict) -> dict:
    """Call Alpha Vantage API with caching. Returns {} on any error."""
    cache_key = make_cache_key("av", function, params)
    cached = get_cached(cache_key)
    if cached is not None:
        return cached
    if AV_KEY == "demo":
        logger.info("Alpha Vantage key is 'demo', skipping live call for %s", function)
        return {}
    try:
        full_params = {"function": function, "apikey": AV_KEY, **params}
        resp = requests.get(AV_BASE, params=full_params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        time.sleep(12)
        set_cached(cache_key, data)
        return data
    except Exception as e:
        logger.warning("Alpha Vantage error for %s: %s", function, e)
        return {}


def yahoo_finance_price_history(ticker: str, period: str = "2y") -> pd.DataFrame:
    """Download price history via yfinance. Returns empty DataFrame on failure."""
    try:
        import yfinance as yf
        df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
        if df is None or df.empty:
            logger.warning("No price data for %s", ticker)
            return pd.DataFrame()
        # Flatten MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        logger.warning("yfinance price history error for %s: %s", ticker, e)
        return pd.DataFrame()


def yahoo_finance_info(ticker: str) -> dict:
    """Get ticker info via yfinance. UNRELIABLE — always use .get(). Returns {} on failure."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        return info if isinstance(info, dict) else {}
    except Exception as e:
        logger.warning("yfinance info error for %s: %s", ticker, e)
        return {}


def yahoo_finance_options(ticker: str) -> tuple[list, dict]:
    """Get options data. Returns (expiry_dates, {"calls": df, "puts": df}) or ([], {}) on failure."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        expiries = list(t.options) if t.options else []
        if not expiries:
            logger.info("No options data for %s (may be ETF)", ticker)
            return [], {}
        chain = t.option_chain(expiries[0])
        return expiries, {"calls": chain.calls, "puts": chain.puts}
    except Exception as e:
        logger.warning("yfinance options error for %s: %s", ticker, e)
        return [], {}
