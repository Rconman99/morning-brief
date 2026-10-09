"""Cash out the bot wallet's pUSD: swap to USDC on Uniswap V3, optionally send it on.

Background: pUSD is Polymarket's collateral token on Polygon. Its `unwrap()` is
restricted to Polymarket's own contracts, and the official withdraw flow runs
through polymarket.com (blocked for US users). A Uniswap V3 pUSD/USDC pool
(0.01% fee tier) quotes ~1:1, so that's the exit.

Usage (from /opt/morning-brief):
    .venv/bin/python agent/cash_out_pusd.py                         # DRY RUN: quote only
    .venv/bin/python agent/cash_out_pusd.py --execute               # swap pUSD -> USDC (stays in bot wallet)
    .venv/bin/python agent/cash_out_pusd.py --execute --send-to 0xYourExchangeDepositAddr
                                                                    # ...then send the USDC there

Safety rails:
- Dry run by default; --execute is required to sign anything.
- Refuses if the quote is worse than 98 cents on the dollar.
- Slippage floor: 0.5% below the quote (amountOutMinimum), 10-minute deadline.
- --send-to must be a checksummed address; the script asks you to confirm it
  supports **USDC on the Polygon network** (a wrong network = lost funds).
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import os
import time

PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
USDC = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"          # native USDC (Circle) on Polygon
QUOTER = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"        # Uniswap V3 QuoterV2
ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"        # Uniswap V3 SwapRouter
FEE = 100                                                    # 0.01% pool
RPC = os.environ.get("POLYGON_RPC", "https://polygon-bor-rpc.publicnode.com")

ERC20 = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "", "type": "address"}], "outputs": [{"type": "uint256"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "", "type": "address"}, {"name": "", "type": "address"}], "outputs": [{"type": "uint256"}]},
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [{"type": "bool"}]},
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [{"type": "bool"}]},
]
QUOTER_ABI = [{"name": "quoteExactInputSingle", "type": "function", "stateMutability": "nonpayable",
    "inputs": [{"name": "params", "type": "tuple", "components": [
        {"name": "tokenIn", "type": "address"}, {"name": "tokenOut", "type": "address"},
        {"name": "amountIn", "type": "uint256"}, {"name": "fee", "type": "uint24"},
        {"name": "sqrtPriceLimitX96", "type": "uint160"}]}],
    "outputs": [{"name": "amountOut", "type": "uint256"}, {"name": "sqrtPriceX96After", "type": "uint160"},
                {"name": "initializedTicksCrossed", "type": "uint32"}, {"name": "gasEstimate", "type": "uint256"}]}]
ROUTER_ABI = [{"name": "exactInputSingle", "type": "function", "stateMutability": "payable",
    "inputs": [{"name": "params", "type": "tuple", "components": [
        {"name": "tokenIn", "type": "address"}, {"name": "tokenOut", "type": "address"},
        {"name": "fee", "type": "uint24"}, {"name": "recipient", "type": "address"},
        {"name": "deadline", "type": "uint256"}, {"name": "amountIn", "type": "uint256"},
        {"name": "amountOutMinimum", "type": "uint256"}, {"name": "sqrtPriceLimitX96", "type": "uint160"}]}],
    "outputs": [{"name": "amountOut", "type": "uint256"}]}]


def load_env():
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def send(w3, acct, fn, label):
    tx = fn.build_transaction({"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address), "chainId": 137})
    tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.3)
    signed = acct.sign_transaction(tx)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"{label}: https://polygonscan.com/tx/{txh.hex()}")
    rcpt = w3.eth.wait_for_transaction_receipt(txh, timeout=240)
    if rcpt.status != 1:
        raise SystemExit(f"{label} FAILED on-chain — stopping.")
    print(f"{label}: confirmed in block {rcpt.blockNumber}")
    return rcpt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="Sign and send (default: dry run)")
    ap.add_argument("--send-to", default="", help="After the swap, transfer the USDC to this Polygon address")
    ap.add_argument("--amount", type=float, default=0.0, help="pUSD to swap (default: full balance)")
    args = ap.parse_args()

    load_env()
    from web3 import Web3
    from eth_account import Account

    key = os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip()
    if not key:
        print("No POLYMARKET_PRIVATE_KEY in .env"); return 2
    acct = Account.from_key(key)
    w3 = Web3(Web3.HTTPProvider(RPC))
    pusd = w3.eth.contract(address=Web3.to_checksum_address(PUSD), abi=ERC20)
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC), abi=ERC20)
    quoter = w3.eth.contract(address=Web3.to_checksum_address(QUOTER), abi=QUOTER_ABI)
    router = w3.eth.contract(address=Web3.to_checksum_address(ROUTER), abi=ROUTER_ABI)

    bal = pusd.functions.balanceOf(acct.address).call()
    usdc_before = usdc.functions.balanceOf(acct.address).call()
    pol = float(w3.from_wei(w3.eth.get_balance(acct.address), "ether"))
    amount = int(round(args.amount * 1e6)) if args.amount > 0 else bal

    print(f"wallet:        {acct.address}")
    print(f"pUSD balance:  {bal/1e6:.6f}    USDC balance: {usdc_before/1e6:.6f}    POL: {pol:.4f}")
    if amount <= 0 or amount > bal:
        print("Nothing to swap / amount exceeds balance."); return 2
    if pol < 0.05:
        print("Not enough POL for gas (need ~0.05)."); return 2

    quote = quoter.functions.quoteExactInputSingle((pusd.address, usdc.address, amount, FEE, 0)).call()[0]
    print(f"quote:         {amount/1e6:.6f} pUSD -> {quote/1e6:.6f} USDC  ({quote/amount*100:.3f}%)")
    if quote < amount * 0.98:
        print("REFUSING: quote is worse than 98% — pool liquidity changed. Try again later or a smaller --amount."); return 2
    min_out = int(quote * 0.995)

    dest = None
    if args.send_to:
        if not Web3.is_checksum_address(args.send_to):
            print("--send-to must be a checksummed 0x address (copy it exactly from the exchange's deposit page)."); return 2
        dest = Web3.to_checksum_address(args.send_to)
        print(f"then send USDC to: {dest}")
        print("  >> This address MUST be a USDC deposit address on the POLYGON network at your exchange. <<")

    if not args.execute:
        print("\nDRY RUN — nothing sent. Re-run with --execute to do it."); return 0

    if dest:
        ans = input("Type the last 4 characters of the destination address to confirm: ").strip()
        if ans.lower() != dest[-4:].lower():
            print("Mismatch — aborting."); return 2

    # 1) approve router (exact amount)
    if pusd.functions.allowance(acct.address, router.address).call() < amount:
        send(w3, acct, pusd.functions.approve(router.address, amount), "approve")

    # 2) swap
    deadline = int(time.time()) + 600
    send(w3, acct, router.functions.exactInputSingle(
        (pusd.address, usdc.address, FEE, acct.address, deadline, amount, min_out, 0)), "swap")
    usdc_after = usdc.functions.balanceOf(acct.address).call()
    got = usdc_after - usdc_before
    print(f"received:      {got/1e6:.6f} USDC   (wallet USDC now {usdc_after/1e6:.6f})")

    # 3) optional transfer
    if dest:
        send(w3, acct, usdc.functions.transfer(dest, usdc_after), "transfer")
        print(f"sent {usdc_after/1e6:.6f} USDC to {dest}. Check the exchange for the deposit (Polygon network).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
