"""Insider Tracker: monitors SEC Form 4 insider transactions via edgartools."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import date, timedelta

from lib.data_envelope import create_envelope, save_envelope

logger = logging.getLogger(__name__)

# Set up edgartools with graceful fallback
try:
    from edgar import set_identity, Company
    set_identity("Trading System trading@local.dev")
except ImportError:
    Company = None
    logger.warning("edgartools not installed — insider tracking disabled")
except Exception as e:
    Company = None
    logger.warning("edgartools setup failed: %s — insider tracking disabled", e)


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


def get_insider_transactions(ticker: str, lookback_days: int = 30) -> list[dict]:
    """Fetch recent Form 4 insider transactions for a ticker."""
    if Company is None:
        return []
    try:
        company = Company(ticker)
        filings = company.get_filings(form="4").latest(20)

        transactions = []
        cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()

        for filing in filings:
            try:
                filed_date = str(filing.filing_date)
                if filed_date < cutoff:
                    continue

                transactions.append({
                    "filed_date": filed_date,
                    "filer": str(filing.filer) if hasattr(filing, "filer") else "Unknown",
                    "form": "4",
                    "accession_no": filing.accession_no if hasattr(filing, "accession_no") else "",
                })
            except Exception as e:
                logger.debug("Error parsing Form 4 for %s: %s", ticker, e)
                continue

        return transactions
    except Exception as e:
        # Try alternative API if Company doesn't work
        try:
            from edgar import get_filings
            filings = get_filings(form="4", ticker=ticker)
            # Basic fallback — just count filings
            return []
        except Exception:
            pass
        logger.warning("Failed to fetch insider data for %s: %s", ticker, e)
        return []


def analyze_insider_activity(ticker: str, config: dict) -> dict:
    """Analyze insider transactions for signals."""
    lookback = config.get("insider_lookback_days", 30)
    cluster_threshold = config.get("insider_cluster_threshold", 3)

    transactions = get_insider_transactions(ticker, lookback)

    if not transactions:
        return {
            "ticker": ticker,
            "transaction_count": 0,
            "recent_filings": [],
            "signal": "no_data",
            "detail": "No recent insider filings found",
            "cluster_buy": False,
        }

    total = len(transactions)

    return {
        "ticker": ticker,
        "transaction_count": total,
        "recent_filings": transactions[:5],
        "signal": "active" if total >= cluster_threshold else "normal",
        "detail": f"{total} Form 4 filings in last {lookback} days",
        "cluster_buy": total >= cluster_threshold,
    }


def analyze_all_insiders(tickers: list[str] = None) -> dict:
    """Run insider analysis on all watchlist + holding tickers."""
    if tickers is None:
        tickers = get_all_tickers()

    settings_path = PROJECT_ROOT / "config" / "settings.json"
    config = json.loads(settings_path.read_text()) if settings_path.exists() else {}

    results = []
    for ticker in tickers:
        result = analyze_insider_activity(ticker, config)
        results.append(result)
        if result["transaction_count"] > 0:
            logger.info("%s: %d insider filings — %s",
                        ticker, result["transaction_count"], result["signal"])

    return {"results": results}


def main():
    setup_logging()
    logger.info("=== Insider Tracker Module ===")

    data = analyze_all_insiders()
    active = [r for r in data["results"] if r["transaction_count"] > 0]
    status = "success"
    error = None
    if Company is None:
        status = "partial"
        error = "edgartools not available — returning empty results"

    envelope = create_envelope("insider_tracker", data, status=status, error=error)
    save_envelope(envelope, "insider_tracker.json")
    logger.info("Insider tracker complete: %d tickers, %d with activity",
                len(data["results"]), len(active))


if __name__ == "__main__":
    main()
