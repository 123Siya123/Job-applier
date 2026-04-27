"""Classify a recruitment email and (try to) match it to one of our applications.

We use a hybrid approach: cheap regex/keyword detection first, Gemini fallback
for ambiguous cases. The match-key is `(sender_domain, job_title)` — Gemini
sees the candidate set we currently track and picks the best match. This
prevents the system from updating an *old* application from a previous round.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..brain.gemini_client import GeminiClient
from ..persistence.models import ApplicationStatus
from ..utils.logger import get_logger

_log = get_logger("monitoring.classifier")


@dataclass
class EmailIntent:
    detected_status: ApplicationStatus | None
    matched_application_id: int | None
    confidence: float
    reason: str


_REGEX_RULES: list[tuple[ApplicationStatus, list[re.Pattern[str]]]] = [
    (
        ApplicationStatus.REJECTED,
        [
            re.compile(r"\b(absage|ablehnung|leider können wir|nicht weiterverfolgen)", re.I),
            re.compile(r"\b(unfortunately|we will not be moving forward|regret to inform)", re.I),
        ],
    ),
    (
        ApplicationStatus.INTERVIEW_INVITATION,
        [
            re.compile(r"\b(einladung zum (gespräch|interview)|kennenlern)", re.I),
            re.compile(r"\b(interview invitation|invite you to|schedule a call)", re.I),
        ],
    ),
    (
        ApplicationStatus.OFFER,
        [
            re.compile(r"\b(angebot|vertragsangebot|wir freuen uns ihnen.*anzubieten)", re.I),
            re.compile(r"\b(offer letter|pleased to offer)", re.I),
        ],
    ),
    (
        ApplicationStatus.CONFIRMED,
        [
            re.compile(r"\b(eingang|bestätigung|wir haben ihre bewerbung erhalten)", re.I),
            re.compile(r"\b(application received|thank you for applying|we received)", re.I),
        ],
    ),
]


class EmailClassifier:
    SYSTEM = (
        "You match recruitment emails to job applications. Given an email and a small "
        "set of currently-tracked applications, output JSON: "
        '{"status":"applied|confirmed|rejected|interview_invitation|offer|none",'
        '"application_id": <int or null>,"confidence":0..1,"reason":"<short>"} '
        "Pick application_id only if the email's sender domain or quoted job title "
        "clearly matches one of the candidates. Otherwise application_id is null."
    )

    def __init__(self, gemini: GeminiClient) -> None:
        self._gemini = gemini

    def regex_status(self, *, subject: str, body: str) -> ApplicationStatus | None:
        text = f"{subject}\n{body}"
        for status, patterns in _REGEX_RULES:
            if any(p.search(text) for p in patterns):
                return status
        return None

    async def classify(
        self,
        *,
        sender: str,
        subject: str,
        body: str,
        candidates: list[dict],
    ) -> EmailIntent:
        # quick regex-based status detection (always run; fast)
        regex_status = self.regex_status(subject=subject, body=body)

        # candidate set must be small to keep prompts cheap
        sample = candidates[:30]
        prompt = (
            f"Email sender: {sender}\nSubject: {subject}\n\n"
            f"Body (truncated):\n{body[:2400]}\n\n"
            f"Tracked applications (id, company, job_title, applied_at):\n"
            + "\n".join(
                f"- {c['id']} | {c['company']} | {c['title']} | {c.get('applied_at')}"
                for c in sample
            )
            + "\n\nMatch and emit JSON."
        )
        try:
            data = await self._gemini.generate_json(
                prompt, system=self.SYSTEM, temperature=0.0, max_output_tokens=300
            )
        except Exception as exc:
            _log.warning("gemini_classify_failed", error=str(exc))
            return EmailIntent(
                detected_status=regex_status,
                matched_application_id=None,
                confidence=0.4 if regex_status else 0.0,
                reason="regex-only fallback",
            )

        status_raw = (data.get("status") or "none").lower()
        try:
            status = ApplicationStatus(status_raw) if status_raw != "none" else None
        except ValueError:
            status = None
        if status is None and regex_status is not None:
            status = regex_status

        app_id = data.get("application_id")
        try:
            app_id_int = int(app_id) if app_id is not None else None
        except (TypeError, ValueError):
            app_id_int = None

        return EmailIntent(
            detected_status=status,
            matched_application_id=app_id_int,
            confidence=float(data.get("confidence", 0.0) or 0.0),
            reason=str(data.get("reason", "")),
        )
