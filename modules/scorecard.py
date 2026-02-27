"""Verdict Scorecard: evaluates past BUY/SELL/HOLD/AVOID verdicts against actual returns."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime, timedelta

import pandas as pd

from lib.data_envelope import create_envelope, save_envelope
from lib.api import yahoo_finance_price_history

logger = logging.getLogger(__name__)

SCORECARD_DIR = PROJECT_ROOT / "data" / "scorecard"
EVALUATION_WINDOWS = [5, 10, 30]


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


def load_verdict_log(scorecard_dir: Path = None) -> list:
    """Load verdict_log.json entries. Returns [] if file missing."""
    sdir = scorecard_dir or SCORECARD_DIR
    log_path = sdir / "verdict_log.json"
    if not log_path.exists():
        return []
    try:
        data = json.loads(log_path.read_text())
        return data.get("entries", [])
    except (json.JSONDecodeError, KeyError):
        logger.warning("Could not parse verdict_log.json")
        return []


def get_price_on_date(history_df: pd.DataFrame, target_date: str) -> float | None:
    """Find close price on or after target_date. Returns None if out of range."""
    if history_df.empty:
        return None
    target = pd.Timestamp(target_date)
    # Find rows on or after the target date (handles weekends/holidays)
    mask = history_df.index >= target
    if not mask.any():
        return None
    return float(history_df.loc[mask, "Close"].iloc[0])


def evaluate_verdicts(entries: list, today_str: str) -> dict:
    """Evaluate past verdicts at 5/10/30-day windows against SPY benchmark.

    Skips entries dated today (not enough time to evaluate).
    Returns summary with win/loss counts and per-entry details.
    """
    today = datetime.strptime(today_str, "%Y-%m-%d").date()

    # Validate and filter entries
    valid_entries = []
    for e in entries:
        if not all(k in e for k in ("date", "ticker", "verdict")):
            logger.warning("Skipping entry with missing fields: %s", e)
            continue
        try:
            datetime.strptime(e["date"], "%Y-%m-%d")
        except (ValueError, TypeError):
            logger.warning("Skipping entry with invalid date: %s", e.get("date"))
            continue
        valid_entries.append(e)

    # Filter out today's entries
    past_entries = [e for e in valid_entries if e.get("date") != today_str]
    if not past_entries:
        return {
            "evaluated_verdicts": 0,
            "evaluation_windows": EVALUATION_WINDOWS,
            "summary": {
                "total_scored": 0, "total_wins": 0, "total_losses": 0,
                "win_rate": 0.0, "by_verdict": {},
            },
            "details": [],
        }

    # Collect unique tickers + SPY for batch fetch
    tickers = list({e["ticker"] for e in past_entries})
    all_tickers = tickers + (["SPY"] if "SPY" not in tickers else [])

    # Batch fetch 3-month histories
    histories = {}
    for ticker in all_tickers:
        histories[ticker] = yahoo_finance_price_history(ticker, period="3mo")

    # Evaluate each entry at each window
    details = []
    total_wins = 0
    total_losses = 0
    by_verdict = {}

    for entry in past_entries:
        ticker = entry["ticker"]
        verdict = entry["verdict"]
        entry_date = entry["date"]
        entry_dt = datetime.strptime(entry_date, "%Y-%m-%d").date()

        windows = {}
        for window in EVALUATION_WINDOWS:
            eval_date = entry_dt + timedelta(days=window)
            if eval_date > today:
                windows[window] = {"status": "pending"}
                continue

            eval_date_str = eval_date.strftime("%Y-%m-%d")
            ticker_price = get_price_on_date(histories.get(ticker, pd.DataFrame()), eval_date_str)
            spy_price = get_price_on_date(histories.get("SPY", pd.DataFrame()), eval_date_str)
            verdict_price = entry.get("price_at_verdict")
            spy_verdict_price = entry.get("spy_price_at_verdict")

            if not all([ticker_price, spy_price, verdict_price, spy_verdict_price]):
                windows[window] = {"status": "no_data"}
                continue

            ticker_return = (ticker_price - verdict_price) / verdict_price
            spy_return = (spy_price - spy_verdict_price) / spy_verdict_price

            # Win/loss logic relative to SPY
            if verdict == "REVIEW":
                windows[window] = {
                    "status": "unscored",
                    "ticker_return": round(ticker_return, 4),
                    "spy_return": round(spy_return, 4),
                }
                continue

            if verdict in ("BUY", "HOLD"):
                win = ticker_return >= spy_return
            elif verdict in ("SELL", "AVOID"):
                win = ticker_return < spy_return
            else:
                win = False

            windows[window] = {
                "status": "win" if win else "loss",
                "ticker_return": round(ticker_return, 4),
                "spy_return": round(spy_return, 4),
            }

        # Aggregate scored windows for this entry
        scored_windows = [w for w in windows.values() if w["status"] in ("win", "loss")]
        entry_wins = sum(1 for w in scored_windows if w["status"] == "win")
        entry_losses = sum(1 for w in scored_windows if w["status"] == "loss")
        total_wins += entry_wins
        total_losses += entry_losses

        # Track by verdict type
        if verdict not in by_verdict:
            by_verdict[verdict] = {"scored": 0, "wins": 0, "losses": 0}
            if verdict == "REVIEW":
                by_verdict[verdict] = {"tracked": 0, "scored": 0}
        if verdict == "REVIEW":
            by_verdict[verdict]["tracked"] = by_verdict[verdict].get("tracked", 0) + 1
        else:
            by_verdict[verdict]["scored"] += len(scored_windows)
            by_verdict[verdict]["wins"] += entry_wins
            by_verdict[verdict]["losses"] += entry_losses

        details.append({
            "date": entry_date,
            "ticker": ticker,
            "verdict": verdict,
            "windows": windows,
        })

    # Compute win rates
    total_scored = total_wins + total_losses
    for v_data in by_verdict.values():
        if v_data.get("scored", 0) > 0:
            v_data["win_rate"] = round(v_data["wins"] / v_data["scored"], 3)

    return {
        "evaluated_verdicts": len(past_entries),
        "evaluation_windows": EVALUATION_WINDOWS,
        "summary": {
            "total_scored": total_scored,
            "total_wins": total_wins,
            "total_losses": total_losses,
            "win_rate": round(total_wins / total_scored, 3) if total_scored > 0 else 0.0,
            "by_verdict": by_verdict,
        },
        "details": details,
    }


def main():
    setup_logging()
    logger.info("=== Scorecard Module ===")

    entries = load_verdict_log()
    if not entries:
        logger.info("No verdict log found — writing empty scorecard")
        envelope = create_envelope("scorecard", {
            "evaluated_verdicts": 0,
            "evaluation_windows": EVALUATION_WINDOWS,
            "summary": {
                "total_scored": 0, "total_wins": 0, "total_losses": 0,
                "win_rate": 0.0, "by_verdict": {},
            },
            "details": [],
        }, status="partial")
        save_envelope(envelope, "scorecard.json")
        return

    from modules.morning_brief import get_eastern_date
    today_str = get_eastern_date()

    result = evaluate_verdicts(entries, today_str)
    status = "success" if result["summary"]["total_scored"] > 0 else "partial"
    envelope = create_envelope("scorecard", result, status=status)
    save_envelope(envelope, "scorecard.json")
    logger.info("Scorecard: %d verdicts evaluated, %.0f%% win rate",
                result["evaluated_verdicts"],
                result["summary"]["win_rate"] * 100)


if __name__ == "__main__":
    main()
