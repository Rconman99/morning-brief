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

from lib.cache import make_cache_key, get_cached, set_cached, get_ticker_cache, set_ticker_cache

load_dotenv(PROJECT_ROOT / ".env")
logger = logging.getLogger(__name__)

AV_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "demo")
AV_BASE = "https://www.alphavantage.co/query"

# Lightweight API usage tracking
_api_stats = {"calls": 0, "failures": 0, "fallbacks": 0}


def get_api_stats() -> dict:
    """Return a copy of the current API usage stats."""
    return dict(_api_stats)


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
    """Download price history via yfinance with Alpha Vantage fallback. Returns empty DataFrame on failure."""
    _api_stats["calls"] += 1

    # 1. Check ticker cache
    cached = get_ticker_cache(ticker, "price_history")
    if cached is not None and isinstance(cached, pd.DataFrame) and not cached.empty:
        logger.info("Ticker cache hit for %s price_history", ticker)
        return cached

    # 2. Try yfinance
    df = pd.DataFrame()
    try:
        import yfinance as yf
        df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
        if df is not None and not df.empty:
            # Flatten MultiIndex columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            set_ticker_cache(ticker, "price_history", df)
            return df
        logger.warning("No yfinance price data for %s", ticker)
    except Exception as e:
        logger.warning("yfinance price history error for %s: %s", ticker, e)

    # 3. Fallback to Alpha Vantage TIME_SERIES_DAILY
    if AV_KEY != "demo":
        _api_stats["fallbacks"] += 1
        logger.info("Falling back to Alpha Vantage for %s price history", ticker)
        try:
            av_data = alpha_vantage_call("TIME_SERIES_DAILY", {"symbol": ticker, "outputsize": "full"})
            ts = av_data.get("Time Series (Daily)", {})
            if ts:
                rows = []
                for date_str, vals in ts.items():
                    rows.append({
                        "Date": pd.Timestamp(date_str),
                        "Open": float(vals.get("1. open", 0)),
                        "High": float(vals.get("2. high", 0)),
                        "Low": float(vals.get("3. low", 0)),
                        "Close": float(vals.get("4. close", 0)),
                        "Volume": int(float(vals.get("5. volume", 0))),
                    })
                df = pd.DataFrame(rows).set_index("Date").sort_index()
                set_ticker_cache(ticker, "price_history", df)
                return df
        except Exception as e:
            logger.warning("Alpha Vantage price history fallback error for %s: %s", ticker, e)

    # 4. Total failure
    _api_stats["failures"] += 1
    return pd.DataFrame()


def yahoo_finance_info(ticker: str) -> dict:
    """Get ticker info via yfinance with Alpha Vantage fallback. UNRELIABLE — always use .get(). Returns {} on failure."""
    _api_stats["calls"] += 1

    # 1. Check ticker cache
    cached = get_ticker_cache(ticker, "info")
    if cached is not None and isinstance(cached, dict) and cached:
        logger.info("Ticker cache hit for %s info", ticker)
        return cached

    # 2. Try yfinance
    info = {}
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        if isinstance(info, dict) and info:
            set_ticker_cache(ticker, "info", info)
            return info
        logger.warning("Empty yfinance info for %s", ticker)
        info = {}
    except Exception as e:
        logger.warning("yfinance info error for %s: %s", ticker, e)

    # 3. Fallback to Alpha Vantage OVERVIEW
    if AV_KEY != "demo":
        _api_stats["fallbacks"] += 1
        logger.info("Falling back to Alpha Vantage OVERVIEW for %s", ticker)
        try:
            av_data = alpha_vantage_call("OVERVIEW", {"symbol": ticker})
            if av_data:
                # Normalize AV field names to match yfinance conventions
                info = _normalize_av_overview(av_data)
                set_ticker_cache(ticker, "info", info)
                return info
        except Exception as e:
            logger.warning("Alpha Vantage OVERVIEW fallback error for %s: %s", ticker, e)

    # 4. Total failure
    _api_stats["failures"] += 1
    return {}


def _normalize_av_overview(av: dict) -> dict:
    """Map Alpha Vantage OVERVIEW fields to yfinance-compatible field names."""
    def _safe_float(val):
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    normalized = dict(av)  # keep all original AV fields too
    field_map = {
        "trailingPE": "PERatio",
        "forwardPE": "ForwardPE",
        "enterpriseToEbitda": "EVToEBITDA",
        "pegRatio": "PEGRatio",
        "priceToBook": "PriceToBookRatio",
        "marketCap": "MarketCapitalization",
        "bookValue": "BookValue",
        "dividendYield": "DividendYield",
        "profitMargins": "ProfitMargin",
        "returnOnEquity": "ReturnOnEquityTTM",
        "beta": "Beta",
        "sector": "Sector",
        "industry": "Industry",
        "longName": "Name",
        "shortName": "Name",
    }
    for yf_name, av_name in field_map.items():
        if av_name in av and yf_name not in normalized:
            val = _safe_float(av[av_name])
            normalized[yf_name] = val if val is not None else av[av_name]
    return normalized


def get_price_with_fallback(ticker: str, period: str = "2y") -> pd.DataFrame:
    """Convenience alias that logs which source succeeded."""
    df = yahoo_finance_price_history(ticker, period=period)
    if not df.empty:
        logger.info("get_price_with_fallback: got %d rows for %s", len(df), ticker)
    else:
        logger.warning("get_price_with_fallback: no data for %s from any source", ticker)
    return df


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
