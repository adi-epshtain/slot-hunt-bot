"""Outbound e-mail alerts (push). OUTPUT ONLY — the user talks to the bot via the web
chat, never by e-mail. We e-mail her when a slot is found (possibly days later) since
she won't be watching the page.

Standard library only (smtplib). Works with a dedicated Gmail + App Password.
Env: BOT_EMAIL, BOT_EMAIL_APP_PASSWORD, MY_EMAIL, optional SMTP_HOST / SMTP_PORT.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

log = logging.getLogger("notifier")


class EmailNotifier:
    def __init__(
        self,
        bot_email: str,
        app_password: str,
        my_email: str,
        smtp_host: str = "smtp.gmail.com",
        smtp_port: int = 587,
    ):
        self.bot_email = bot_email
        self.app_password = app_password
        self.my_email = my_email
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self._enabled = bool(bot_email and app_password and my_email)
        if not self._enabled:
            log.warning("EmailNotifier disabled — set BOT_EMAIL/APP_PASSWORD/MY_EMAIL")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send(self, text: str, subject: str = "בוט תורים · עדכון") -> bool:
        if not self._enabled:
            log.info("[EMAIL - not sent] %s", text)
            return False
        try:
            msg = EmailMessage()
            msg["From"] = self.bot_email
            msg["To"] = self.my_email
            msg["Subject"] = subject
            msg.set_content(text)
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as s:
                s.starttls()
                s.login(self.bot_email, self.app_password)
                s.send_message(msg)
            log.info("email sent: %s", text[:80])
            return True
        except Exception as e:
            log.error("email send failed: %s", e)
            return False
