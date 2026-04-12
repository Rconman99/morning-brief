"""Telegram command bot for the trading agent.

Simple HTTP polling bot that responds to commands from Ryan's Telegram.
Uses TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from .env.

Run standalone: .venv/bin/python agent/telegram_bot.py
"""

import sys
import time
import logging
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

import os
import requests

from agent.equity_executor import get_account, get_positions, close_all_positions
from agent.equity_db import get_trades, get_performance_metrics
from lib.notify import send_telegram

logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
POLL_INTERVAL = 5  # seconds

# Track kill switch state — requires two-step confirmation
_kill_pending = False
_kill_pending_time = 0


def _send_reply(text: str) -> bool:
    """Send a reply message to the authorized chat."""
    return send_telegram(text, parse_mode="HTML")


def _cmd_status() -> str:
    """Show account equity, buying power, open position count, today's P&L."""
    acct = get_account()
    positions = get_positions()

    equity = acct.get("equity", 0)
    buying_power = acct.get("buying_power", 0)
    cash = acct.get("cash", 0)
    status = acct.get("status", "unknown")

    # Today's P&L from unrealized positions
    todays_pnl = sum(p.get("unrealized_pnl", 0) for p in positions)

    return (
        f"<b>Account Status</b>\n"
        f"Status: {status}\n"
        f"Equity: ${equity:,.2f}\n"
        f"Buying Power: ${buying_power:,.2f}\n"
        f"Cash: ${cash:,.2f}\n"
        f"Open Positions: {len(positions)}\n"
        f"Unrealized P&L: ${todays_pnl:,.2f}"
    )


def _cmd_positions() -> str:
    """List all open Alpaca positions with ticker, qty, avg cost, P&L."""
    positions = get_positions()

    if not positions:
        return "<b>Positions</b>\nNo open positions."

    lines = ["<b>Open Positions</b>\n"]
    total_pnl = 0
    for p in positions:
        ticker = p.get("ticker", "?")
        qty = p.get("qty", 0)
        avg_cost = p.get("avg_cost", 0)
        pnl = p.get("unrealized_pnl", 0)
        pnl_pct = p.get("unrealized_pnl_pct", 0)
        current = p.get("current_price", 0)
        total_pnl += pnl

        sign = "+" if pnl >= 0 else ""
        lines.append(
            f"<b>{ticker}</b> {qty:.2f} shares\n"
            f"  Avg: ${avg_cost:.2f} | Now: ${current:.2f}\n"
            f"  P&L: {sign}${pnl:,.2f} ({sign}{pnl_pct:.1%})"
        )

    lines.append(f"\n<b>Total P&L: ${total_pnl:,.2f}</b>")
    return "\n".join(lines)


def _cmd_trades() -> str:
    """Show last 5 trades from SQLite."""
    trades = get_trades(days=30)[:5]

    if not trades:
        return "<b>Recent Trades</b>\nNo trades in the last 30 days."

    lines = ["<b>Last 5 Trades</b>\n"]
    for t in trades:
        ticker = t.get("ticker", "?")
        side = t.get("side", "?")
        price = t.get("price", 0)
        fill = t.get("fill_price")
        status = t.get("status", "?")
        ts = t.get("timestamp", "")[:16]  # trim to minute

        price_str = f"${fill:.2f}" if fill else f"${price:.2f}"
        lines.append(f"{ts} | {side} {ticker} @ {price_str} [{status}]")

    return "\n".join(lines)


def _cmd_performance() -> str:
    """Show 30-day metrics: trade count, Sharpe, total deployed."""
    metrics = get_performance_metrics(days=30)

    trade_count = metrics.get("trades", 0)
    sharpe = metrics.get("sharpe", 0)
    buys = metrics.get("buys", 0)
    sells = metrics.get("sells", 0)

    # Calculate total deployed from strategy breakdown
    by_strat = metrics.get("by_strategy", {})
    total_deployed = sum(s.get("total_cost", 0) for s in by_strat.values())

    lines = [
        f"<b>30-Day Performance</b>\n",
        f"Trades: {trade_count} ({buys} buys, {sells} sells)",
        f"Sharpe Ratio: {sharpe:.2f}",
        f"Total Deployed: ${total_deployed:,.2f}",
    ]

    if by_strat:
        lines.append("\n<b>By Strategy:</b>")
        for name, data in by_strat.items():
            lines.append(f"  {name}: {data['count']} trades, ${data['total_cost']:,.0f}")

    return "\n".join(lines)


