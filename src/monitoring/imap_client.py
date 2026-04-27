"""IMAP wrapper around `imap-tools`.

We pull recent unprocessed messages, expose them as `EmailMessage` dataclasses
and let the agent decide what to do. We never delete or move messages — read-only.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..settings import get_settings
from ..utils.logger import get_logger

_log = get_logger("monitoring.imap")


@dataclass
class EmailMessage:
    uid: str
    sender: str
    sender_name: str
    subject: str
    body_plain: str
    received_at: datetime


class ImapClient:
    def __init__(self, *, host: str | None = None, port: int | None = None,
                 user: str | None = None, password: str | None = None) -> None:
        s = get_settings()
        self.host = host or s.email_imap_host
        self.port = port or s.email_imap_port
        self.user = user or s.email_imap_user
        self.password = password or s.email_imap_password

    async def fetch_recent(self, *, since_hours: int = 24, limit: int = 100) -> list[EmailMessage]:
        if not self.user or not self.password:
            _log.warning("imap_credentials_missing")
            return []
        return await asyncio.to_thread(self._sync_fetch, since_hours, limit)

    def _sync_fetch(self, since_hours: int, limit: int) -> list[EmailMessage]:
        from imap_tools import AND, MailBox

        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        out: list[EmailMessage] = []
        try:
            with MailBox(self.host, self.port).login(self.user, self.password, "INBOX") as mb:
                criteria = AND(date_gte=cutoff.date())
                for msg in mb.fetch(criteria=criteria, mark_seen=False, limit=limit, reverse=True):
                    out.append(
                        EmailMessage(
                            uid=str(msg.uid or ""),
                            sender=msg.from_ or "",
                            sender_name=msg.from_values.name if msg.from_values else "",
                            subject=msg.subject or "",
                            body_plain=msg.text or msg.html or "",
                            received_at=msg.date or datetime.now(timezone.utc),
                        )
                    )
        except Exception as exc:
            _log.warning("imap_fetch_failed", error=str(exc))
            return []
        return out
