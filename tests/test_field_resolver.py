"""FieldResolver — phone formatting + path mapping."""

from __future__ import annotations

from pathlib import Path

from src.application.field_resolver import FieldResolver
from src.brain.form_analyzer import PlannedField
from src.settings import (
    Address,
    EducationEntry,
    Personal,
    Preferences,
    Profile,
    ProfileFiles,
    Skills,
)


def _profile() -> Profile:
    return Profile(
        personal=Personal(
            first_name="Max",
            last_name="Mustermann",
            email="m@x.de",
            phone="0170 1234567",
            address=Address(
                street="Beispielstr. 1",
                postal_code="60311",
                city="Frankfurt am Main",
                country="Deutschland",
            ),
        ),
        education=[EducationEntry(degree="B.Sc.", institution="TU", start="2022-10")],
        skills=Skills(hard=["Python"]),
        files=ProfileFiles(cv_path="/tmp/cv.pdf"),
        self_description="Test",
        preferences=Preferences(earliest_start="2026-01-01", max_weekly_hours=20),
    )


def test_phone_formatting_with_plus_49():
    r = FieldResolver(_profile())
    field = PlannedField(kind="phone", label="Telefon", notes="Format: +49…")
    assert r.resolve(field) == "+491701234567"


def test_phone_formatting_unchanged_when_no_note():
    r = FieldResolver(_profile())
    field = PlannedField(kind="phone", label="Phone")
    assert r.resolve(field) == "0170 1234567"


def test_cv_path_returned_as_path():
    r = FieldResolver(_profile())
    field = PlannedField(kind="cv_upload", label="CV")
    assert r.resolve(field) == Path("/tmp/cv.pdf")


def test_privacy_consent_returns_true():
    r = FieldResolver(_profile())
    assert r.resolve(PlannedField(kind="privacy_consent", label="DSE")) is True


def test_unknown_kind_returns_none():
    r = FieldResolver(_profile())
    assert r.resolve(PlannedField(kind="unknown", label="???")) is None
