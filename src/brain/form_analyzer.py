"""Vision-driven form understanding.

`FormAnalyzer` takes a `PageSnapshot`, sends the screenshot + the visible
text to Gemini and returns a `FormPlan` — a structured list of fields with
their semantic intent (name, email, phone, CV upload, cover letter, …) and
how to find them on the page.

Gemini does the hard part: identifying that an unlabelled `<input>` is the
phone number, that a custom date-picker needs a click-then-type sequence,
that a checkbox labelled "Ich akzeptiere die Datenschutzerklärung" is the
mandatory privacy consent.

The output schema is intentionally simple — easy to extend, easy for the
ApplicationAgent to consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..actor import PageSnapshot
from ..utils.exceptions import BrainError
from ..utils.logger import get_logger
from .gemini_client import GeminiClient

_log = get_logger("brain.form_analyzer")


FieldKind = Literal[
    "first_name",
    "last_name",
    "full_name",
    "email",
    "phone",
    "address_street",
    "address_postal_code",
    "address_city",
    "address_country",
    "linkedin",
    "github",
    "earliest_start",
    "weekly_hours",
    "salary_expectation",
    "cv_upload",
    "cover_letter_upload",
    "cover_letter_text",
    "transcript_upload",
    "additional_documents_upload",
    "privacy_consent",
    "newsletter_optin",
    "submit_button",
    "next_button",
    "captcha",
    "unknown",
]


@dataclass
class PlannedField:
    kind: FieldKind
    label: str
    selector_hint: str | None = None    # e.g. "input[name='email']" if model spotted it
    text_hint: str | None = None        # accessible name / nearby visible text
    required: bool = False
    notes: str | None = None            # e.g. "phone must start with +49"
    candidate_values: list[str] = field(default_factory=list)  # for selects


@dataclass
class FormPlan:
    page_kind: Literal["job_listing", "application_form", "login", "captcha", "thank_you", "other"]
    is_application_form: bool
    requires_cover_letter_text: bool
    fields: list[PlannedField] = field(default_factory=list)
    submit: PlannedField | None = None
    next_step: PlannedField | None = None
    notes: str | None = None
    confidence: float = 0.0


class FormAnalyzer:
    SYSTEM_PROMPT = (
        "You are a UI-understanding expert helping an automation agent fill in a job "
        "application form. Look carefully at the screenshot and visible text, then describe "
        "exactly which fields exist and what each one expects. "
        "You MUST output strict JSON with this schema:\n"
        "{\n"
        '  "page_kind": "job_listing|application_form|login|captcha|thank_you|other",\n'
        '  "is_application_form": true|false,\n'
        '  "requires_cover_letter_text": true|false,\n'
        '  "fields": [\n'
        '     {"kind": "<one of FieldKind>", "label": "<visible label>", '
        '      "selector_hint": "<css selector if obvious from DOM>", '
        '      "text_hint": "<nearby text>", "required": true|false, '
        '      "notes": "<format constraints, e.g. phone must start with +49>", '
        '      "candidate_values": ["<options if dropdown>"]\n'
        "     }\n"
        "  ],\n"
        '  "submit": {"kind":"submit_button","label":"...", "selector_hint":"..."},\n'
        '  "next_step": {"kind":"next_button","label":"...", "selector_hint":"..."},\n'
        '  "notes": "<anything else the agent should know>",\n'
        '  "confidence": 0.0-1.0\n'
        "}\n\n"
        "FieldKind must be one of: "
        "first_name,last_name,full_name,email,phone,address_street,address_postal_code,"
        "address_city,address_country,linkedin,github,earliest_start,weekly_hours,"
        "salary_expectation,cv_upload,cover_letter_upload,cover_letter_text,"
        "transcript_upload,additional_documents_upload,privacy_consent,newsletter_optin,"
        "submit_button,next_button,captcha,unknown."
    )

    def __init__(self, gemini: GeminiClient) -> None:
        self._gemini = gemini

    async def analyze(self, snapshot: PageSnapshot) -> FormPlan:
        prompt = (
            "Here is the rendered page. Analyse it and emit the JSON described above.\n"
            f"URL: {snapshot.url}\n"
            f"Title: {snapshot.title}\n"
            f"Visible text (truncated to 4000 chars):\n{snapshot.visible_text[:4000]}\n"
        )
        try:
            data = await self._gemini.generate_json(
                prompt,
                system=self.SYSTEM_PROMPT,
                image_png=snapshot.screenshot_png,
                temperature=0.1,
                max_output_tokens=2048,
            )
        except BrainError as exc:
            _log.warning("form_analyzer_failed", error=str(exc))
            return FormPlan(
                page_kind="other",
                is_application_form=False,
                requires_cover_letter_text=False,
                notes=f"analyzer error: {exc}",
                confidence=0.0,
            )

        return _parse_plan(data)


def _parse_plan(data: dict) -> FormPlan:
    fields = [
        PlannedField(
            kind=_safe_kind(f.get("kind")),
            label=f.get("label", ""),
            selector_hint=f.get("selector_hint") or None,
            text_hint=f.get("text_hint") or None,
            required=bool(f.get("required", False)),
            notes=f.get("notes") or None,
            candidate_values=list(f.get("candidate_values", []) or []),
        )
        for f in data.get("fields", [])
        if isinstance(f, dict)
    ]
    submit = _maybe_field(data.get("submit"))
    nxt = _maybe_field(data.get("next_step"))
    return FormPlan(
        page_kind=data.get("page_kind", "other"),
        is_application_form=bool(data.get("is_application_form", False)),
        requires_cover_letter_text=bool(data.get("requires_cover_letter_text", False)),
        fields=fields,
        submit=submit,
        next_step=nxt,
        notes=data.get("notes") or None,
        confidence=float(data.get("confidence", 0.0) or 0.0),
    )


def _maybe_field(d: dict | None) -> PlannedField | None:
    if not d:
        return None
    return PlannedField(
        kind=_safe_kind(d.get("kind", "unknown")),
        label=d.get("label", ""),
        selector_hint=d.get("selector_hint") or None,
        text_hint=d.get("text_hint") or None,
        required=bool(d.get("required", False)),
        notes=d.get("notes") or None,
    )


_VALID_KINDS = set(FieldKind.__args__)  # type: ignore[attr-defined]


def _safe_kind(k: str | None) -> FieldKind:
    if k in _VALID_KINDS:
        return k  # type: ignore[return-value]
    return "unknown"
