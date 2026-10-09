"""Shared Polymarket client for the agent (official `polymarket-client` SDK).

Why this exists
---------------
Polymarket CLOB V2 (May 2026) stopped accepting orders from plain wallets
(EOAs) unless they were grandfathered. Every account must now trade through a
Polymarket **Deposit Wallet** (signature type 3). The old ``py-clob-client-v2``
library can't do that flow; the official ``polymarket`` SDK can, so every
module that talks to the CLOB goes through here.

Environment variables (in ``/opt/morning-brief/.env``):

- ``POLYMARKET_PRIVATE_KEY``   signer key (the bot's EOA) — required for live mode
- ``POLYMARKET_WALLET``        the account's Deposit Wallet address (what the
                               polymarket.com profile menu shows). If unset the
                               SDK derives the signer's default Deposit Wallet.
- ``POLYMARKET_RELAYER_API_KEY`` / ``POLYMARKET_RELAYER_API_KEY_ADDRESS``
                               optional; needed only for gasless on-chain
                               actions (redeem winnings, transfers). Orders
                               work without them.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging
import os

logger = logging.getLogger(__name__)

_cached_client = None


def get_private_key() -> str:
    return os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip()


def get_signer_address() -> str:
    """Public address of the signer key (the EOA). Empty if no key."""
    key = get_private_key()
    if not key:
        return ""
    from eth_account import Account
    return Account.from_key(key).address


def get_wallet_address() -> str:
    """The Polymarket account wallet that holds funds and positions.

    Order of precedence: ``POLYMARKET_WALLET`` → ``POLYMARKET_WALLET_ADDRESS``
    (legacy read-only override used by the dashboard) → the live client's
    resolved wallet → the signer address.
    """
    for var in ("POLYMARKET_WALLET", "POLYMARKET_WALLET_ADDRESS"):
        addr = os.environ.get(var, "").strip()
        if addr:
            return addr
    if _cached_client is not None:
        return str(_cached_client.wallet)
    return get_signer_address()


def get_client():
    """Return a cached, authenticated ``SecureClient`` or None (no key / failure)."""
    global _cached_client
    if _cached_client is not None:
        return _cached_client

    key = get_private_key()
    if not key:
        return None

    try:
        from polymarket import SecureClient, RelayerApiKey
    except ImportError:
        logger.error("polymarket-client SDK not installed — run: .venv/bin/pip install polymarket-client")
        return None

    wallet = os.environ.get("POLYMARKET_WALLET", "").strip() or None
    api_key = None
    rk = os.environ.get("POLYMARKET_RELAYER_API_KEY", "").strip()
    rk_addr = os.environ.get("POLYMARKET_RELAYER_API_KEY_ADDRESS", "").strip()
    if rk and rk_addr:
        api_key = RelayerApiKey(key=rk, address=rk_addr)

    try:
        client = SecureClient.create(private_key=key, wallet=wallet, api_key=api_key)
    except Exception as e:
        logger.error("Failed to create Polymarket client: %s", e)
        return None

    logger.info("Polymarket client ready — wallet %s (type %s), signer %s",
                client.wallet, client.wallet_type, get_signer_address())
    _cached_client = client
    return client


def get_collateral_balance() -> float:
    """Available collateral (pUSD) in the account wallet, in dollars."""
    client = get_client()
    if not client:
        return 0.0
    try:
        ba = client.get_balance_allowance(asset_type="COLLATERAL")
        return ba.balance / 1e6
    except Exception as e:
        logger.warning("Balance lookup failed: %s", e)
        return 0.0


def resolve_order_asset(slug: str) -> dict:
    """Resolve a market slug to the IDs the CLOB wants for orders.

    Returns ``{"yes": id, "no": id, "condition_id": ..., "question": ..., "version": ...}``.
    For V2 markets the order asset is the *position_id*; for V1 it's the CTF
    token_id. The SDK's Market model exposes both, so we pick per version.
    """
    client = get_client()
    try:
        if client:
            m = client.get_market(slug=slug)
        else:
            from polymarket import PublicClient
            with PublicClient() as pc:
                m = pc.get_market(slug=slug)
    except Exception as e:
        logger.debug("Market lookup failed for %s: %s", slug, e)
        return {}

    def pick(outcome):
        if getattr(m, "version", None) == "v2":
            return outcome.position_id or outcome.token_id
        return outcome.token_id or outcome.position_id

    yes, no = m.outcomes.yes, m.outcomes.no
    if not pick(yes) or not pick(no):
        return {}
    return {
        "yes": pick(yes),
        "no": pick(no),
        "condition_id": getattr(m, "condition_id", "") or "",
        "question": getattr(m, "question", "") or "",
        "version": getattr(m, "version", None),
    }


def list_open_orders() -> list[dict]:
    """Open orders as plain dicts (keys: id, side, price, original_size, size_matched, status, asset_id)."""
    client = get_client()
    if not client:
        return []
    try:
        out = []
        for o in client.list_open_orders():
            out.append({
                "id": o.id,
                "side": o.side,
                "price": float(o.price),
                "original_size": float(o.original_size),
                "size": float(o.original_size),
                "size_matched": float(o.size_matched),
                "status": o.status,
                "asset_id": o.asset_id,
                "outcome": o.outcome,
            })
        return out
    except Exception as e:
        logger.warning("Open-orders lookup failed: %s", e)
        return []
