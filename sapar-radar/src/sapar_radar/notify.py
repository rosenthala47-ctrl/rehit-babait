"""Delivery channels. Every one is optional and fails soft."""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

#: Telegram rejects messages longer than this.
TELEGRAM_LIMIT = 4096


def send_telegram(text: str) -> bool:
    """Post the summary to a Telegram chat. Needs TELEGRAM_BOT_TOKEN/CHAT_ID."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.warning("telegram not configured (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok = True
    for chunk in _chunks(text, TELEGRAM_LIMIT):
        try:
            response = httpx.post(
                url,
                json={"chat_id": chat_id, "text": chunk,
                      "disable_web_page_preview": True},
                timeout=20.0,
            )
            if response.status_code >= 400:
                log.error("telegram error %s: %s",
                          response.status_code, response.text[:200])
                ok = False
        except httpx.HTTPError as exc:
            log.error("telegram request failed: %s", exc)
            ok = False
    return ok


def send_email(subject: str, body: str, attachments: list[Path] | None = None) -> bool:
    """Email the summary, optionally attaching the CSV."""
    host = os.environ.get("SMTP_HOST", "").strip()
    to_addr = os.environ.get("EMAIL_TO", "").strip()
    if not host or not to_addr:
        log.warning("email not configured (SMTP_HOST/EMAIL_TO)")
        return False

    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    port = int(os.environ.get("SMTP_PORT", "587"))

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ.get("EMAIL_FROM", user or to_addr)
    message["To"] = to_addr
    message.set_content(body)

    for path in attachments or []:
        if not path.exists():
            continue
        message.add_attachment(
            path.read_bytes(),
            maintype="text",
            subtype="csv",
            filename=path.name,
        )

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        log.error("email send failed: %s", exc)
        return False
    return True


def _chunks(text: str, size: int) -> list[str]:
    """Split on line boundaries so a shop's entry is never cut in half."""
    if len(text) <= size:
        return [text]
    out: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > size:
            out.append(current)
            current = ""
        current += line
    if current:
        out.append(current)
    return out
