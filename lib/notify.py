"""Notification sender — Telegram (primary) + email (fallback).

Telegram setup:
1. Message @BotFather on Telegram, send /newbot, follow prompts
2. Copy the bot token to .env as TELEGRAM_BOT_TOKEN
3. Message your bot, then visit:
   https://api.telegram.org/bot<TOKEN>/getUpdates
4. Find your chat_id in the response, add to .env as TELEGRAM_CHAT_ID

The bot sends alerts for: trade placed, daily P&L, risk gate breaches, errors.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import os
import smtplib
import logging
from email.mime.text import MIMEText
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

try:
    import requests as _requests
except ImportError:
    _requests = None

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    """Send a Telegram message. Returns True on success."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id or not _requests:
        return False

    try:
        resp = _requests.post(
            TELEGRAM_API.format(token=token),
            json={"chat_id": chat_id, "text": message, "parse_mode": parse_mode},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.debug("Telegram sent: %s", message[:50])
            return True
        else:
            logger.warning("Telegram failed (%d): %s", resp.status_code, resp.text[:100])
            return False
    except Exception as e:
        logger.warning("Telegram error: %s", e)
        return False


def send_email(subject: str, body: str) -> bool:
    """Send email notification. Returns True on success."""
    try:
        host = os.getenv("SMTP_HOST")
        port = int(os.getenv("SMTP_PORT", "587"))
        user = os.getenv("SMTP_USER")
        password = os.getenv("SMTP_PASSWORD")
        to_addr = os.getenv("NOTIFY_EMAIL")

        if not all([host, user, password, to_addr]):
            return False

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = to_addr

        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, [to_addr], msg.as_string())

        logger.info("Email sent: %s", subject)
        return True
    except Exception as e:
        logger.warning("Email failed: %s", e)
        return False


def send_notification(subject: str, body: str) -> bool:
    """Send via Telegram first, fall back to email."""
    # Try Telegram
    telegram_msg = f"<b>{subject}</b>\n\n{body}"
    if send_telegram(telegram_msg):
        return True
    # Fall back to email
    return send_email(subject, body)


# ============================================================
# Pre-built alert messages for the trading agent
# ============================================================

def alert_trade_placed(trade: dict) -> None:
    """Alert when a trade is placed."""
    side = trade.get("side", "?")
    ticker = trade.get("ticker", "?")
    qty = trade.get("quantity", 0)
    price = trade.get("price", 0)
    cost = trade.get("cost_usd", price * qty)
    strategy = trade.get("strategy", "")
    status = trade.get("status", "")

    icon = "BUY" if side == "BUY" else "SELL"
    msg = (
        f"<b>{icon} {ticker}</b>\n"
        f"{qty:.2f} shares @ ${price:,.2f} = ${cost:,.2f}\n"
        f"Strategy: {strategy}\n"
        f"Status: {status}"
    )
    if trade.get("stop_price"):
        msg += f"\nStop: ${trade['stop_price']:,.2f}"
    if trade.get("take_profit"):
        msg += f"\nTarget: ${trade['take_profit']:,.2f}"

    send_telegram(msg)


def alert_options_trade(trade: dict) -> None:
    """Alert when an options trade is placed."""
    side = trade.get("side", "?")
    occ = trade.get("ticker", trade.get("occ_symbol", "?"))
    qty = trade.get("quantity", 0)
    premium = trade.get("price", 0)
    strategy = trade.get("strategy", "")

    msg = (
        f"<b>OPTIONS {side} {occ}</b>\n"
        f"{qty} contracts @ ${premium:.2f} (${premium * 100 * qty:,.0f})\n"
        f"Strategy: {strategy}"
    )
    send_telegram(msg)


def alert_daily_summary(run_result: dict) -> None:
    """Send end-of-day summary."""
    proposals = run_result.get("proposals", 0)
    approved = run_result.get("approved", 0)
    executed = run_result.get("executed", 0)
    deployed = run_result.get("total_deployed", 0)
    portfolio = run_result.get("portfolio_value", 0)

    msg = (
        f"<b>EOD Summary</b>\n"
        f"Portfolio: ${portfolio:,.2f}\n"
        f"Proposals: {proposals} found, {approved} approved, {executed} executed\n"
        f"Deployed: ${deployed:,.2f}"
    )

    strategies = run_result.get("strategies", {})
    if strategies:
        msg += "\n\nBy strategy:"
        for s, count in strategies.items():
            msg += f"\n  {s}: {count}"

    send_telegram(msg)


def alert_risk_breach(breach_type: str, details: str) -> None:
    """Alert on risk gate breach — daily loss, drawdown, etc."""
    msg = (
        f"<b>RISK ALERT: {breach_type}</b>\n\n"
        f"{details}\n\n"
        f"Trading may be paused. Check the agent."
    )
    send_telegram(msg)


def alert_error(error_type: str, details: str) -> None:
    """Alert on agent errors."""
    msg = (
        f"<b>ERROR: {error_type}</b>\n\n"
        f"{details}"
    )
    send_telegram(msg)
