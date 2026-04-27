"""MonitoringAgent — runs every `EMAIL_POLL_INTERVAL_MINUTES` minutes.

For each unprocessed message:
    1. Skip if already in `email_logs` (idempotency).
    2. Build a candidate set: applications applied to in the last 60 days.
    3. Classify via `EmailClassifier`.
    4. If matched + confident → update status, record in `email_logs`.
    5. Otherwise → still record so we don't reprocess on next poll.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..brain.gemini_client import GeminiClient
from ..persistence.models import Application, ApplicationStatus, Company, Job
from ..persistence.repository import Repository
from ..utils.events import EventBus, get_event_bus
from ..utils.logger import get_logger
from .email_classifier import EmailClassifier
from .imap_client import ImapClient

_log = get_logger("agent.monitoring")


class MonitoringAgent:
    def __init__(
        self,
        *,
        repo: Repository,
        gemini: GeminiClient,
        imap: ImapClient | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.repo = repo
        self.imap = imap or ImapClient()
        self.classifier = EmailClassifier(gemini)
        self.events = event_bus or get_event_bus()

    async def run_once(self, *, since_hours: int = 6) -> int:
        messages = await self.imap.fetch_recent(since_hours=since_hours, limit=200)
        if not messages:
            return 0

        candidates = await self._candidate_applications()

        processed = 0
        async with self.repo.session() as s:
            already = set()
            for m in messages:
                if await self.repo.email_already_processed(s, m.uid):
                    already.add(m.uid)

        for m in messages:
            if m.uid in already:
                continue
            intent = await self.classifier.classify(
                sender=m.sender,
                subject=m.subject,
                body=m.body_plain,
                candidates=candidates,
            )

            async with self.repo.session() as s:
                if (
                    intent.detected_status is not None
                    and intent.matched_application_id
                    and intent.confidence >= 0.6
                ):
                    await self.repo.update_application_status(
                        s,
                        intent.matched_application_id,
                        intent.detected_status,
                        from_email_uid=m.uid,
                    )
                    await self.events.publish(
                        {
                            "agent": "monitoring",
                            "event": "status_updated",
                            "application_id": intent.matched_application_id,
                            "new_status": intent.detected_status.value,
                            "subject": m.subject,
                        }
                    )
                await self.repo.record_email(
                    s,
                    message_uid=m.uid,
                    sender=m.sender,
                    subject=m.subject,
                    received_at=m.received_at,
                    matched_application_id=intent.matched_application_id,
                    detected_status=intent.detected_status,
                    raw_excerpt=m.body_plain[:1000],
                )
                processed += 1

        _log.info("monitoring_done", processed=processed)
        return processed

    async def _candidate_applications(self) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=60)
        async with self.repo.session() as s:
            stmt = (
                select(Application, Job, Company)
                .join(Job, Application.job_id == Job.id)
                .join(Company, Job.company_id == Company.id)
                .where(
                    Application.status.in_(
                        (
                            ApplicationStatus.APPLIED,
                            ApplicationStatus.CONFIRMED,
                        )
                    )
                )
                .where(Application.applied_at.is_(None) | (Application.applied_at >= cutoff))
                .order_by(Application.applied_at.desc().nullslast())
                .limit(80)
            )
            rows = (await s.execute(stmt)).all()
        return [
            {
                "id": app.id,
                "company": company.name,
                "title": job.title,
                "applied_at": app.applied_at.isoformat() if app.applied_at else None,
            }
            for app, job, company in rows
        ]
