"""
Dividend tracker module: Monitor dividend information for portfolio holdings and watchlist.

Tracks ex-dates, yields, payout ratios, dividend growth, and DRIP impact.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime, timedelta
import pandas as pd

from lib.data_envelope import create_envelope, save_envelope

logger = logging.getLogger(__name__)


def setup_logging():
    """Configure logging. Call only in main()."""
    log_dir = PROJECT_ROOT / "data" / "outputs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_dir / "run.log", mode="a")
            ]
        )


def load_config(filename: str) -> dict:
    """Load JSON config file. Returns {} on failure."""
    try:
        path = PROJECT_ROOT / "config" / filename
        return json.loads(path.read_text())
    except Exception as e:
        logger.warning("Failed to load config %s: %s", filename, e)
        return {}


def get_treasury_yield() -> float:
    """Load 10-year treasury yield from risk_dashboard.json if available, else use default 4.5%."""
    try:
        path = PROJECT_ROOT / "data" / "processed" / "risk_dashboard.json"
        if path.exists():
            data = json.loads(path.read_text())
            yield_val = data.get("data", {}).get("treasury_10y_yield")
            if yield_val is not None:
                return float(yield_val)
    except Exception:
        pass
    return 0.045


def get_ticker_info(ticker: str) -> dict:
    """Get ticker info from yfinance. Returns {} on error."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        return info if isinstance(info, dict) else {}
    except Exception as e:
        logger.warning("yfinance info error for %s: %s", ticker, e)
        return {}


def get_dividend_history(ticker: str) -> pd.Series:
    """Get dividend history from yfinance. Returns empty Series on error."""
    try:
        import yfinance as yf
        dividends = yf.Ticker(ticker).dividends
        return dividends if isinstance(dividends, pd.Series) and not dividends.empty else pd.Series()
    except Exception as e:
        logger.warning("yfinance dividends error for %s: %s", ticker, e)
        return pd.Series()


