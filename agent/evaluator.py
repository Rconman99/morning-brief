"""Darwinian evaluator — tracks performance and evolves strategy params.

Every EVAL_CYCLE_DAYS (default 5), evaluates each strategy's rolling Sharpe ratio.
Winners get more capital weight. Losers get less. The worst performer gets its
parameters rewritten. Changes are committed to git — reverted if performance drops.

This is the "or you will be replaced" mechanic.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
import math
from datetime import datetime, timedelta
from collections import defaultdict

from agent.config import (
    STRATEGY_WEIGHT_MIN, STRATEGY_WEIGHT_MAX, STRATEGY_WEIGHT_ADJUST,
    EVAL_CYCLE_DAYS, load_agent_config, save_strategy_params,
)
from agent.executor import get_paper_trades

logger = logging.getLogger(__name__)

EVAL_LOG = PROJECT_ROOT / "agent" / "eval_history.jsonl"
PERF_LOG = PROJECT_ROOT / "agent" / "performance.json"


MIN_RESOLVED_TO_RANK = 10   # settled trades a strategy needs before its weight can move


def calculate_strategy_performance(trades: list, days: int = None) -> dict:
    """Per-strategy performance from REAL settlements (agent/ledger.py).

    The previous version assumed every paper trade priced >=90¢ won, which is
    what produced the inflated "7-0" record. Now a trade only counts once its
    market has settled; strategies with fewer than MIN_RESOLVED_TO_RANK settled
    trades are reported but not ranked (their weight doesn't move).

    `trades` and `days` are kept for signature compatibility; the ledger reads
    the trade log itself and scores everything that has settled.
    """
    try:
        from agent.ledger import build
        _, summary = build(refresh_settlements=True)
    except Exception as e:
        logger.warning("Ledger unavailable (%s) — skipping evaluation", e)
        return {}

    results = {}
    for strategy, v in summary.items():
        if v["resolved"] < MIN_RESOLVED_TO_RANK:
            logger.info("  %s: %d settled trades (<%d) — not ranked yet",
                        strategy, v["resolved"], MIN_RESOLVED_TO_RANK)
            continue
        results[strategy] = {
            "trades": v["resolved"],
            "total_cost": v["deployed"],
            "wins": v["wins"],
            "losses": v["losses"],
            "win_rate": v["win_rate"],
            # Rank and reward on the conservative (taker-bound) ROI: paper maker
            # fills are assumed, so the optimistic number must not move capital.
            "avg_return": v["roi_taker_bound"],
            "sharpe": v["sharpe"] if v["roi_taker_bound"] > 0 else min(v["sharpe"], 0.0),
            "pnl": v["pnl"],
            "pnl_taker_bound": v["pnl_taker_bound"],
        }
    return results


def adjust_weights(params: dict, performance: dict) -> dict:
    """Adjust strategy weights based on relative Sharpe ratios.

    Top quartile: weight * (1 + ADJUST)
    Bottom: weight * (1 - ADJUST)
    Clamped to [MIN, MAX]
    """
    if not performance:
        return params

    # Rank strategies by Sharpe
    ranked = sorted(performance.items(), key=lambda x: x[1].get("sharpe", 0), reverse=True)

    for i, (strategy, perf) in enumerate(ranked):
        if strategy not in params:
            continue

        old_weight = params[strategy].get("weight", 1.0)

        roi = perf.get("avg_return", 0)
        if roi < 0:  # Losing money after fees: always shrink, whatever the rank
            new_weight = old_weight * (1 - STRATEGY_WEIGHT_ADJUST)
        elif i == 0 and roi > 0:  # Best performer and actually profitable
            new_weight = old_weight * (1 + STRATEGY_WEIGHT_ADJUST)
        elif i == len(ranked) - 1 and len(ranked) > 1:  # Worst of several
            new_weight = old_weight * (1 - STRATEGY_WEIGHT_ADJUST)
        else:
            new_weight = old_weight  # Middle stays

        new_weight = max(STRATEGY_WEIGHT_MIN, min(STRATEGY_WEIGHT_MAX, new_weight))
        params[strategy]["weight"] = round(new_weight, 3)

        if new_weight != old_weight:
            logger.info("Weight %s: %.3f → %.3f (Sharpe: %.3f, %d trades)",
                        strategy, old_weight, new_weight, perf.get("sharpe", 0), perf.get("trades", 0))

    return params


def should_evaluate() -> bool:
    """Check if enough time has passed since last evaluation."""
    if not EVAL_LOG.exists():
        return True

    lines = EVAL_LOG.read_text().strip().split("\n")
    if not lines or not lines[-1]:
        return True

    try:
        last = json.loads(lines[-1])
        last_ts = last.get("timestamp", "")
        last_dt = datetime.fromisoformat(last_ts)
        days_since = (datetime.now().astimezone() - last_dt).days
        return days_since >= EVAL_CYCLE_DAYS
    except (json.JSONDecodeError, ValueError):
        return True


def run_evaluation() -> dict:
    """Run the full Darwinian evaluation cycle."""
    logger.info("=== Darwinian Evaluation ===")

    trades = get_paper_trades()
    if len(trades) < 5:
        logger.info("Only %d trades — need at least 5 for evaluation", len(trades))
        return {"status": "insufficient_data", "trades": len(trades)}

    # Calculate performance
    performance = calculate_strategy_performance(trades)
    logger.info("Performance by strategy:")
    for s, p in performance.items():
        logger.info("  %s: Sharpe=%.3f, Win=%.1f%%, Trades=%d, AvgReturn=%.2f%%",
                     s, p["sharpe"], p["win_rate"] * 100, p["trades"], p["avg_return"] * 100)

    # Adjust weights
    params = load_agent_config()
    params = adjust_weights(params, performance)
    save_strategy_params(params)

    # Find worst performer for potential rewrite
    worst = min(performance.items(), key=lambda x: x[1].get("sharpe", 0)) if performance else None
    rewrite_candidate = None
    if worst and worst[1].get("sharpe", 0) < 0:
        rewrite_candidate = worst[0]
        logger.warning("WORST PERFORMER: %s (Sharpe %.3f) — candidate for parameter rewrite",
                        worst[0], worst[1]["sharpe"])

    # Log evaluation
    eval_record = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "total_trades": len(trades),
        "performance": performance,
        "weight_adjustments": {s: params[s].get("weight", 1.0) for s in params},
        "rewrite_candidate": rewrite_candidate,
    }
    with open(EVAL_LOG, "a") as f:
        f.write(json.dumps(eval_record) + "\n")

    # Save overall performance snapshot
    PERF_LOG.write_text(json.dumps({
        "last_eval": eval_record["timestamp"],
        "strategies": performance,
        "weights": eval_record["weight_adjustments"],
        "total_trades": len(trades),
    }, indent=2))

    return eval_record


def get_performance_summary() -> dict:
    """Get the latest performance snapshot."""
    if PERF_LOG.exists():
        try:
            return json.loads(PERF_LOG.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    result = run_evaluation()
    print(json.dumps(result, indent=2))
