"""Phase 4: Email monitoring."""

from .email_classifier import EmailIntent, EmailClassifier
from .imap_client import EmailMessage, ImapClient
from .monitoring_agent import MonitoringAgent

__all__ = [
    "EmailClassifier",
    "EmailIntent",
    "EmailMessage",
    "ImapClient",
    "MonitoringAgent",
]
