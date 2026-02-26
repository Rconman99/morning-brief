"""Trade journal analysis: win rates, revenge trading detection, day-of-week patterns."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import csv
import logging
from datetime import datetime, timedelta
from collections import defaultdict

from lib.data_envelope import create_envelope, save_envelope

logger = logging.getLogger(__name__)


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


def load_trades() -> list[dict]:
    """Load trades from raw or sample CSV."""
    raw_path = PROJECT_ROOT / "data" / "raw" / "trades.csv"
    sample_path = PROJECT_ROOT / "data" / "sample" / "trades.csv"
    csv_path = raw_path if raw_path.exists() else sample_path

    if not csv_path.exists():
        logger.error("No trades CSV found")
        return []

    logger.info("Loading trades from %s", csv_path)
    trades = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            date_str = row.get("date", "").strip()
            ticker = row.get("ticker", "").strip()
            entry = row.get("entry_price", "").strip()
            exit_ = row.get("exit_price", "").strip()

            if not all([date_str, ticker, entry, exit_]):
                logger.warning("Row %d: missing critical field, skipping", i)
                continue

            try:
                trade_date = datetime.strptime(date_str, "%Y-%m-%d")
                entry_price = float(entry)
                exit_price = float(exit_)
            except ValueError as e:
                logger.warning("Row %d: parse error %s, skipping", i, e)
                continue

            pnl_str = row.get("pnl", "").strip()
            if pnl_str:
                try:
                    pnl = float(pnl_str)
                except ValueError:
                    pnl = exit_price - entry_price
            else:
                pnl = exit_price - entry_price

            hold_str = row.get("hold_time_hours", "").strip()
            hold_time = float(hold_str) if hold_str else "unknown"

            trades.append({
                "date": trade_date,
                "day_of_week": trade_date.strftime("%A"),
                "ticker": ticker,
                "direction": row.get("direction", "").strip(),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl": pnl,
                "hold_time_hours": hold_time,
                "rationale": row.get("rationale", "").strip(),
            })

    return trades


def analyze_trades(trades: list[dict]) -> dict:
    """Run all journal analyses."""
    if not trades:
        return {"win_rate": None, "total_trades": 0, "patterns": [], "checklist": []}

    total = len(trades)
    winners = sum(1 for t in trades if t["pnl"] > 0)
    win_rate = round(winners / total, 4)

    # Win rate by day of week
    day_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})
    for t in trades:
        day = t["day_of_week"]
        day_stats[day]["total"] += 1
        if t["pnl"] > 0:
            day_stats[day]["wins"] += 1
        else:
            day_stats[day]["losses"] += 1

    day_win_rates = {}
    for day, stats in day_stats.items():
        day_win_rates[day] = round(stats["wins"] / stats["total"], 4) if stats["total"] > 0 else 0

    # Revenge trading: 3+ consecutive losses on same ticker within 48hrs each
    trades_sorted = sorted(trades, key=lambda t: t["date"])
    ticker_losses = defaultdict(list)
    for t in trades_sorted:
        if t["pnl"] < 0:
            ticker_losses[t["ticker"]].append(t["date"])

    revenge_patterns = []
    for ticker, loss_dates in ticker_losses.items():
        loss_dates.sort()
        streak = [loss_dates[0]]
        for i in range(1, len(loss_dates)):
            if (loss_dates[i] - loss_dates[i - 1]) <= timedelta(hours=48):
                streak.append(loss_dates[i])
            else:
                if len(streak) >= 3:
                    revenge_patterns.append({
                        "type": "revenge_trading",
                        "ticker": ticker,
                        "count": len(streak),
                        "dates": [d.strftime("%Y-%m-%d") for d in streak],
                        "detail": f"{ticker}: {len(streak)} consecutive losses within 48hrs",
                    })
                streak = [loss_dates[i]]
        if len(streak) >= 3:
            revenge_patterns.append({
                "type": "revenge_trading",
                "ticker": ticker,
                "count": len(streak),
                "dates": [d.strftime("%Y-%m-%d") for d in streak],
                "detail": f"{ticker}: {len(streak)} consecutive losses within 48hrs",
            })

    # Day-of-week clustering: win_rate < 30% with >= 3 trades
    day_flags = []
    for day, stats in day_stats.items():
        if stats["total"] >= 3:
            wr = day_win_rates[day]
            if wr < 0.30:
                day_flags.append({
                    "type": "day_of_week",
                    "day": day,
                    "win_rate": wr,
                    "total_trades": stats["total"],
                    "detail": f"{day} win rate {wr:.0%} ({stats['total']} trades)",
                })

    patterns = revenge_patterns + day_flags

    # Generate checklist rules
    checklist = []
    for p in revenge_patterns:
        checklist.append(f"RULE: After 2 consecutive losses on {p['ticker']}, stop trading that ticker for 24hrs")
    for d in day_flags:
        checklist.append(f"RULE: Avoid trading on {d['day']}s (historically poor performance)")

    return {
        "win_rate": win_rate,
        "total_trades": total,
        "winners": winners,
        "losers": total - winners,
        "day_of_week_win_rates": day_win_rates,
        "patterns": patterns,
        "checklist": checklist,
    }


def main():
    setup_logging()
    logger.info("=== Journal Module ===")

    trades = load_trades()
    result = analyze_trades(trades)

    status = "success" if trades else "error"
    error = None if trades else "No trades data found"
    envelope = create_envelope("journal", result, status=status, error=error)
    save_envelope(envelope, "journal.json")
    logger.info("Journal analysis complete: %d trades, %.1f%% win rate",
                result["total_trades"], (result["win_rate"] or 0) * 100)


if __name__ == "__main__":
    main()