def get_current_price(ticker: str) -> float:
    """Get current price from yfinance. Returns None on error."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        return float(price) if price else None
    except Exception as e:
        logger.warning("yfinance price error for %s: %s", ticker, e)
        return None


def calculate_div_growth_3y(dividends: pd.Series) -> float:
    """Calculate 3-year dividend growth rate from history.
    
    Compares most recent 4 quarterly dividends to dividends from 12 quarters ago.
    Returns growth rate as decimal, or None if insufficient data.
    """
    if dividends.empty or len(dividends) < 16:
        return None
    
    try:
        # Sort by date
        divs = dividends.sort_index()
        
        # Most recent 4 quarters
        recent_sum = divs.tail(4).sum()
        
        # 12 quarters ago (3 years back) = 16 dividends back, take 4 of those
        if len(divs) >= 16:
            past_sum = divs.iloc[-16:-12].sum()
            if past_sum > 0:
                growth = (recent_sum - past_sum) / past_sum
                return round(float(growth), 4)
    except Exception as e:
        logger.debug("Error calculating 3-year dividend growth for: %s", e)
    
    return None


def detect_dividend_cut(dividends: pd.Series) -> bool:
    """Check if latest dividend < previous dividend."""
    if dividends.empty or len(dividends) < 2:
        return False
    
    try:
        divs = dividends.sort_index()
        latest = divs.iloc[-1]
        previous = divs.iloc[-2]
        return bool(latest < previous)
    except Exception:
        return False


def process_ticker(ticker: str, shares: int = None, in_portfolio: bool = False) -> dict:
    """Process a single ticker for dividend analysis.
    
    Args:
        ticker: Stock ticker symbol
        shares: Number of shares held (for portfolio holdings)
        in_portfolio: Whether this is a portfolio holding
    
    Returns:
        Dictionary with dividend data for this ticker
    """
    info = get_ticker_info(ticker)
    if not info:
        logger.warning("No data available for %s", ticker)
        return {
            "ticker": ticker,
            "in_portfolio": in_portfolio,
            "shares": shares,
            "dividend_rate": 0,
            "current_yield": None,
            "annual_income": None,
            "next_ex_date": None,
            "days_until_ex": None,
            "ex_date_alert": False,
            "payout_ratio": None,
            "div_growth_3y": None,
            "drip_shares": None,
            "yield_vs_10y": None,
            "dividend_cut": False
        }
    
    # Get dividend data
    dividend_rate = info.get("dividendRate", 0) or 0
    dividend_yield = info.get("dividendYield") or 0
    ex_date_ts = info.get("exDividendDate")
    payout_ratio = info.get("payoutRatio")
    
    # Get current price
    current_price = get_current_price(ticker)
    
    # Calculate yield (prefer from info, calculate if not available)
    if dividend_yield and dividend_yield > 0:
        current_yield = round(dividend_yield, 6)
    elif dividend_rate and current_price and current_price > 0:
        current_yield = round(dividend_rate / current_price, 6)
    else:
        current_yield = None
    
    # Calculate annual income for portfolio holdings
    annual_income = None
    if in_portfolio and shares is not None and dividend_rate > 0:
        annual_income = round(dividend_rate * shares, 2)
    
    # Parse ex-dividend date
    next_ex_date = None
    days_until_ex = None
    ex_date_alert = False
    
    if ex_date_ts:
        try:
            if isinstance(ex_date_ts, int):
                ex_date = datetime.fromtimestamp(ex_date_ts)
            else:
                ex_date = pd.to_datetime(ex_date_ts)
            
            today = datetime.now()
            if ex_date > today:
                next_ex_date = ex_date.strftime("%Y-%m-%d")
                days_until_ex = (ex_date - today).days
                ex_date_alert = days_until_ex <= 7
        except Exception as e:
            logger.debug("Error parsing ex-dividend date for %s: %s", ticker, e)
    
    # Get dividend history for growth calculation
    dividends = get_dividend_history(ticker)
    div_growth_3y = calculate_div_growth_3y(dividends)
    dividend_cut = detect_dividend_cut(dividends)
    
    # DRIP shares calculation (only for portfolio holdings with valid data)
    drip_shares = None
    if in_portfolio and shares is not None and dividend_rate > 0 and current_price and current_price > 0:
        quarterly_div = (dividend_rate / 4) * shares
        drip_shares = round(quarterly_div / current_price, 4)
    
    # Yield vs 10-year treasury
    treasury_yield = get_treasury_yield()
    yield_vs_10y = None
    if current_yield is not None and treasury_yield:
        if current_yield > treasury_yield:
            yield_vs_10y = "above_treasury"
        elif current_yield < treasury_yield:
            yield_vs_10y = "below_treasury"
        else:
            yield_vs_10y = "at_treasury"
    
    return {
        "ticker": ticker,
        "in_portfolio": in_portfolio,
        "shares": shares,
        "dividend_rate": round(float(dividend_rate), 4) if dividend_rate else 0,
        "current_yield": current_yield,
        "annual_income": annual_income,
        "next_ex_date": next_ex_date,
        "days_until_ex": days_until_ex,
        "ex_date_alert": ex_date_alert,
        "payout_ratio": round(float(payout_ratio), 4) if payout_ratio else None,
        "div_growth_3y": div_growth_3y,
        "drip_shares": drip_shares,
        "yield_vs_10y": yield_vs_10y,
        "dividend_cut": dividend_cut
    }


def main():
    """Main entry point: analyze dividend data and generate envelope."""
    setup_logging()
    logger.info("=== Building module: dividend_tracker ===")
    
    try:
        # Load config
        portfolio_config = load_config("portfolio.json")
        watchlist_config = load_config("watchlist.json")
        
        # Collect all tickers: portfolio holdings + watchlist
        portfolio_holdings = portfolio_config.get("holdings", [])
        watchlist_tickers = watchlist_config.get("tickers", [])
        
        results = []
        seen_tickers = set()
        
        # Process portfolio holdings first
        for holding in portfolio_holdings:
            ticker = holding.get("ticker", "").upper()
            if not ticker:
                continue
            seen_tickers.add(ticker)
            shares = holding.get("shares", 0)
            result = process_ticker(ticker, shares=shares, in_portfolio=True)
            results.append(result)
        
        # Process watchlist tickers not already in portfolio
        for ticker in watchlist_tickers:
            ticker = ticker.upper()
            if ticker in seen_tickers:
                continue
            result = process_ticker(ticker, in_portfolio=False)
            results.append(result)
        
        # Calculate portfolio summary
        total_annual_income = 0.0
        yields = []
        upcoming_ex_dates = []
        total_drip_value = 0.0
        
        for result in results:
            if result.get("annual_income"):
                total_annual_income += result["annual_income"]
            if result.get("current_yield") is not None:
                yields.append(result["current_yield"])
            if result.get("ex_date_alert") and result.get("next_ex_date") and result.get("days_until_ex"):
                upcoming_ex_dates.append({
                    "ticker": result["ticker"],
                    "ex_date": result["next_ex_date"],
                    "days": result["days_until_ex"]
                })
            if result.get("drip_shares"):
                # Estimate DRIP value using current_yield and annual_income if available
                if result.get("annual_income"):
                    total_drip_value += (result["annual_income"] / 4)  # Quarterly estimate
        
        avg_yield = round(sum(yields) / len(yields), 6) if yields else None
        total_annual_income = round(total_annual_income, 2)
        total_drip_value = round(total_drip_value, 2)
        
        # Sort upcoming ex-dates by days remaining
        upcoming_ex_dates.sort(key=lambda x: x["days"])
        
        portfolio_summary = {
            "total_annual_income": total_annual_income,
            "avg_yield": avg_yield,
            "upcoming_ex_dates": upcoming_ex_dates,
            "total_drip_value": total_drip_value
        }
        
        # Determine status
        status = "success" if results else "partial"
        error_msg = None
        
        data = {
            "results": results,
            "portfolio_summary": portfolio_summary
        }
        
        envelope = create_envelope("dividend_tracker", data, status=status, error=error_msg)
        save_envelope(envelope, "dividend_tracker.json")
        
        logger.info("Dividend tracker complete: %d tickers processed", len(results))
        print("=== dividend_tracker module completed successfully ===")
        
    except Exception as e:
        logger.error("Dividend tracker failed: %s", e, exc_info=True)
        error_msg = f"Module error: {str(e)}"
        envelope = create_envelope("dividend_tracker", {}, status="error", error=error_msg)
        save_envelope(envelope, "dividend_tracker.json")
        print(f"=== dividend_tracker module failed: {error_msg} ===")


if __name__ == "__main__":
    main()
