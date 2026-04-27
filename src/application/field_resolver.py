"""Translate `FormPlan` field kinds into concrete profile values.

The FormAnalyzer says "this field is `phone` and must start with +49"; the
FieldResolver turns that into the actual string to type. Centralising the
mapping here keeps the application loop readable and the formatting rules
testable.
"""

from __future__ import annotations

from pathlib import Path

from ..brain.form_analyzer import PlannedField
from ..settings import Profile


class FieldResolver:
    def __init__(self, profile: Profile) -> None:
        self.profile = profile

    def resolve(self, field: PlannedField) -> str | Path | bool | None:
        p = self.profile.personal

        match field.kind:
            case "first_name":
                return p.first_name
            case "last_name":
                return p.last_name
            case "full_name":
                return f"{p.first_name} {p.last_name}"
            case "email":
                return p.email
            case "phone":
                return self._format_phone(p.phone, field)
            case "address_street":
                return p.address.street
            case "address_postal_code":
                return p.address.postal_code
            case "address_city":
                return p.address.city
            case "address_country":
                return p.address.country
            case "linkedin":
                return p.linkedin or ""
            case "github":
                return p.github or ""
            case "earliest_start":
                return self.profile.preferences.earliest_start or ""
            case "weekly_hours":
                return str(self.profile.preferences.max_weekly_hours or "")
            case "salary_expectation":
                return ""  # leave blank; Gemini will write a note if asked
            case "cv_upload":
                return Path(self.profile.files.cv_path)
            case "cover_letter_upload":
                if self.profile.files.cover_letter_template_path:
                    return Path(self.profile.files.cover_letter_template_path)
                return None
            case "transcript_upload":
                if self.profile.files.transcript_path:
                    return Path(self.profile.files.transcript_path)
                return None
            case "additional_documents_upload":
                return None
            case "privacy_consent":
                return True
            case "newsletter_optin":
                return False
            case _:
                return None

    @staticmethod
    def _format_phone(raw: str, field: PlannedField) -> str:
        notes = (field.notes or "").lower()
        # Strip whitespace once so all branches see canonical input.
        cleaned = raw.replace(" ", "")
        if "+49" in notes or "international" in notes:
            if cleaned.startswith("+"):
                return cleaned
            if cleaned.startswith("0"):
                return "+49" + cleaned[1:]
        return raw
