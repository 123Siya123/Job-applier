"""ORM models — the core dictionary the system maintains.

Maps to the user spec:
    {"Unternehmen": "Beispiel GmbH",
     "Jobs": [{"Titel": "Werkstudent Mechatronik", "Status": "Applied"}]}

extended with everything needed for monitoring, dashboard and idempotent
batch processing.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class CompanyStatus(str, enum.Enum):
    """Where this company sits in the funnel."""

    DISCOVERED = "discovered"               # found by sourcing, not yet processed
    QUALIFIED = "qualified"                 # passed filtering, ready for application
    IN_PROGRESS = "in_progress"             # application agent currently working on it
    APPLIED_TO_SOMETHING = "applied"        # at least one job applied to
    SCHON_FUER_ALLES_BEWORBEN = "schon_fuer_alles_beworben"  # exhausted on this company
    SKIPPED = "skipped"                     # filtered out (blacklist, no careers page, etc.)
    ERROR = "error"                         # unrecoverable failure


class CompanySize(str, enum.Enum):
    UNKNOWN = "unknown"
    STARTUP = "startup"
    SME = "sme"
    LARGE = "large"
    ENTERPRISE = "enterprise"


class ApplicationStatus(str, enum.Enum):
    """Per-job status — the values the user spec calls out explicitly."""

    NEW = "new"
    APPLIED = "applied"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    INTERVIEW_INVITATION = "interview_invitation"
    OFFER = "offer"
    WITHDRAWN = "withdrawn"
    FAILED = "failed"                  # we couldn't submit (broken portal, captcha, …)


class EventType(str, enum.Enum):
    DISCOVERED = "discovered"
    QUALIFIED = "qualified"
    APPLICATION_STARTED = "application_started"
    APPLICATION_SUBMITTED = "application_submitted"
    APPLICATION_FAILED = "application_failed"
    EMAIL_RECEIVED = "email_received"
    STATUS_CHANGED = "status_changed"
    NOTE = "note"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    careers_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    location_city: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    location_country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size: Mapped[CompanySize] = mapped_column(
        Enum(CompanySize), default=CompanySize.UNKNOWN, nullable=False
    )
    status: Mapped[CompanyStatus] = mapped_column(
        Enum(CompanyStatus), default=CompanyStatus.DISCOVERED, nullable=False, index=True
    )
    sourcing_query: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sourcing_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    jobs: Mapped[list[Job]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    job_type: Mapped[str | None] = mapped_column(String(64), nullable=True)  # Praktikum/WS/...
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_cover_letter: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_html_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)

    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    company: Mapped[Company] = relationship(back_populates="jobs")
    applications: Mapped[list[Application]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("company_id", "title", "url", name="uq_job_company_title_url"),
        Index("ix_job_title_company", "title", "company_id"),
    )


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus),
        default=ApplicationStatus.NEW,
        nullable=False,
        index=True,
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    interview_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    cover_letter: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON dump
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    portal_confirmation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    job: Mapped[Job] = relationship(back_populates="applications")
    events: Mapped[list[ApplicationEvent]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class ApplicationEvent(Base):
    """Append-only event log for the dashboard timeline + audit."""

    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=True, index=True
    )
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_type: Mapped[EventType] = mapped_column(Enum(EventType), nullable=False, index=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    application: Mapped[Application | None] = relationship(back_populates="events")


class EmailLog(Base):
    """Inbox messages we've already processed — keeps monitoring idempotent."""

    __tablename__ = "email_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_uid: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    sender: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(1024), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    matched_application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"), nullable=True
    )
    detected_status: Mapped[ApplicationStatus | None] = mapped_column(
        Enum(ApplicationStatus), nullable=True
    )
    raw_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
