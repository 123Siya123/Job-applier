"""Repository tests against an in-memory SQLite instance."""

from __future__ import annotations

import pytest

from src.persistence.db import init_db
from src.persistence.models import ApplicationStatus, CompanyStatus
from src.persistence.repository import Repository


@pytest.fixture(autouse=True)
async def _setup_db():
    await init_db("sqlite+aiosqlite:///:memory:")
    yield


async def test_upsert_company_is_idempotent():
    repo = Repository()
    async with repo.session() as s:
        c1 = await repo.upsert_company(s, name="Foo GmbH", domain="foo.de")
    async with repo.session() as s:
        c2 = await repo.upsert_company(s, name="Foo GmbH", careers_url="https://foo.de/jobs")
    assert c1.id == c2.id


async def test_status_transitions_and_event_log():
    repo = Repository()
    async with repo.session() as s:
        c = await repo.upsert_company(s, name="Bar AG")
        await repo.set_company_status(s, c.id, CompanyStatus.QUALIFIED)
        await repo.set_company_status(s, c.id, CompanyStatus.IN_PROGRESS)

    async with repo.session() as s:
        events = await repo.recent_events(s, limit=10)
    # 1 discovered + 2 status_changed + …
    assert any("status_changed" in e.event_type.value for e in events)
    assert len(events) >= 3


async def test_application_lifecycle():
    repo = Repository()
    async with repo.session() as s:
        company = await repo.upsert_company(s, name="Beispiel GmbH")
        job = await repo.upsert_job(s, company_id=company.id, title="Werkstudent")
        app = await repo.create_application(s, job_id=job.id)
        await repo.mark_application_submitted(
            s, app.id, cover_letter="Sehr geehrte…", portal_confirmation_id="REF-42"
        )
        await repo.update_application_status(
            s, app.id, ApplicationStatus.INTERVIEW_INVITATION, from_email_uid="42"
        )

    async with repo.session() as s:
        counts = await repo.aggregate_status_counts(s)
    assert counts.get(ApplicationStatus.INTERVIEW_INVITATION.value) == 1


async def test_email_idempotency():
    from datetime import datetime, timezone

    repo = Repository()
    async with repo.session() as s:
        assert not await repo.email_already_processed(s, "uid-1")
        await repo.record_email(
            s,
            message_uid="uid-1",
            sender="hr@foo.de",
            subject="Bestätigung",
            received_at=datetime.now(timezone.utc),
            matched_application_id=None,
            detected_status=None,
            raw_excerpt=None,
        )
    async with repo.session() as s:
        assert await repo.email_already_processed(s, "uid-1")
