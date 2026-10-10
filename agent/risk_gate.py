"""Risk gate — the CRO agent that approves or rejects trade proposals.

Every proposal from strategies.py passes through here before execution.
Enforces hard limits from config.py that cannot be overridden.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime, timedelta

from agent import config
from agent.executor import get_balance, get_paper_trades, get_mode

logger = logging.getLogger(__name__)


def _resolved_slugs() -> set:
    """Markets the ledger has seen settle — their trades no longer tie up capital."""
    try:
        c = json.loads((PROJECT_ROOT / "agent" / "ledger_cache.json").read_text())
        return set(c.get("settlements", {}).keys())
    except (OSError, json.JSONDecodeError):
        return set()


def _group(t: dict) -> str:
    g = t.get("group")
    if g:
        return g
    try:
        from agent.pm_us import group_key
        return group_key(t.get("slug", ""))
    except Exception:
        return t.get("slug", "")


def _calculate_exposure(trades: list) -> dict:
    """Current exposure from open (unsettled) trades in the last 30 days."""
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    resolved = _resolved_slugs()
    active_cost = 0.0
    category_cost = {}
    strategy_cost = {}
    groups = {}

    today = datetime.now().date().isoformat()
    week_ago = (datetime.now() - timedelta(days=7)).date().isoformat()
    daily_cost = 0.0
    weekly_cost = 0.0

    for t in trades:
        ts = t.get("timestamp", "")
        if ts < cutoff:
            continue

        cost = t.get("cost_usd", 0)
        cat = t.get("category", "other") if "category" in t else t.get("strategy", "other")
        status = t.get("status", "")

        if status in ("paper_filled", "submitted"):
            if ts[:10] == today:
                daily_cost += cost
            if ts[:10] >= week_ago:
                weekly_cost += cost
            if t.get("slug") in resolved:
                continue  # settled: capital is back
            active_cost += cost
            category_cost[cat] = category_cost.get(cat, 0) + cost
            strat = t.get("strategy", "unknown")
            strategy_cost[strat] = strategy_cost.get(strat, 0) + cost
            g = _group(t)
            groups[g] = groups.get(g, 0) + 1

    return {
        "total_deployed": active_cost,
        "by_category": category_cost,
        "by_strategy": strategy_cost,
        "groups": groups,
        "today_deployed": daily_cost,
        "week_deployed": weekly_cost,
    }


def strategy_budgets(bankroll: float, active: set) -> dict:
    """Capital budget per active strategy, proportional to its performance weight.

    Weights come from agent/strategy_params.json and are moved by the evaluator
    using *realized* results (agent/ledger.py): winners get more, losers less,
    clamped to [STRATEGY_WEIGHT_MIN, STRATEGY_WEIGHT_MAX] so a new strategy
    always keeps some exploration capital.
    """
    params = config.load_agent_config()
    w = {s: float((params.get(s) or {}).get("weight", 1.0)) for s in active}
    total = sum(w.values()) or 1.0
    deployable = bankroll * config.MAX_TOTAL_EXPOSURE_PCT
    return {s: round(deployable * w[s] / total, 2) for s in active}


def check_proposal(proposal: dict, bankroll: float, trades: list) -> dict:
    """Check a trade proposal against risk limits.

    Returns: {"approved": bool, "reason": str, "adjusted_size": float}
    """
    cost = proposal.get("size_usd", 0)
    category = proposal.get("category", "other")
    strategy = proposal.get("strategy", "unknown")

    exposure = _calculate_exposure(trades)

    # --- HARD LIMIT CHECKS ---

    # 1. Single position size
    max_single = bankroll * config.MAX_SINGLE_POSITION_PCT
    if cost > max_single:
        adjusted = max_single
        logger.warning("Position $%.0f exceeds %.0f%% limit ($%.0f) — sizing down",
                        cost, config.MAX_SINGLE_POSITION_PCT * 100, max_single)
        proposal["size_usd"] = adjusted
        proposal["size_shares"] = adjusted / max(proposal.get("price", 0.5), 0.01)
        cost = adjusted

    # 1b. Correlation: one position per event group (same underlying + date).
    group = _group(proposal)
    if exposure["groups"].get(group, 0) >= config.MAX_POSITIONS_PER_GROUP:
        return {
            "approved": False,
            "reason": f"Already holding {exposure['groups'][group]} position(s) in event group '{group}'",
        }

    # 1c. Strategy budget: capital follows realized performance (see strategy_budgets).
    budgets = proposal.get("_budgets") or {}
    if strategy in budgets:
        used = exposure["by_strategy"].get(strategy, 0)
        room = budgets[strategy] - used
        if room < 1.0:
            return {
                "approved": False,
                "reason": f"Strategy '{strategy}' budget ${budgets[strategy]:.0f} used (${used:.0f} open)",
            }
        if cost > room:
            proposal["size_usd"] = room
            proposal["size_shares"] = room / max(proposal.get("price", 0.5), 0.01)
            cost = room

    # 2. Total exposure
    total_after = exposure["total_deployed"] + cost
    max_total = bankroll * config.MAX_TOTAL_EXPOSURE_PCT
    if total_after > max_total:
        remaining = max_total - exposure["total_deployed"]
        if remaining <= 0:
            return {
                "approved": False,
                "reason": f"Total exposure ${exposure['total_deployed']:.0f} at {config.MAX_TOTAL_EXPOSURE_PCT*100:.0f}% cap — no room",
            }
        proposal["size_usd"] = remaining
        proposal["size_shares"] = remaining / max(proposal.get("price", 0.5), 0.01)
        cost = remaining

    # 3. Category concentration
    cat_after = exposure["by_category"].get(category, 0) + cost
    max_cat = bankroll * config.MAX_CATEGORY_EXPOSURE_PCT
    if cat_after > max_cat:
        remaining = max_cat - exposure["by_category"].get(category, 0)
        if remaining <= 0:
            return {
                "approved": False,
                "reason": f"Category '{category}' at ${exposure['by_category'].get(category, 0):.0f} — {config.MAX_CATEGORY_EXPOSURE_PCT*100:.0f}% cap hit",
            }
        proposal["size_usd"] = remaining
        proposal["size_shares"] = remaining / max(proposal.get("price", 0.5), 0.01)
        cost = remaining

    # 4. Minimum edge — applies to ALL strategies, not just weather
    edge_raw = proposal.get("edge_pct", 0)
    edge = edge_raw / 100.0 if edge_raw > 1 else edge_raw
    if strategy in ("weather_edge", "probability_arb") and edge < config.MIN_EDGE_TO_TRADE:
        return {
            "approved": False,
            "reason": f"Edge {edge*100:.1f}% below {config.MIN_EDGE_TO_TRADE*100:.0f}% minimum",
        }

    # 4b. Require slug for live execution — can't trade without token resolution
    if not proposal.get("slug") and get_mode() == "live":
        return {
            "approved": False,
            "reason": "No market slug — cannot resolve CLOB token ID for live execution",
        }

    # 5. Minimum volume/liquidity
    vol = proposal.get("volume_24h", proposal.get("min_volume_24h", 99999))
    if vol < config.MIN_VOLUME_24H:
        return {
            "approved": False,
            "reason": f"Volume ${vol:,.0f} below ${config.MIN_VOLUME_24H:,.0f} minimum",
        }

    # 6. Daily loss check (simplified — would need P&L tracking for real)
    # For now, just check daily deployment vs bankroll
    daily_total = exposure["today_deployed"] + cost
    daily_limit = bankroll * config.MAX_DAILY_LOSS_PCT * 5  # 5x loss limit as deployment limit
    if daily_total > daily_limit:
        return {
            "approved": False,
            "reason": f"Daily deployment ${daily_total:.0f} exceeds limit — pausing",
        }

    # 7. Duplicate detection — don't buy the same market twice in 24h
    slug = proposal.get("slug", "")
    if slug:
        today = datetime.now().date().isoformat()
        for t in trades:
            if t.get("slug") == slug and t.get("timestamp", "")[:10] == today:
                return {
                    "approved": False,
                    "reason": f"Already traded {slug} today — skipping duplicate",
                }

    # 8. Don't trade if cost is trivially small
    if cost < 1.0:
        return {
            "approved": False,
            "reason": "Position size less than $1 — not worth transaction cost",
        }

    # 9. Weekly deployment limit — don't deploy more than MAX_WEEKLY_LOSS_PCT * 5 in a week
    weekly_total = exposure["week_deployed"] + cost
    weekly_limit = bankroll * config.MAX_WEEKLY_LOSS_PCT * 5
    if weekly_total > weekly_limit:
        return {
            "approved": False,
            "reason": f"Weekly deployment ${weekly_total:.0f} exceeds limit ${weekly_limit:.0f} — pausing",
        }

    # 10. Microstructure block — longshot bias filter may have flagged this
    if proposal.get("microstructure_blocked"):
        return {
            "approved": False,
            "reason": f"Microstructure filter blocked: "
                      + "; ".join(proposal.get("microstructure_warnings", ["longshot bias"])),
        }

    # 11. Conviction floor — require minimum conviction score
    conviction = proposal.get("conviction", 0)
    if isinstance(conviction, float) and conviction < 0.3:
        return {
            "approved": False,
            "reason": f"Conviction {conviction:.2f} too low (minimum 0.3)",
        }

    return {
        "approved": True,
        "reason": f"Approved: ${cost:.0f} on {strategy}/{category} (exposure {exposure['total_deployed']+cost:.0f}/{max_total:.0f})",
        "adjusted_size": cost,
    }


def filter_proposals(proposals: list, bankroll: float) -> list:
    """Run all proposals through the risk gate. Returns approved proposals."""
    trades = list(get_paper_trades())
    approved = []

    exposure = _calculate_exposure(trades)
    active = {p.get("strategy", "unknown") for p in proposals} | set(exposure["by_strategy"].keys())
    budgets = strategy_budgets(bankroll, active)
    if budgets:
        logger.info("Strategy budgets: %s", ", ".join(f"{k} ${v:.0f}" for k, v in sorted(budgets.items())))

    for p in proposals:
        p["_budgets"] = budgets
        result = check_proposal(p, bankroll, trades)
        p.pop("_budgets", None)
        p["risk_check"] = result

        if result["approved"]:
            approved.append(p)
            # Count this approval against limits for the rest of the run.
            trades.append({
                "timestamp": datetime.now().isoformat(),
                "status": "paper_filled",
                "cost_usd": p.get("size_usd", 0),
                "strategy": p.get("strategy", "unknown"),
                "category": p.get("category", "other"),
                "slug": p.get("slug", ""),
                "group": _group(p),
            })
            logger.info("APPROVED: %s — %s", p.get("question", "")[:50], result["reason"])
        else:
            logger.info("REJECTED: %s — %s", p.get("question", "")[:50], result["reason"])

    return approved
