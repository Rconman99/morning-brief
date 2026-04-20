"""Walk-forward backtest runner for equity strategies.

Research basis (April 2026): straight train/test splits overfit. Walk-forward
(rolling train→test windows) + aggregated out-of-sample metrics is what
Zorro Project, de Prado, and surviving retail algo traders use.

Gate logic:
  - Run the strategy across N rolling test windows (default 6 windows of 90 days each).
  - Compute test-window Sharpe, Calmar, max DD.
  - Passing = median Calmar ≥ 0.5 AND median Sharpe ≥ 0.8.
  - Store pass/fail in strategy_status table.

Usage:
    .venv/bin/python agent/walk_forward.py --strategy technical_reversion
    .venv/bin/python agent/walk_forward.py --strategy all --windows 6
"""
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import logging
import statistics
from datetime import datetime, timedelta

from agent.backtester import backtest, technical_reversion
from agent.equity_db import set_strategy_backtest, init_db

logger = logging.getLogger(__name__)

DEFAULT_TICKERS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "AMZN"]
WINDOWS_DEFAULT = 6
TEST_DAYS = 90            # each test window covers ~3 months
TRAIN_DAYS = 365          # ~1 year of train data before each test window
CALMAR_PASS = 0.5
SHARPE_PASS = 0.8


# Map of strategy names → backtester strategy callables.
# The backtester's callable signature: (signals_df, ticker, day_idx) -> 'BUY'/'SELL'/None
STRATEGY_FNS = {
    "technical_reversion": technical_reversion,
    # Other strategies (congressional, sector_rotation, overnight_drift) don't
    # yet have a corresponding callable in backtester.py. Left as TODO — they
    # require historical filings / macro signal data replayed chronologically.
}


def compute_calmar(sharpe: float, max_dd: float, total_return: float, days: int) -> float:
    """Calmar = annualized return / max drawdown. Robust to tiny drawdowns."""
    if days <= 0:
        return 0.0
    annual_return = (1 + total_return) ** (365 / days) - 1 if total_return > -1 else -1
    if max_dd <= 0:
        return 999.0 if annual_return > 0 else 0.0
    return round(annual_return / max_dd, 2)


def run_walk_forward(strategy_name: str, windows: int = WINDOWS_DEFAULT,
                      tickers: list[str] | None = None) -> dict:
    """Run walk-forward backtest across rolling test windows.

    Returns: {'sharpe_median': float, 'calmar_median': float, 'max_dd': float,
              'windows': [{'start': ..., 'end': ..., 'sharpe': ..., ...}],
              'passed': bool}
    """
    strategy_fn = STRATEGY_FNS.get(strategy_name)
    if not strategy_fn:
        logger.warning("No backtester callable registered for '%s'", strategy_name)
        return {"passed": False, "error": f"No backtest callable for {strategy_name}"}

    tickers = tickers or DEFAULT_TICKERS
    today = datetime.now()
    results = []

    for i in range(windows):
        # Walk from most-recent to oldest window, step 90 days each
        test_end = today - timedelta(days=i * TEST_DAYS)
        test_start = test_end - timedelta(days=TEST_DAYS)

        try:
            r = backtest(
                strategy_fn=strategy_fn,
                tickers=tickers,
                start_date=test_start.strftime("%Y-%m-%d"),
                end_date=test_end.strftime("%Y-%m-%d"),
            )
        except Exception as e:
            logger.warning("Window %s-%s failed: %s", test_start.date(), test_end.date(), e)
            continue

        results.append({
            "start": test_start.strftime("%Y-%m-%d"),
            "end": test_end.strftime("%Y-%m-%d"),
            "sharpe": r.sharpe,
            "max_dd": r.max_drawdown,
            "return": r.total_return,
            "trades": r.total_trades,
            "win_rate": r.win_rate,
            "calmar": compute_calmar(r.sharpe, r.max_drawdown, r.total_return, TEST_DAYS),
        })

    if not results:
        return {"passed": False, "error": "No windows succeeded", "windows": []}

    sharpes = [w["sharpe"] for w in results]
    calmars = [w["calmar"] for w in results]
    max_dds = [w["max_dd"] for w in results]

    sharpe_median = statistics.median(sharpes)
    calmar_median = statistics.median(calmars)
    max_dd_overall = max(max_dds)

    passed = calmar_median >= CALMAR_PASS and sharpe_median >= SHARPE_PASS

    return {
        "strategy": strategy_name,
        "windows": results,
        "sharpe_median": round(sharpe_median, 2),
        "calmar_median": round(calmar_median, 2),
        "max_dd": round(max_dd_overall, 4),
        "passed": passed,
        "gate": f"Calmar >= {CALMAR_PASS} AND Sharpe >= {SHARPE_PASS}",
    }


def print_report(result: dict) -> None:
    if result.get("error"):
        print(f"ERROR: {result['error']}")
        return
    print(f"\n{'=' * 70}")
    print(f"  WALK-FORWARD BACKTEST — {result['strategy']}")
    print(f"{'=' * 70}")
    print(f"  Gate: {result['gate']}")
    print(f"  Result: {'✓ PASSED' if result['passed'] else '✗ FAILED'}")
    print(f"  Median Sharpe: {result['sharpe_median']}")
    print(f"  Median Calmar: {result['calmar_median']}")
    print(f"  Worst max DD:  {result['max_dd']:.2%}")
    print()
    print(f"  Per-window results:")
    print(f"  {'Period':<25} {'Sharpe':>8} {'Calmar':>8} {'MaxDD':>8} {'Return':>9} {'Trades':>7}")
    for w in result["windows"]:
        print(f"  {w['start']} to {w['end'][:10]:<10} "
              f"{w['sharpe']:>8.2f} {w['calmar']:>8.2f} "
              f"{w['max_dd']:>7.1%} {w['return']:>+8.1%} {w['trades']:>7}")
    print(f"{'=' * 70}\n")


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    init_db()

    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="all",
                    help="Strategy name or 'all' (default: all)")
    ap.add_argument("--windows", type=int, default=WINDOWS_DEFAULT,
                    help=f"Number of test windows (default: {WINDOWS_DEFAULT})")
    args = ap.parse_args()

    strategies_to_run = (list(STRATEGY_FNS.keys())
                         if args.strategy == "all" else [args.strategy])

    for s in strategies_to_run:
        result = run_walk_forward(s, windows=args.windows)
        print_report(result)
        if result.get("passed") is not None and "error" not in result:
            set_strategy_backtest(
                strategy=s,
                sharpe=result["sharpe_median"],
                calmar=result["calmar_median"],
                max_dd=result["max_dd"],
                passed=result["passed"],
            )


if __name__ == "__main__":
    main()
