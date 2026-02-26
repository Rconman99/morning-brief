"""Options analysis: max pain, PMCC selection, top strikes by open interest."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import math
import logging
from datetime import datetime, timedelta

import pandas as pd

from lib.data_envelope import create_envelope, save_envelope
from lib.api import yahoo_finance_options, yahoo_finance_price_history, yahoo_finance_info

logger = logging.getLogger(__name__)


def setup_logging():
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


def compute_delta(current_price: float, strike: float, days_to_expiry: int,
                  hist_vol: float | None = None) -> float | None:
    """Optional Black-Scholes delta. Returns None if scipy unavailable."""
    try:
        from scipy.stats import norm
        S = current_price
        K = strike
        T = days_to_expiry / 365
        if T <= 0:
            return None
        r = 0.045
        sigma = hist_vol or 0.30
        d1 = (math.log(S / K) + (r + sigma**2 / 2) * T) / (sigma * math.sqrt(T))
        return round(norm.cdf(d1), 4)
    except Exception:
        return None


def calculate_max_pain(calls_df: pd.DataFrame, puts_df: pd.DataFrame) -> float | None:
    """Calculate max pain by iterating candidate settlement prices across all strikes."""
    try:
        all_strikes = sorted(set(
            list(calls_df["strike"].dropna().unique()) +
            list(puts_df["strike"].dropna().unique())
        ))
        if not all_strikes:
            return None

        call_oi = dict(zip(calls_df["strike"], calls_df.get("openInterest", pd.Series(dtype=float)).fillna(0)))
        put_oi = dict(zip(puts_df["strike"], puts_df.get("openInterest", pd.Series(dtype=float)).fillna(0)))

        min_pain = float("inf")
        max_pain_price = None

        for candidate in all_strikes:
            total_pain = 0
            for strike in all_strikes:
                c_oi = call_oi.get(strike, 0)
                p_oi = put_oi.get(strike, 0)
                total_pain += c_oi * max(0, candidate - strike)
                total_pain += p_oi * max(0, strike - candidate)
            if total_pain < min_pain:
                min_pain = total_pain
                max_pain_price = candidate

        return max_pain_price
    except Exception as e:
        logger.warning("Max pain calculation error: %s", e)
        return None


def find_nearest_strike(df: pd.DataFrame, target: float) -> pd.Series | None:
    """Find the option row with strike closest to target."""
    if df.empty:
        return None
    idx = (df["strike"] - target).abs().idxmin()
    return df.loc[idx]


def analyze_ticker_options(ticker: str, current_price: float) -> dict | None:
    """Analyze options for a single ticker."""
    import yfinance as yf

    try:
        t = yf.Ticker(ticker)
        expiries = list(t.options) if t.options else []
    except Exception as e:
        logger.warning("Could not get options expiries for %s: %s", ticker, e)
        return None

    if not expiries:
        logger.info("No options data for %s (may be ETF), skipping", ticker)
        return {"ticker": ticker, "skipped": True, "reason": "No options data available"}

    today = datetime.now().date()

    # Find front month (>= 20 days out)
    front_month = None
    for exp in expiries:
        exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
        if (exp_date - today).days >= 20:
            front_month = exp
            break

    if not front_month:
        front_month = expiries[-1] if expiries else None

    # Find LEAPS (>= 180 days out)
    leaps_expiry = None
    for exp in expiries:
        exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
        if (exp_date - today).days >= 180:
            leaps_expiry = exp
            break

    result = {
        "ticker": ticker,
        "current_price": current_price,
        "front_month_expiry": front_month,
        "leaps_expiry": leaps_expiry,
    }

    # Get front month chain
    if front_month:
        try:
            chain = t.option_chain(front_month)
            if chain.calls.empty:
                logger.warning("%s: calls DataFrame is empty for %s, skipping", ticker, front_month)
                result["skipped"] = True
                result["reason"] = "Empty calls chain"
                return result

            # Max pain
            result["max_pain"] = calculate_max_pain(chain.calls, chain.puts)

            # Top 3 strikes by combined OI
            calls_oi = chain.calls[["strike", "openInterest"]].copy()
            calls_oi.columns = ["strike", "call_oi"]
            puts_oi = chain.puts[["strike", "openInterest"]].copy()
            puts_oi.columns = ["strike", "put_oi"]
            combined = calls_oi.merge(puts_oi, on="strike", how="outer").fillna(0)
            combined["total_oi"] = combined["call_oi"] + combined["put_oi"]
            top3 = combined.nlargest(3, "total_oi")
            result["top_strikes"] = [
                {"strike": float(row["strike"]), "total_oi": int(row["total_oi"])}
                for _, row in top3.iterrows()
            ]

            # Historical vol for delta
            hist = yahoo_finance_price_history(ticker, period="1y")
            hist_vol = None
            if not hist.empty:
                rets = hist["Close"].pct_change().dropna()
                if len(rets) >= 30:
                    hist_vol = float(rets.tail(30).std() * (252 ** 0.5))

            # PMCC selection via moneyness
            if leaps_expiry:
                try:
                    leaps_chain = t.option_chain(leaps_expiry)
                    if not leaps_chain.calls.empty:
                        # Long leg: strike ~ 0.85 * current (deep ITM)
                        long_target = current_price * 0.85
                        long_leg = find_nearest_strike(leaps_chain.calls, long_target)

                        # Short leg: strike ~ 1.05 * current (slightly OTM)
                        short_target = current_price * 1.05
                        short_leg = find_nearest_strike(chain.calls, short_target)

                        if (long_leg is not None and short_leg is not None and
                            long_leg.get("lastPrice", 0) > 0 and short_leg.get("lastPrice", 0) > 0):

                            front_days = (datetime.strptime(front_month, "%Y-%m-%d").date() - today).days
                            leaps_days = (datetime.strptime(leaps_expiry, "%Y-%m-%d").date() - today).days

                            result["pmcc"] = {
                                "long_strike": float(long_leg["strike"]),
                                "long_price": float(long_leg["lastPrice"]),
                                "long_expiry": leaps_expiry,
                                "long_delta": compute_delta(current_price, float(long_leg["strike"]), leaps_days, hist_vol),
                                "short_strike": float(short_leg["strike"]),
                                "short_price": float(short_leg["lastPrice"]),
                                "short_expiry": front_month,
                                "short_delta": compute_delta(current_price, float(short_leg["strike"]), front_days, hist_vol),
                                "net_debit": round(float(long_leg["lastPrice"]) - float(short_leg["lastPrice"]), 2),
                            }
                except Exception as e:
                    logger.warning("PMCC analysis failed for %s: %s", ticker, e)

        except Exception as e:
            logger.warning("Option chain analysis failed for %s: %s", ticker, e)
            result["error"] = str(e)

    return result


def main():
    setup_logging()
    logger.info("=== Options Module ===")

    portfolio_path = PROJECT_ROOT / "config" / "portfolio.json"
    if not portfolio_path.exists():
        envelope = create_envelope("options", {}, status="error", error="portfolio.json not found")
        save_envelope(envelope, "options.json")
        return

    portfolio = json.loads(portfolio_path.read_text())
    holdings = portfolio.get("holdings", [])

    results = []
    for h in holdings:
        ticker = h["ticker"]
        logger.info("Analyzing options for %s", ticker)

        # Get current price
        hist = yahoo_finance_price_history(ticker, period="5d")
        if hist.empty:
            info = yahoo_finance_info(ticker)
            current_price = info.get("regularMarketPrice") or info.get("previousClose")
            if not current_price:
                logger.warning("Cannot get price for %s, skipping options", ticker)
                results.append({"ticker": ticker, "skipped": True, "reason": "No price data"})
                continue
            current_price = float(current_price)
        else:
            current_price = float(hist["Close"].iloc[-1])

        analysis = analyze_ticker_options(ticker, current_price)
        if analysis:
            results.append(analysis)

    has_data = any(not r.get("skipped") for r in results)
    status = "success" if has_data else "partial" if results else "error"
    error = None if results else "No options data available"

    envelope = create_envelope("options", {"tickers": results}, status=status, error=error)
    save_envelope(envelope, "options.json")
    logger.info("Options analysis complete: %d tickers processed", len(results))


if __name__ == "__main__":
    main()
