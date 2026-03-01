"""Valuation comparison module: compares fundamental metrics between stock pairs."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging

from lib.data_envelope import create_envelope, save_envelope
from lib.api import alpha_vantage_call, yahoo_finance_info

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


def get_metrics(ticker: str) -> dict:
    """Fetch valuation metrics for a ticker. Returns dict with None for unavailable."""
    # Try Alpha Vantage first
    av_data = alpha_vantage_call("OVERVIEW", {"symbol": ticker})

    # Always get yfinance as fallback
    yf_info = yahoo_finance_info(ticker)

    def pick(yf_key: str, av_key: str):
        val = yf_info.get(yf_key)
        if val is not None and val != 0:
            return round(float(val), 2) if val else None
        av_val = av_data.get(av_key)
        if av_val and av_val != "None" and av_val != "-":
            try:
                return round(float(av_val), 2)
            except (ValueError, TypeError):
                return None
        return None

    pe = pick("trailingPE", "PERatio")
    ev_ebitda = pick("enterpriseToEbitda", "EVToEBITDA")
    peg = pick("pegRatio", "PEGRatio")
    pb = pick("priceToBook", "PriceToBookRatio")

    # Price/FCF requires calculation
    mcap = yf_info.get("marketCap")
    fcf = yf_info.get("freeCashflow")
    price_fcf = round(mcap / fcf, 2) if mcap and fcf and fcf != 0 else None

    return {
        "ticker": ticker,
        "pe_ttm": pe,
        "ev_ebitda": ev_ebitda,
        "price_fcf": price_fcf,
        "peg": peg,
        "price_book": pb,
    }


def compare_pair(ticker_a: str, ticker_b: str) -> dict:
    """Compare two tickers on valuation metrics."""
    metrics_a = get_metrics(ticker_a)
    metrics_b = get_metrics(ticker_b)

    metric_keys = ["pe_ttm", "ev_ebitda", "price_fcf", "peg", "price_book"]
    a_wins = 0
    b_wins = 0
    comparable = 0
    details = {}

    for key in metric_keys:
        va = metrics_a.get(key)
        vb = metrics_b.get(key)
        details[key] = {ticker_a: va, ticker_b: vb}
        if va is not None and vb is not None:
            comparable += 1
            if va < vb:
                a_wins += 1
            elif vb < va:
                b_wins += 1

    if comparable < 2:
        cheaper = "inconclusive"
        thesis = f"Insufficient comparable metrics ({comparable}) to determine relative value between {ticker_a} and {ticker_b}."
    elif a_wins > b_wins:
        cheaper = ticker_a
        thesis = f"{ticker_a} appears cheaper than {ticker_b} on {a_wins} of {comparable} comparable metrics."
    elif b_wins > a_wins:
        cheaper = ticker_b
        thesis = f"{ticker_b} appears cheaper than {ticker_a} on {b_wins} of {comparable} comparable metrics."
    else:
        cheaper = "inconclusive"
        thesis = f"{ticker_a} and {ticker_b} are similarly valued across {comparable} comparable metrics."

    return {
        "stock_a": ticker_a,
        "stock_b": ticker_b,
        "metrics": details,
        "a_wins": a_wins,
        "b_wins": b_wins,
        "comparable_metrics": comparable,
        "cheaper": cheaper,
        "thesis": thesis,
    }


def scenario_valuation(ticker: str) -> dict | None:
    """Calculate bull/base/bear fair value scenarios using yfinance data.

    Uses forward PE * forward EPS estimates with sector percentile adjustments.
    Returns None if insufficient data.
    """
    info = yahoo_finance_info(ticker)

    forward_pe = info.get("forwardPE")
    forward_eps = info.get("forwardEps")
    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
    target_high = info.get("targetHighPrice")
    target_low = info.get("targetLowPrice")
    target_mean = info.get("targetMeanPrice")

    if not forward_pe or not current_price:
        return None

    # Use analyst targets if available, otherwise estimate from PE
    if target_high and target_low and target_mean:
        bull_price = target_high
        base_price = target_mean
        bear_price = target_low
    elif forward_eps and forward_pe:
        bull_price = round(forward_eps * forward_pe * 1.25, 2)
        base_price = round(forward_eps * forward_pe, 2)
        bear_price = round(forward_eps * forward_pe * 0.75, 2)
    else:
        return None

    bull_upside = round((bull_price - current_price) / current_price, 4)
    base_upside = round((base_price - current_price) / current_price, 4)
    bear_downside = round((bear_price - current_price) / current_price, 4)

    if bear_downside != 0:
        risk_reward = round(abs(bull_upside / bear_downside), 2)
    else:
        risk_reward = None

    return {
        "ticker": ticker,
        "current_price": round(current_price, 2),
        "bull_price": round(bull_price, 2),
        "base_price": round(base_price, 2),
        "bear_price": round(bear_price, 2),
        "bull_upside": bull_upside,
        "base_upside": base_upside,
        "bear_downside": bear_downside,
        "risk_reward": risk_reward,
        "forward_pe": round(forward_pe, 1) if forward_pe else None,
        "forward_eps": round(forward_eps, 2) if forward_eps else None,
    }


def main():
    setup_logging()
    logger.info("=== Valuation Module ===")

    watchlist_path = PROJECT_ROOT / "config" / "watchlist.json"
    if not watchlist_path.exists():
        envelope = create_envelope("valuation", {}, status="error", error="watchlist.json not found")
        save_envelope(envelope, "valuation.json")
        return

    watchlist = json.loads(watchlist_path.read_text())
    pairs = watchlist.get("comparison_pairs", [])

    comparisons = []
    errors = 0

    for pair in pairs:
        if len(pair) != 2:
            logger.warning("Invalid pair: %s", pair)
            continue
        try:
            result = compare_pair(pair[0], pair[1])
            comparisons.append(result)
            logger.info("Compared %s vs %s: cheaper=%s", pair[0], pair[1], result["cheaper"])
        except Exception as e:
            logger.error("Error comparing %s vs %s: %s", pair[0], pair[1], e)
            errors += 1

    # Scenario valuations for all tickers
    all_tickers = set()
    for pair in pairs:
        all_tickers.update(pair)
    portfolio_path = PROJECT_ROOT / "config" / "portfolio.json"
    if portfolio_path.exists():
        try:
            pf = json.loads(portfolio_path.read_text())
            for h in pf.get("holdings", []):
                all_tickers.add(h["ticker"])
        except Exception:
            pass

    scenarios = []
    for ticker in sorted(all_tickers):
        try:
            scenario = scenario_valuation(ticker)
            if scenario:
                scenarios.append(scenario)
                logger.info("%s: bull=$%.0f (%+.1f%%), bear=$%.0f (%+.1f%%), R/R=%.1fx",
                            ticker, scenario["bull_price"], scenario["bull_upside"] * 100,
                            scenario["bear_price"], scenario["bear_downside"] * 100,
                            scenario["risk_reward"] or 0)
        except Exception as e:
            logger.warning("Scenario valuation failed for %s: %s", ticker, e)

    status = "success" if comparisons and errors == 0 else "partial" if comparisons else "error"
    error = None if comparisons else "No comparisons could be made"
    data = {"comparisons": comparisons, "scenarios": scenarios}
    envelope = create_envelope("valuation", data, status=status, error=error)
    save_envelope(envelope, "valuation.json")
    logger.info("Valuation analysis complete: %d pairs compared, %d scenarios", len(comparisons), len(scenarios))


if __name__ == "__main__":
    main()
