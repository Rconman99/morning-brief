"""Equity Trading Agent — main runner.

Orchestrates: signals → strategies → risk gate → execution → logging.

Usage:
    # Paper trading (default — no Alpaca keys needed for offline mode)
    .venv/bin/python agent/equity_run.py

    # With custom portfolio value
    .venv/bin/python agent/equity_run.py --bankroll 15000

    # Dry run — show proposals without executing
    .venv/bin/python agent/equity_run.py --dry-run

    # Force run outside market hours (for testing)
    .venv/bin/python agent/equity_run.py --force --dry-run
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

from agent.equity_config import load_equity_config
from agent.equity_db import init_db, record_daily_equity, get_performance_metrics
from agent.equity_strategies import run_all_equity_strategies
from agent.equity_risk_gate import filter_proposals, filter_options_proposals, _is_market_hours
from agent.equity_executor import (
    place_order, get_mode, get_account, get_positions, get_balance,
)
from agent.options_config import load_options_config
from agent.options_strategies import run_all_options_strategies
from agent.options_executor import place_options_order

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
                logging.FileHandler(log_dir / "equity_agent.log", mode="a"),
            ],
        )


def print_summary(proposals: list, approved: list, executed: list,
                   portfolio_value: float, mode: str):
    """Print a human-readable summary of the agent run."""
    now = datetime.now().astimezone()
    print(f"\n{'='*60}")
    print(f"  EQUITY AGENT — {now.strftime('%Y-%m-%d %H:%M')} [{mode.upper()}]")
    print(f"{'='*60}")
    print(f"  Portfolio: ${portfolio_value:,.2f}")
    print(f"  Proposals: {len(proposals)} found → {len(approved)} approved → {len(executed)} executed")
    print()

    if not proposals:
        print("  No opportunities found. Signals insufficient or market conditions unclear.")
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
            status = "+" if p.get("risk_check", {}).get("approved") else "-"
            reason = p.get("reason", "")[:55]
            price = p.get("limit_price", 0)
            qty = p.get("quantity", p.get("quantity_override", 0))
            cost = price * qty if price and qty else 0
            print(f"    {status} {p['side']:4} {p['ticker']:5} "
                  f"{qty:>6.1f} sh @ ${price:>8.2f} = ${cost:>8.0f}  "
                  f"[conv={p.get('conviction', 0)}]")
            if not p.get("risk_check", {}).get("approved"):
                rej = p.get("risk_check", {}).get("reason", "")[:60]
                print(f"      REJECTED: {rej}")
        print()

    if executed:
        total_deployed = sum(
            (e.get("price", 0) or 0) * (e.get("quantity", 0) or 0)
            for e in executed
        )
        print(f"  TOTAL DEPLOYED: ${total_deployed:,.2f}")

    print(f"{'='*60}\n")


def run_equity_agent(portfolio_value: float = 15000.0, dry_run: bool = False,
                     force: bool = False):
    """Main agent loop: scan → propose → risk-check → execute."""
    mode = get_mode()
    logger.info("=== Equity Agent [%s mode] ===", mode.upper())
    logger.info("Portfolio: $%.2f", portfolio_value)

    # Init database
    init_db()

    # Check market hours (unless forced)
    if not force and not _is_market_hours():
        logger.info("Market closed — use --force to run anyway")
        print("\n  Market closed. Use --force to run outside trading hours.\n")
        if not dry_run:
            return {"proposals": 0, "approved": 0, "executed": 0, "reason": "market_closed"}

    # Try to get real portfolio value from Alpaca
    acct = get_account()
    if acct.get("equity") and acct["equity"] > 0:
        portfolio_value = acct["equity"]
        logger.info("Using Alpaca equity: $%.2f", portfolio_value)

    # 1. Load strategy params
    params = load_equity_config()

    # 2. Run all strategies to get proposals
    proposals = run_all_equity_strategies(params)
    logger.info("Total proposals: %d", len(proposals))

    if not proposals:
        print_summary([], [], [], portfolio_value, mode)
        return {"proposals": 0, "approved": 0, "executed": 0}

    # 3. Risk gate — filter proposals
    approved = filter_proposals(proposals, portfolio_value, force=force)
    logger.info("Approved: %d / %d", len(approved), len(proposals))

    # 4. Generate trade reasoning via local LLM (audit trail, $0)
    try:
        from agent.local_signals import generate_trade_reasoning
        for p in approved:
            reasoning = generate_trade_reasoning(p)
            if reasoning and reasoning != p.get("reason", ""):
                p["llm_reasoning"] = reasoning
    except Exception:
        pass  # Local LLM optional

    # 5. Execute approved proposals (unless dry run)
    executed = []
    run_id = f"eq_{int(datetime.now().timestamp())}"

    if not dry_run:
        for p in approved:
            result = place_order(
                ticker=p["ticker"],
                side=p["side"],
                quantity=p.get("quantity", 0),
                limit_price=p.get("limit_price"),
                strategy=p.get("strategy", ""),
                reason=p.get("llm_reasoning", p.get("reason", "")),
                conviction=p.get("conviction", 0),
                sector=p.get("sector", ""),
                run_id=run_id,
            )
            executed.append(result)
    else:
        logger.info("DRY RUN — skipping execution")

    # 5b. OPTIONS STRATEGIES
    options_executed = []
    try:
        options_params = load_options_config()
        options_proposals = run_all_options_strategies(options_params)
        logger.info("Options proposals: %d", len(options_proposals))

        if options_proposals:
            approved_options = filter_options_proposals(options_proposals, portfolio_value, force=force)
            logger.info("Options approved: %d / %d", len(approved_options), len(options_proposals))

            # LLM reasoning for options
            try:
                from agent.local_signals import generate_trade_reasoning
                for p in approved_options:
                    reasoning = generate_trade_reasoning(p)
                    if reasoning and reasoning != p.get("reason", ""):
                        p["llm_reasoning"] = reasoning
            except Exception:
                pass

            if not dry_run:
                for p in approved_options:
                    result = place_options_order(
                        occ_symbol=p["occ_symbol"],
                        side=p["side"],
                        qty=p.get("quantity", 1),
                        limit_price=p.get("limit_price", 0),
                        strategy=p.get("strategy", ""),
                        reason=p.get("llm_reasoning", p.get("reason", "")),
                        conviction=p.get("conviction", 0),
                        run_id=run_id,
                    )
                    options_executed.append(result)
            else:
                logger.info("DRY RUN — skipping options execution")

            # Add options to proposals list for summary
            proposals.extend(options_proposals)
            approved.extend(approved_options)
            executed.extend(options_executed)
    except Exception as e:
        logger.warning("Options strategies skipped: %s", e)

    # 6. Print summary
    print_summary(proposals, approved, executed, portfolio_value, mode)

    # 7. Record daily equity snapshot
    try:
        cash = acct.get("cash") or portfolio_value
        invested = sum(
            (p.get("market_value") or 0) for p in get_positions()
        )
        record_daily_equity(portfolio_value, float(cash), invested)
    except Exception as e:
        logger.debug("Daily equity snapshot failed: %s", e)

    # 8. Save run log
    run_log = PROJECT_ROOT / "agent" / "equity_runs.jsonl"
    run_record = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "run_id": run_id,
        "mode": mode,
        "portfolio_value": portfolio_value,
        "proposals": len(proposals),
        "approved": len(approved),
        "executed": len(executed),
        "total_deployed": sum(
            (e.get("price", 0) or 0) * (e.get("quantity", 0) or 0)
            for e in executed
        ),
        "strategies": {
            s: len([p for p in proposals if p.get("strategy") == s])
            for s in set(p.get("strategy", "?") for p in proposals)
        },
    }
    with open(run_log, "a") as f:
        f.write(json.dumps(run_record) + "\n")

    # 9. Send daily summary notification
    try:
        from lib.notify import alert_daily_summary, alert_risk_breach
        run_record["portfolio_value"] = portfolio_value
        alert_daily_summary(run_record)

        # Check for risk breaches
        from agent.equity_db import get_current_drawdown, get_drawdown_pause_until
        drawdown = get_current_drawdown()
        if drawdown > 0.03:  # Alert at 3% drawdown (pause at 5%)
            alert_risk_breach(
                "Drawdown Warning",
                f"Current drawdown: {drawdown*100:.1f}% (pause at 5%)\n"
                f"Portfolio: ${portfolio_value:,.2f}"
            )
        pause = get_drawdown_pause_until()
        if pause:
            alert_risk_breach("Trading Paused", f"Drawdown pause active until {pause}")
    except Exception as e:
        logger.debug("Notification failed: %s", e)

    return run_record


def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="Equity Trading Agent")
    parser.add_argument("--bankroll", type=float, default=15000.0,
                        help="Portfolio value in USD (default: $15,000)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show proposals without executing")
    parser.add_argument("--force", action="store_true",
                        help="Run even outside market hours")
    args = parser.parse_args()

    # Load .env
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

    run_equity_agent(
        portfolio_value=args.bankroll,
        dry_run=args.dry_run,
        force=args.force,
    )


if __name__ == "__main__":
    main()
