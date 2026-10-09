"""Polymarket order executor — handles authentication and order placement.

Supports two modes:
- PAPER: logs what it would do, no real orders (default)
- LIVE: places real orders via the official `polymarket` SDK (see agent/pm_client.py)

Set POLYMARKET_PRIVATE_KEY (and POLYMARKET_WALLET) in .env to enable live trading.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
import os
from datetime import datetime

try:
    import requests as _requests
except ImportError:
    _requests = None

logger = logging.getLogger(__name__)

PAPER_TRADE_LOG = PROJECT_ROOT / "agent" / "paper_trades.jsonl"
GAMMA_API = "https://gamma-api.polymarket.com"

# Cache resolved token IDs to avoid repeated API calls
_token_cache: dict[str, dict] = {}


def resolve_token_ids(slug: str) -> dict:
    """Resolve a market slug to the CLOB asset IDs used for orders.

    Returns: {"yes": "id", "no": "id", "condition_id": "0x...", "question": ...}

    Prefers the SDK (version-aware: V2 markets order by position_id, V1 by
    token_id); falls back to the raw Gamma API when the SDK is unavailable.
    """
    if slug in _token_cache:
        return _token_cache[slug]

    try:
        from agent.pm_client import resolve_order_asset
        result = resolve_order_asset(slug)
        if result:
            _token_cache[slug] = result
            return result
    except Exception as e:
        logger.debug("SDK market resolution failed for %s: %s", slug, e)

    if not _requests:
        return {}

    try:
        resp = _requests.get(
            f"{GAMMA_API}/markets",
            params={"slug": slug, "limit": "1"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        markets = data if isinstance(data, list) else [data]

        if not markets:
            # Try searching by question text
            return {}

        m = markets[0]
        clob_ids = m.get("clobTokenIds", "")
        if isinstance(clob_ids, str):
            clob_ids = json.loads(clob_ids)

        if len(clob_ids) >= 2:
            result = {
                "yes": clob_ids[0],
                "no": clob_ids[1],
                "condition_id": m.get("conditionId", ""),
                "question": m.get("question", ""),
            }
            _token_cache[slug] = result
            return result

    except Exception as e:
        logger.debug("Token resolution failed for %s: %s", slug, e)

    return {}


def get_mode() -> str:
    """Returns 'live' if private key is configured, otherwise 'paper'."""
    key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    if key and len(key) > 10:
        return "live"
    return "paper"


def get_client():
    """Authenticated Polymarket ``SecureClient`` (cached) or None if no key.

    History: py-clob-client 0.34.x broke with CLOB v2 (Apr 2026,
    `order_version_mismatch`); py-clob-client-v2 then broke on 2026-06-28 when
    the bot moved to a fresh wallet, because V2 rejects orders from
    non-grandfathered EOAs (`maker address not allowed, please use the deposit
    wallet flow`). The official SDK handles the Deposit Wallet flow.
    """
    from agent.pm_client import get_client as _get
    return _get()


def place_limit_order(
    token_id: str,
    side: str,
    price: float,
    size: float,
    market_question: str = "",
    strategy: str = "",
    reason: str = "",
    slug: str = "",
    token_hint: str = "yes",
) -> dict:
    """Place a limit order. Returns order result dict.

    In paper mode, logs the trade. In live mode, submits to Polymarket CLOB.
    Uses slug + token_hint to resolve the actual CLOB token ID from Gamma API.
    """
    mode = get_mode()
    now = datetime.now().astimezone().isoformat()

    # Price sanity checks
    if price <= 0 or price >= 1.0:
        logger.warning("BLOCKED: Invalid price %.4f for %s — must be (0, 1)", price, market_question[:50])
        return {"status": "error", "error": f"Invalid price {price}", "timestamp": now}
    if size <= 0:
        logger.warning("BLOCKED: Invalid size %.2f for %s", size, market_question[:50])
        return {"status": "error", "error": f"Invalid size {size}", "timestamp": now}

    # Resolve real token ID from slug if we have one
    resolved_token = token_id
    if slug and mode == "live":
        tokens = resolve_token_ids(slug)
        if tokens:
            resolved_token = tokens.get(token_hint, tokens.get("yes", token_id))
            logger.info("Resolved %s → %s token: %s...%s",
                        slug, token_hint, resolved_token[:8], resolved_token[-8:])
        else:
            logger.warning("Could not resolve token for slug: %s", slug)

    order_record = {
        "timestamp": now,
        "mode": mode,
        "token_id": resolved_token[:20] + "..." if len(resolved_token) > 20 else resolved_token,
        "slug": slug,
        "token_hint": token_hint,
        "side": side,
        "price": round(price, 4),
        "size": round(size, 2),
        "cost_usd": round(price * size, 2),
        "market": market_question[:100],
        "strategy": strategy,
        "reason": reason,
        "status": "pending",
        "order_type": "limit",  # Always limit — makers have positive excess returns (Becker research)
        "role": "maker",        # Limit orders = maker. GTC ensures we're providing liquidity.
    }

    if mode == "paper":
        order_record["status"] = "paper_filled"
        order_record["order_id"] = f"paper_{int(datetime.now().timestamp())}"

        with open(PAPER_TRADE_LOG, "a") as f:
            f.write(json.dumps(order_record) + "\n")

        logger.info(
            "[PAPER] %s %s %.0f shares @ %.2f ($%.2f) — %s | %s",
            side, token_hint.upper(), size, price, price * size,
            market_question[:50], reason[:50],
        )
        return order_record

    # LIVE execution — CRITICAL: abort if token resolution failed
    if not resolved_token or resolved_token == token_id and not token_id:
        order_record["status"] = "error"
        order_record["error"] = f"Token resolution failed for slug: {slug}"
        logger.error("BLOCKED: Cannot place live order without resolved token ID for %s", slug)
        with open(PAPER_TRADE_LOG, "a") as f:
            f.write(json.dumps(order_record) + "\n")
        return order_record

    client = get_client()
    if not client:
        order_record["status"] = "error"
        order_record["error"] = "No authenticated client"
        logger.error("Cannot place live order — no client")
        return order_record

    # The SDK fetches tick size and neg-risk status per asset itself, so the
    # old neg-risk skip and hard-coded tick_size are gone.
    try:
        resp = client.place_limit_order(
            asset_id=resolved_token,
            price=round(price, 2),
            size=round(size, 2),
            side="BUY" if side.upper() == "BUY" else "SELL",
        )

        if getattr(resp, "ok", False):
            order_record["status"] = "submitted"
            order_record["order_id"] = resp.order_id
            order_record["response"] = {
                "status": str(resp.status),
                "making_amount": str(resp.making_amount),
                "taking_amount": str(resp.taking_amount),
                "trade_ids": list(resp.trade_ids),
            }
            logger.info(
                "[LIVE] %s %.0f shares @ %.2f ($%.2f) — %s | order %s (%s)",
                side, size, price, price * size, market_question[:50],
                resp.order_id[:12], resp.status,
            )
        else:
            order_record["status"] = "error"
            order_record["error"] = f"{getattr(resp, 'code', 'rejected')}: {getattr(resp, 'message', resp)}"
            logger.error("Order rejected: %s", order_record["error"])

    except Exception as e:
        order_record["status"] = "error"
        order_record["error"] = str(e)
        logger.error("Order execution failed: %s", e)

    # Log all live trades too
    with open(PAPER_TRADE_LOG, "a") as f:
        f.write(json.dumps(order_record) + "\n")

    return order_record


def get_balance() -> float:
    """Get available USDC balance. Returns 0 in paper mode."""
    mode = get_mode()
    if mode == "paper":
        # Read paper bankroll from config
        config_path = PROJECT_ROOT / "agent" / "bankroll.json"
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text())
                return data.get("balance", 1000.0)
            except (json.JSONDecodeError, OSError):
                pass
        return 1000.0  # Default $1K paper bankroll

    from agent.pm_client import get_collateral_balance
    return get_collateral_balance()


def get_paper_trades() -> list:
    """Read all paper trades from the log."""
    if not PAPER_TRADE_LOG.exists():
        return []
    trades = []
    for line in PAPER_TRADE_LOG.read_text().strip().split("\n"):
        if line:
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return trades


def init_paper_bankroll(amount: float = 1000.0) -> None:
    """Initialize the paper trading bankroll."""
    config_path = PROJECT_ROOT / "agent" / "bankroll.json"
    data = {
        "balance": amount,
        "initial": amount,
        "started_at": datetime.now().astimezone().isoformat(),
    }
    config_path.write_text(json.dumps(data, indent=2))
    logger.info("Paper bankroll initialized: $%.2f", amount)
