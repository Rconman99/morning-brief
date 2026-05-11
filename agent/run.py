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

from agent.config import AUTO_EXIT_ENABLED, AUTO_EXIT_PRICE, load_agent_config
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


def auto_exit_winners(target_price: float, dry_run: bool) -> list:
    """Place SELL limits on positions whose price has reached target_price.

    Recycles capital instead of holding to resolution. Tracks placed orders in
    pending_exits.json keyed by asset id so we don't place duplicate sells
    every 15-min cycle. Stale entries (positions already sold/resolved) are
    pruned each call.

    Field names match the Polymarket data-api response: `asset` (token_id),
    `curPrice` (current mid), `title` (market question), `currentValue`.
    """
    try:
        from agent.close_positions import close_position
        from agent.tracker import load_positions
        from agent.executor import get_client
    except ImportError as e:
        logger.warning("auto_exit_winners skipped — %s", e)
        return []

    try:
        positions = load_positions()
    except Exception as e:
        logger.warning("auto_exit_winners: load_positions failed — %s", e)
        return []

    active_asset_ids = {p.get("asset", "") for p in positions if p.get("asset")}

    pending_path = PROJECT_ROOT / "agent" / "pending_exits.json"
    pending = set()
    if pending_path.exists():
        try:
            pending = set(json.loads(pending_path.read_text()))
        except (json.JSONDecodeError, OSError):
            pass

    # Drop entries for positions we no longer hold (sold/resolved)
    pending &= active_asset_ids

    # Filter out neg-risk markets — py-clob-client 0.34.6 builds the wrong
    # order version and the CLOB rejects with `order_version_mismatch`.
    # Until the SDK is upgraded, these have to be redeemed manually via UI.
    eligible = [p for p in positions if not p.get("negativeRisk")]
    skipped_neg_risk = len(positions) - len(eligible)

    candidates = [
        p for p in eligible
        if p.get("curPrice", 0) >= target_price
        and p.get("asset", "") not in pending
    ]

    logger.info("Auto-exit: %d active positions (%d neg-risk skipped), %d candidates at >= %.3f",
                len(positions), skipped_neg_risk, len(candidates), target_price)

    if not candidates:
        pending_path.write_text(json.dumps(list(pending)))
        return []

    logger.info("Auto-exit: placing SELL limits on %d position(s)", len(candidates))

    client = None if dry_run else get_client()
    placed = []
    for p in candidates:
        result = close_position(client, p, dry_run=dry_run)
        if result.get("status") in ("submitted", "dry_run"):
            pending.add(p.get("asset", ""))
            placed.append({**result, "title": p.get("title", "")})

    pending_path.write_text(json.dumps(list(pending)))
    return placed


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

    # Inject bankroll into each strategy's params so sizing can scale with it
    for strategy_name, strategy_params in params.items():
        if isinstance(strategy_params, dict):
            strategy_params["bankroll"] = bankroll

    # 1.5. Auto-exit: take profit on positions at/above AUTO_EXIT_PRICE
    # Disabled by default while pre-existing hold-to-resolution positions are open;
    # flip AUTO_EXIT_ENABLED in config.py once those have settled.
    if AUTO_EXIT_ENABLED:
        exits_placed = auto_exit_winners(AUTO_EXIT_PRICE, dry_run=dry_run)
        if exits_placed:
            logger.info("Placed %d auto-exit SELL order(s)", len(exits_placed))
    else:
        logger.debug("AUTO_EXIT_ENABLED=False — skipping take-profit step")

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
    failed = []
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

            # Only count successful executions — not errors
            if result.get("status") in ("submitted", "paper_filled"):
                executed.append(result)
            else:
                failed.append(result)
                logger.warning("EXECUTION FAILED: %s — %s",
                               p.get("question", "")[:50],
                               result.get("error", "unknown"))
    else:
        logger.info("DRY RUN — skipping execution")

    if failed:
        logger.warning("%d / %d executions FAILED", len(failed), len(failed) + len(executed))

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
        "failed": len(failed),
        "total_deployed": sum(e.get("cost_usd", 0) for e in executed),
        "strategies": {
            s: len([p for p in proposals if p.get("strategy") == s])
            for s in set(p.get("strategy", "?") for p in proposals)
        },
    }
    with open(run_log, "a") as f:
        f.write(json.dumps(run_record) + "\n")

    # 7. Check resolved positions — notify on wins/losses
    try:
        from lib.notify import send_telegram
        from agent.tracker import get_positions as get_pm_positions, load_env as pm_load_env
        from eth_account import Account

        pm_load_env()
        pm_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
        if pm_key:
            wallet = Account.from_key(pm_key).address
            all_positions = get_pm_positions(wallet)

            # Track which resolutions we've already notified about
            notified_path = PROJECT_ROOT / "agent" / "resolved_notified.json"
            notified = set()
            if notified_path.exists():
                try:
                    notified = set(json.loads(notified_path.read_text()))
                except (json.JSONDecodeError, OSError):
                    pass

            resolved = [p for p in all_positions if (p.get("currentValue") or 0) == 0]
            new_resolutions = []
            for p in resolved:
                # Use a unique key for each position
                pos_key = f"{p.get('asset', '')}-{p.get('conditionId', '')}"
                if pos_key in notified or pos_key == "-":
                    continue

                pnl = p.get("cashPnl", 0)
                title = p.get("title", "Unknown")[:50]
                outcome = p.get("outcome", "?")
                cost = p.get("initialValue", 0)
                won = pnl > 0

                icon = "W" if won else "L"
                pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

                new_resolutions.append(
                    f"  [{icon}] {title}\n"
                    f"      {outcome} — Cost ${cost:.2f} → P&L {pnl_str}"
                )
                notified.add(pos_key)

            if new_resolutions:
                wins = sum(1 for p in resolved if p.get("cashPnl", 0) > 0 and
                          f"{p.get('asset', '')}-{p.get('conditionId', '')}" in notified)
                total_pnl = sum(p.get("cashPnl", 0) for p in resolved)

                msg = f"<b>Market Resolved!</b>\n\n"
                msg += "\n".join(new_resolutions[:10])
                msg += f"\n\nAll-time: {len([p for p in resolved if p.get('cashPnl',0)>0])}W / "
                msg += f"{len([p for p in resolved if p.get('cashPnl',0)<0])}L"
                msg += f"\nResolved P&L: ${total_pnl:+.2f}"
                send_telegram(msg)

                # Save notified set
                notified_path.write_text(json.dumps(list(notified)))
    except Exception as e:
        logger.debug("Resolution check failed: %s", e)

    # 8. Send notifications — ONLY when trades actually execute or fail
    try:
        from lib.notify import send_telegram
        if executed:
            msg = (
                f"<b>Polymarket Agent</b>\n"
                f"Proposals: {len(proposals)} → {len(approved)} approved → {len(executed)} executed\n"
                f"Failed: {len(failed)}\n"
                f"Deployed: ${sum(e.get('cost_usd', 0) for e in executed):,.2f}"
                f"\n\nTrades:"
            )
            for e in executed[:5]:
                msg += f"\n  {e.get('side', '?')} {e.get('slug', '?')[:30]} @ {e.get('price', 0):.2f}"
            send_telegram(msg)
        elif failed:
            # Only alert on failures, not empty runs
            msg = f"<b>Polymarket Agent</b>\n⚠ {len(failed)} trades FAILED:"
            for f_ in failed[:3]:
                msg += f"\n  {f_.get('error', 'unknown')[:50]}"
            send_telegram(msg)
        # No message when 0 executed, 0 failed — stay silent
    except Exception:
        pass

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
