"""Polymarket Trading Agent — main runner.

Orchestrates: signals → strategies → risk gate → execution → logging.

Usage:
    # Paper trading (default — no private key needed)
    .venv/bin/python agent/run.py

    # With custom bankroll
    .venv/bin/python agent/run.py --bankroll 5000

    # Dry run — show proposals without executing
    .venv/bin/python agent/run.py --dry-run

    # Live trading (requires POLYMARKET_PRIVATE_KEY in .env)
    POLYMARKET_PRIVATE_KEY=0x... .venv/bin/python agent/run.py
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import logging
import os
from datetime import datetime

from agent.config import load_agent_config
from agent.strategies import run_all_strategies
from agent.risk_gate import filter_proposals
from agent.executor import (
    place_limit_order, get_balance, get_mode,
    init_paper_bankroll, get_paper_trades,
)
from agent.evaluator import should_evaluate, run_evaluation

logger = logging.getLogger(__name__)


def setup_logging():
    log_dir = PROJECT_ROOT / "agent"
    log_dir.mkdir(parents=True, exist_ok=True)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_dir / "agent.log", mode="a"),
            ],
        )


def print_summary(proposals: list, approved: list, executed: list, bankroll: float, mode: str):
    """Print a human-readable summary of the agent run."""
    now = datetime.now().astimezone()
    print(f"\n{'='*60}")
    print(f"  POLYMARKET AGENT — {now.strftime('%Y-%m-%d %H:%M')} [{mode.upper()}]")
    print(f"{'='*60}")
    print(f"  Bankroll: ${bankroll:,.2f}")
    print(f"  Proposals: {len(proposals)} found → {len(approved)} approved → {len(executed)} executed")
    print()

    if not proposals:
        print("  No opportunities found. Markets quiet or signals insufficient.")
        print(f"{'='*60}\n")
        return

    # Group by strategy
    by_strategy = {}
    for p in proposals:
        s = p.get("strategy", "unknown")
        by_strategy.setdefault(s, []).append(p)

    for strategy, props in by_strategy.items():
        approved_in = [p for p in props if p.get("risk_check", {}).get("approved")]
        print(f"  [{strategy.upper()}] {len(props)} proposals, {len(approved_in)} approved")
        for p in props[:5]:
            status = "APPROVED" if p.get("risk_check", {}).get("approved") else "REJECTED"
            icon = "  +" if status == "APPROVED" else "  -"
            reason = p.get("reason", "")[:60]
            print(f"  {icon} ${p.get('size_usd', 0):>7.0f} | {p.get('question', '')[:45]}")
            print(f"         {reason}")
        print()

    if executed:
        total_deployed = sum(e.get("cost_usd", 0) for e in executed)
        print(f"  TOTAL DEPLOYED: ${total_deployed:,.2f}")

    print(f"{'='*60}\n")


def run_agent(bankroll: float = 1000.0, dry_run: bool = False):
    """Main agent loop: scan → propose → risk-check → execute."""
    mode = get_mode()
    logger.info("=== Polymarket Agent [%s mode] ===", mode.upper())
    logger.info("Bankroll: $%.2f", bankroll)

    # Initialize paper bankroll if needed
    if mode == "paper":
        init_paper_bankroll(bankroll)

    # 0. Darwinian evaluation — adjust weights if cycle is due
    if should_evaluate():
        eval_result = run_evaluation()
        if eval_result.get("rewrite_candidate"):
            logger.warning("Strategy '%s' flagged for rewrite — next cycle will attempt improvement",
                           eval_result["rewrite_candidate"])

    # 1. Load strategy params (may have been updated by evaluator)
    params = load_agent_config()

    # 2. Run all strategies to get proposals
    proposals = run_all_strategies(params)
    logger.info("Total proposals: %d", len(proposals))

    if not proposals:
        print_summary([], [], [], bankroll, mode)
        return {"proposals": 0, "approved": 0, "executed": 0}

    # 3. Risk gate — filter proposals
    approved = filter_proposals(proposals, bankroll)
    logger.info("Approved: %d / %d", len(approved), len(proposals))

    # 4. Generate trade reasoning via local LLM (audit trail, $0)
    try:
        from agent.local_signals import generate_trade_reasoning
        for p in approved:
            reasoning = generate_trade_reasoning(p)
            if reasoning and reasoning != p.get("reason", ""):
                p["llm_reasoning"] = reasoning
    except Exception:
        pass  # Local LLM optional — don't block execution

    # 5. Execute approved proposals (unless dry run)
    executed = []
    if not dry_run:
        for p in approved:
            result = place_limit_order(
                token_id=p.get("market_id", ""),
                side=p["side"],
                price=p["price"],
                size=p["size_shares"],
                market_question=p.get("question", ""),
                strategy=p.get("strategy", ""),
                reason=p.get("llm_reasoning", p.get("reason", "")),
                slug=p.get("slug", ""),
                token_hint=p.get("token_hint", "yes"),
            )
            result["category"] = p.get("category", "other")
            executed.append(result)
    else:
        logger.info("DRY RUN — skipping execution")

    # 5. Print summary
    print_summary(proposals, approved, executed, bankroll, mode)

    # 6. Save run results
    run_log = PROJECT_ROOT / "agent" / "runs.jsonl"
    run_record = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "mode": mode,
        "bankroll": bankroll,
        "proposals": len(proposals),
        "approved": len(approved),
        "executed": len(executed),
        "total_deployed": sum(e.get("cost_usd", 0) for e in executed),
        "strategies": {
            s: len([p for p in proposals if p.get("strategy") == s])
            for s in set(p.get("strategy", "?") for p in proposals)
        },
    }
    with open(run_log, "a") as f:
        f.write(json.dumps(run_record) + "\n")

    return run_record


def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="Polymarket Trading Agent")
    parser.add_argument("--bankroll", type=float, default=1000.0,
                        help="Starting bankroll in USD (default: $1000)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show proposals without executing")
    args = parser.parse_args()

    # Load .env — try dotenv first, fall back to manual parsing
    env_path = PROJECT_ROOT / ".env"
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        if env_path.exists():
            for line in env_path.read_text().strip().split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())

    run_agent(bankroll=args.bankroll, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
