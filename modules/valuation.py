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

    status = "success" if comparisons and errors == 0 else "partial" if comparisons else "error"
    error = None if comparisons else "No comparisons could be made"
    envelope = create_envelope("valuation", {"comparisons": comparisons}, status=status, error=error)
    save_envelope(envelope, "valuation.json")
    logger.info("Valuation analysis complete: %d pairs compared", len(comparisons))


if __name__ == "__main__":
    main()
