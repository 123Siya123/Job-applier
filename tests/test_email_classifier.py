"""EmailClassifier — regex layer can be tested without Gemini."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.monitoring.email_classifier import EmailClassifier
from src.persistence.models import ApplicationStatus


def test_regex_detects_german_rejection():
    cl = EmailClassifier(MagicMock())
    assert (
        cl.regex_status(subject="Ihre Bewerbung", body="Leider können wir Sie nicht weiterverfolgen.")
        == ApplicationStatus.REJECTED
    )


def test_regex_detects_interview_invitation():
    cl = EmailClassifier(MagicMock())
    assert (
        cl.regex_status(subject="Einladung zum Gespräch", body="Wir würden Sie gerne kennenlernen.")
        == ApplicationStatus.INTERVIEW_INVITATION
    )


def test_regex_detects_confirmation():
    cl = EmailClassifier(MagicMock())
    assert (
        cl.regex_status(
            subject="Eingang Ihrer Bewerbung",
            body="Wir haben Ihre Bewerbung erhalten und werden uns melden.",
        )
        == ApplicationStatus.CONFIRMED
    )


def test_regex_returns_none_on_unrelated():
    cl = EmailClassifier(MagicMock())
    assert cl.regex_status(subject="Newsletter", body="Hi there!") is None
