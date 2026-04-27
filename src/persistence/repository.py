"""Repository — single point of access for DB writes/reads.

Agents never use sessions directly; they go through `Repository` so events
get logged consistently and idempotency is enforced in one place.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session_factory
from .models import (
    Application,
    ApplicationEvent,
    ApplicationStatus,
    Company,
    CompanySize,
    CompanyStatus,
    EmailLog,
    EventType,
    Job,
)


class Repository:
    """Thin domain-aware wrapper around an `AsyncSession`.

    Use it via the `session()` async context manager — every block runs in a
    single transaction.

    Example:
        repo = Repository()
        async with repo.session() as s:
            company = await repo.upsert_company(s, name="Foo GmbH")
    """

    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session_factory()

    @asynccontextmanager
    async def session(self):
        async with self._session_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    # ------------------------------------------------------------------ Company

    async def upsert_company(
        self,
        s: AsyncSession,
        *,
        name: str,
        domain: str | None = None,
        careers_url: str | None = None,
        location_city: str | None = None,
        location_country: str | None = None,
        industry: str | None = None,
        size: CompanySize = CompanySize.UNKNOWN,
        sourcing_query: str | None = None,
        sourcing_stage: str | None = None,
    ) -> Company:
        existing = (
            await s.execute(select(Company).where(Company.name == name))
        ).scalar_one_or_none()
        if existing:
            # patch missing fields, never overwrite a non-null value with null
            for field, value in {
                "domain": domain,
                "careers_url": careers_url,
                "location_city": location_city,
                "location_country": location_country,
                "industry": industry,
                "sourcing_query": sourcing_query,
                "sourcing_stage": sourcing_stage,
            }.items():
                if value and getattr(existing, field) in (None, ""):
                    setattr(existing, field, value)
            if size != CompanySize.UNKNOWN and existing.size == CompanySize.UNKNOWN:
                existing.size = size
            return existing

        company = Company(
            name=name,
            domain=domain,
            careers_url=careers_url,
            location_city=location_city,
            location_country=location_country,
            industry=industry,
            size=size,
            sourcing_query=sourcing_query,
            sourcing_stage=sourcing_stage,
            status=CompanyStatus.DISCOVERED,
        )
        s.add(company)
        await s.flush()
        await self.log_event(
            s,
            EventType.DISCOVERED,
            company_id=company.id,
            payload={"name": name, "stage": sourcing_stage},
        )
        return company

    async def set_company_status(
        self, s: AsyncSession, company_id: int, status: CompanyStatus, *, note: str | None = None
    ) -> None:
        company = await s.get(Company, company_id)
        if company is None:
            return
        old = company.status
        company.status = status
        company.last_processed_at = datetime.now(timezone.utc)
        if note:
            company.notes = (company.notes + "\n" if company.notes else "") + note
        await self.log_event(
            s,
            EventType.STATUS_CHANGED,
            company_id=company_id,
            payload={"from": old.value, "to": status.value, "note": note},
        )

    async def count_companies(self, s: AsyncSession, *statuses: CompanyStatus) -> int:
        q = select(func.count(Company.id))
        if statuses:
            q = q.where(Company.status.in_(statuses))
        return (await s.execute(q)).scalar_one()

    async def list_qualified_companies(
        self, s: AsyncSession, limit: int = 50
    ) -> list[Company]:
        q = (
            select(Company)
            .where(Company.status.in_(
                (CompanyStatus.DISCOVERED, CompanyStatus.QUALIFIED)
            ))
            .order_by(Company.discovered_at.asc())
            .limit(limit)
        )
        return list((await s.execute(q)).scalars().all())

    async def list_all_companies(self, s: AsyncSession) -> list[Company]:
        q = select(Company).order_by(Company.discovered_at.asc())
        return list((await s.execute(q)).scalars().all())

    # ------------------------------------------------------------------ Job

    async def upsert_job(
        self,
        s: AsyncSession,
        *,
        company_id: int,
        title: str,
        url: str | None = None,
        location: str | None = None,
        job_type: str | None = None,
        description: str | None = None,
        requires_cover_letter: bool = False,
    ) -> Job:
        existing = (
            await s.execute(
                select(Job).where(
                    Job.company_id == company_id,
                    Job.title == title,
                    Job.url == url,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return existing
        job = Job(
            company_id=company_id,
            title=title,
            url=url,
            location=location,
            job_type=job_type,
            description=description,
            requires_cover_letter=requires_cover_letter,
        )
        s.add(job)
        await s.flush()
        return job

    async def jobs_for_company(self, s: AsyncSession, company_id: int) -> list[Job]:
        q = select(Job).where(Job.company_id == company_id)
        return list((await s.execute(q)).scalars().all())

    # ------------------------------------------------------------ Application

    async def create_application(
        self, s: AsyncSession, *, job_id: int
    ) -> Application:
        existing = (
            await s.execute(select(Application).where(Application.job_id == job_id))
        ).scalar_one_or_none()
        if existing:
            return existing
        app = Application(job_id=job_id, status=ApplicationStatus.NEW)
        s.add(app)
        await s.flush()
        await self.log_event(
            s, EventType.APPLICATION_STARTED, application_id=app.id
        )
        return app

    async def mark_application_submitted(
        self,
        s: AsyncSession,
        application_id: int,
        *,
        cover_letter: str | None = None,
        submitted_payload: dict[str, Any] | None = None,
        portal_confirmation_id: str | None = None,
    ) -> None:
        app = await s.get(Application, application_id)
        if app is None:
            return
        app.status = ApplicationStatus.APPLIED
        app.applied_at = datetime.now(timezone.utc)
        app.cover_letter = cover_letter
        if submitted_payload is not None:
            app.submitted_payload = json.dumps(submitted_payload, ensure_ascii=False)
        app.portal_confirmation_id = portal_confirmation_id
        await self.log_event(
            s,
            EventType.APPLICATION_SUBMITTED,
            application_id=application_id,
            payload={"portal_confirmation_id": portal_confirmation_id},
        )

    async def mark_application_failed(
        self, s: AsyncSession, application_id: int, *, reason: str
    ) -> None:
        app = await s.get(Application, application_id)
        if app is None:
            return
        app.status = ApplicationStatus.FAILED
        app.failure_reason = reason
        await self.log_event(
            s,
            EventType.APPLICATION_FAILED,
            application_id=application_id,
            payload={"reason": reason},
        )

    async def update_application_status(
        self,
        s: AsyncSession,
        application_id: int,
        new_status: ApplicationStatus,
        *,
        from_email_uid: str | None = None,
    ) -> None:
        app = await s.get(Application, application_id)
        if app is None:
            return
        old = app.status
        app.status = new_status
        now = datetime.now(timezone.utc)
        if new_status == ApplicationStatus.CONFIRMED:
            app.confirmation_at = now
        elif new_status == ApplicationStatus.REJECTED:
            app.rejection_at = now
        elif new_status == ApplicationStatus.INTERVIEW_INVITATION:
            app.interview_at = now
        await self.log_event(
            s,
            EventType.STATUS_CHANGED,
            application_id=application_id,
            payload={
                "from": old.value,
                "to": new_status.value,
                "via_email_uid": from_email_uid,
            },
        )

    async def aggregate_status_counts(
        self, s: AsyncSession
    ) -> dict[str, int]:
        q = select(Application.status, func.count(Application.id)).group_by(Application.status)
        rows = (await s.execute(q)).all()
        return {status.value: count for status, count in rows}

    # ------------------------------------------------------------------ Events

    async def log_event(
        self,
        s: AsyncSession,
        event_type: EventType,
        *,
        application_id: int | None = None,
        company_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ApplicationEvent:
        ev = ApplicationEvent(
            application_id=application_id,
            company_id=company_id,
            event_type=event_type,
            payload=json.dumps(payload, ensure_ascii=False) if payload else None,
        )
        s.add(ev)
        await s.flush()
        return ev

    async def recent_events(self, s: AsyncSession, limit: int = 50) -> list[ApplicationEvent]:
        q = select(ApplicationEvent).order_by(ApplicationEvent.created_at.desc()).limit(limit)
        return list((await s.execute(q)).scalars().all())

    # ------------------------------------------------------------------ EmailLog

    async def email_already_processed(self, s: AsyncSession, message_uid: str) -> bool:
        q = select(EmailLog.id).where(EmailLog.message_uid == message_uid)
        return (await s.execute(q)).first() is not None

    async def record_email(
        self,
        s: AsyncSession,
        *,
        message_uid: str,
        sender: str,
        subject: str,
        received_at: datetime,
        matched_application_id: int | None,
        detected_status: ApplicationStatus | None,
        raw_excerpt: str | None,
    ) -> EmailLog:
        log = EmailLog(
            message_uid=message_uid,
            sender=sender,
            subject=subject,
            received_at=received_at,
            matched_application_id=matched_application_id,
            detected_status=detected_status,
            raw_excerpt=raw_excerpt,
        )
        s.add(log)
        await s.flush()
        return log
