"""Email notification sender. Fails silently if not configured."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import os
import smtplib
import logging
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")
logger = logging.getLogger(__name__)


def send_notification(subject: str, body: str) -> bool:
    """Send email notification. Returns True on success, False otherwise."""
    try:
        host = os.getenv("SMTP_HOST")
        port = int(os.getenv("SMTP_PORT", "587"))
        user = os.getenv("SMTP_USER")
        password = os.getenv("SMTP_PASSWORD")
        to_addr = os.getenv("NOTIFY_EMAIL")

        if not all([host, user, password, to_addr]):
            logger.info("SMTP not configured, skipping notification")
            return False

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = to_addr

        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, [to_addr], msg.as_string())

        logger.info("Notification sent: %s", subject)
        return True
    except Exception as e:
        logger.warning("Failed to send notification: %s", e)
        return False