def _cmd_kill() -> str:
    """First step of kill switch — ask for confirmation."""
    global _kill_pending, _kill_pending_time
    _kill_pending = True
    _kill_pending_time = time.time()
    return (
        "<b>KILL SWITCH</b>\n\n"
        "This will close ALL positions and cancel ALL orders.\n\n"
        "Send /kill_confirm within 30 seconds to execute."
    )


def _cmd_kill_confirm() -> str:
    """Execute the kill switch if confirmation is recent."""
    global _kill_pending, _kill_pending_time

    if not _kill_pending:
        return "No pending kill switch. Send /kill first."

    elapsed = time.time() - _kill_pending_time
    _kill_pending = False

    if elapsed > 30:
        return "Kill confirmation expired (>30s). Send /kill again."

    results = close_all_positions()
    status = results[0].get("status", "unknown") if results else "no response"

    if status == "all_closed":
        return "<b>KILL SWITCH EXECUTED</b>\nAll positions closed. All orders cancelled."
    else:
        error = results[0].get("error", "") if results else ""
        return f"<b>KILL SWITCH RESULT:</b> {status}\n{error}"


def _cmd_help() -> str:
    """List all commands."""
    return (
        "<b>Trading Bot Commands</b>\n\n"
        "/status — Account equity, buying power, P&L\n"
        "/positions — All open positions with P&L\n"
        "/trades — Last 5 trades\n"
        "/performance — 30-day metrics + Sharpe\n"
        "/kill — Emergency: close everything (requires /kill_confirm)\n"
        "/help — This message"
    )


# Map commands to handlers
COMMANDS = {
    "/status": _cmd_status,
    "/positions": _cmd_positions,
    "/trades": _cmd_trades,
    "/performance": _cmd_performance,
    "/kill": _cmd_kill,
    "/kill_confirm": _cmd_kill_confirm,
    "/help": _cmd_help,
    "/start": _cmd_help,  # Telegram sends /start on first interaction
}


def poll_updates(offset: int = 0) -> tuple[list, int]:
    """Fetch new messages from Telegram. Returns (messages, new_offset)."""
    try:
        resp = requests.get(
            f"{API_BASE}/getUpdates",
            params={"offset": offset, "timeout": POLL_INTERVAL},
            timeout=POLL_INTERVAL + 5,
        )
        if resp.status_code != 200:
            logger.warning("getUpdates failed: %d", resp.status_code)
            return [], offset

        data = resp.json()
        if not data.get("ok"):
            return [], offset

        results = data.get("result", [])
        if not results:
            return [], offset

        # Advance offset past the last update we received
        new_offset = results[-1]["update_id"] + 1
        return results, new_offset

    except requests.exceptions.Timeout:
        return [], offset
    except Exception as e:
        logger.error("Poll error: %s", e)
        return [], offset


def handle_update(update: dict) -> None:
    """Process a single Telegram update."""
    msg = update.get("message", {})
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").strip()

    if not text or not chat_id:
        return

    # Security: only respond to authorized chat
    if chat_id != CHAT_ID:
        logger.warning("Unauthorized message from chat_id=%s: %s", chat_id, text[:50])
        return

    # Extract the command (ignore @bot_name suffix and args)
    command = text.split()[0].split("@")[0].lower()

    handler = COMMANDS.get(command)
    if handler:
        try:
            reply = handler()
            _send_reply(reply)
        except Exception as e:
            logger.error("Command %s failed: %s", command, e)
            _send_reply(f"Error running {command}: {e}")
    else:
        _send_reply(f"Unknown command: {command}\nSend /help for available commands.")


def main():
    """Main polling loop."""
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)
    if not CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID not set in .env")
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    logger.info("Telegram bot starting — polling every %ds", POLL_INTERVAL)
    logger.info("Authorized chat ID: %s", CHAT_ID)

    offset = 0

    # Announce startup
    _send_reply("Trading bot online. Send /help for commands.")

    while True:
        try:
            updates, offset = poll_updates(offset)
            for update in updates:
                handle_update(update)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            _send_reply("Trading bot going offline.")
            break
        except Exception as e:
            logger.error("Main loop error: %s", e)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
