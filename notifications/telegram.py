"""Telegram notification helpers for Spider4AI alerts."""

from __future__ import annotations

import logging

import requests

from config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram_message(text: str) -> bool:
    """Send a plain text message to the configured Telegram chat.

    Returns ``False`` (and logs) when Telegram is not configured or the send
    fails; never raises, so alerting never breaks the trading loop.
    """
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.info("Telegram not configured; notification skipped: %s", text)
        return False
    try:
        response = requests.post(
            TELEGRAM_API_URL.format(token=settings.telegram_bot_token),
            json={"chat_id": settings.telegram_chat_id, "text": text},
            timeout=10,
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Telegram notification failed: %s", exc)
        return False
