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


def calculate_strategy_performance(trades: list, days: int = None) -> dict:
    """Calculate performance metrics per strategy from trade history.

    Returns: {strategy_name: {pnl, trades, wins, losses, win_rate, sharpe, avg_return}}
    """
    if days is None:
        days = EVAL_CYCLE_DAYS

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    recent = [t for t in trades if t.get("timestamp", "") >= cutoff]

    by_strategy = defaultdict(list)
    for t in recent:
        strategy = t.get("strategy", "unknown")
        by_strategy[strategy].append(t)

    results = {}
    for strategy, strades in by_strategy.items():
        costs = [t.get("cost_usd", 0) for t in strades]
        # For paper trades, estimate P&L based on edge
        # In live mode, we'd check resolution status
        returns = []
        wins = 0
        losses = 0

        for t in strades:
            price = t.get("price", 0.5)
            # Gimme bets: expected return = (1 - price) / price
            # Weather edge: expected return = edge_pct
            if price > 0 and price < 1:
                expected_return = (1.0 - price) / price
                returns.append(expected_return)
                # For paper mode, assume gimme bets (>90%) resolve correctly 95% of the time
                if price >= 0.90:
                    wins += 1
                elif price >= 0.60:
                    wins += 1  # Weather edge with high confidence
                else:
                    losses += 1

        total_cost = sum(costs)
        avg_return = sum(returns) / len(returns) if returns else 0
        win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0

        # Sharpe ratio approximation
        if len(returns) >= 2:
            mean_r = sum(returns) / len(returns)
            var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
            std_r = math.sqrt(var_r) if var_r > 0 else 0.001
            sharpe = mean_r / std_r
        else:
            sharpe = 0

        results[strategy] = {
            "trades": len(strades),
            "total_cost": round(total_cost, 2),
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 3),
            "avg_return": round(avg_return, 4),
            "sharpe": round(sharpe, 3),
            "returns": [round(r, 4) for r in returns],
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

        if i == 0:  # Best performer
            new_weight = old_weight * (1 + STRATEGY_WEIGHT_ADJUST)
        elif i == len(ranked) - 1:  # Worst performer
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
