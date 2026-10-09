"""Move collateral (pUSD) from the bot's signer EOA into its Polymarket Deposit Wallet.

Why: CLOB V2 trades from the Deposit Wallet, not the EOA. The 2026-06-28 sweep
left the bankroll sitting in the EOA (0x1E52…6356), where the CLOB can't see it.

Usage (from /opt/morning-brief):
    .venv/bin/python agent/fund_deposit_wallet.py            # DRY RUN — prints what it would send
    .venv/bin/python agent/fund_deposit_wallet.py --execute  # actually send

Safety:
- Destination must equal POLYMARKET_WALLET in .env AND the wallet the SDK
  resolves for this signer, or the script refuses.
- Sends the full pUSD balance minus nothing else; POL stays for gas.
- Plain ERC-20 transfer signed by the EOA (needs ~0.01 POL of gas).
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import os

PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
RPC = os.environ.get("POLYGON_RPC", "https://polygon-bor-rpc.publicnode.com")
ERC20_ABI = [
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"type": "bool"}]},
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "", "type": "address"}], "outputs": [{"type": "uint256"}]},
]


def load_env():
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="Actually send (default is dry run)")
    ap.add_argument("--amount", type=float, default=0.0, help="USD amount to send (default: full balance)")
    args = ap.parse_args()

    load_env()
    from web3 import Web3
    from eth_account import Account
    from agent.pm_client import get_client, get_private_key

    key = get_private_key()
    if not key:
        print("No POLYMARKET_PRIVATE_KEY in .env"); return 2
    acct = Account.from_key(key)
    dest_env = os.environ.get("POLYMARKET_WALLET", "").strip()
    if not dest_env:
        print("Set POLYMARKET_WALLET in .env to the Deposit Wallet address first."); return 2

    client = get_client()
    if not client:
        print("Could not create Polymarket client."); return 2
    dest_sdk = str(client.wallet)
    if dest_sdk.lower() != dest_env.lower():
        print(f"REFUSING: POLYMARKET_WALLET ({dest_env}) != SDK-resolved wallet ({dest_sdk})"); return 2
    if client.wallet_type != "DEPOSIT_WALLET":
        print(f"REFUSING: wallet type is {client.wallet_type}, expected DEPOSIT_WALLET"); return 2
    dest = Web3.to_checksum_address(dest_env)

    w3 = Web3(Web3.HTTPProvider(RPC))
    token = w3.eth.contract(address=Web3.to_checksum_address(PUSD), abi=ERC20_ABI)
    bal = token.functions.balanceOf(acct.address).call()
    pol = w3.from_wei(w3.eth.get_balance(acct.address), "ether")
    amount = int(round(args.amount * 1e6)) if args.amount > 0 else bal

    print(f"from (EOA):   {acct.address}")
    print(f"to (deposit): {dest}")
    print(f"EOA pUSD:     {bal/1e6:.6f}   POL for gas: {pol:.4f}")
    print(f"sending:      {amount/1e6:.6f} pUSD")
    if amount <= 0 or amount > bal:
        print("Nothing to send / amount exceeds balance."); return 2
    if pol < 0.01:
        print("Not enough POL for gas."); return 2
    if not args.execute:
        print("\nDRY RUN — re-run with --execute to send."); return 0

    tx = token.functions.transfer(dest, amount).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": 137,
    })
    tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.3)
    signed = acct.sign_transaction(tx)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"sent: https://polygonscan.com/tx/{txh.hex()}")
    rcpt = w3.eth.wait_for_transaction_receipt(txh, timeout=180)
    print("status:", "SUCCESS" if rcpt.status == 1 else "FAILED")
    print(f"deposit wallet pUSD now: {token.functions.balanceOf(dest).call()/1e6:.6f}")
    return 0 if rcpt.status == 1 else 1


if __name__ == "__main__":
    sys.exit(main())
