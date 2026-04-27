"""Classifier: does this job posting match the user's interests?

Used by the sourcing agent (filters out noise from broad searches) and by
the application agent when scrolling through a careers list (decides
whether the job is worth opening).

Outputs a small JSON: `{ "is_match": bool, "score": 0..1, "reason": str,
"job_type": "Werkstudent|Praktikum|Vollzeit|Other" }`.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..settings import SearchParams
from ..utils.logger import get_logger
from .gemini_client import GeminiClient

_log = get_logger("brain.job_classifier")


@dataclass
class JobMatch:
    is_match: bool
    score: float
    job_type: str
    reason: str


class JobClassifier:
    SYSTEM = (
        "You are a strict relevance classifier. Decide whether a German job "
        "posting matches the user's wanted job types and fields. Return JSON: "
        '{"is_match": bool, "score": float in [0,1], '
        '"job_type": "Werkstudent|Praktikum|Vollzeit|Junior|Other", "reason": "<short>"}'
    )

    def __init__(self, gemini: GeminiClient) -> None:
        self._gemini = gemini

    async def classify(
        self, *, title: str, description: str | None, search: SearchParams
    ) -> JobMatch:
        prompt = (
            f"Wanted job types: {', '.join(search.job_types)}\n"
            f"Wanted fields: {', '.join(search.fields)}\n\n"
            f"Posting title: {title}\n"
            f"Posting description (truncated):\n{(description or '')[:1200]}\n\n"
            "Decide. Output JSON only."
        )
        try:
            data = await self._gemini.generate_json(
                prompt, system=self.SYSTEM, temperature=0.0, max_output_tokens=200
            )
        except Exception as exc:
            _log.warning("classify_failed", title=title, error=str(exc))
            return JobMatch(False, 0.0, "Other", f"classifier error: {exc}")

        return JobMatch(
            is_match=bool(data.get("is_match", False)),
            score=float(data.get("score", 0.0) or 0.0),
            job_type=str(data.get("job_type", "Other")),
            reason=str(data.get("reason", "")),
        )
