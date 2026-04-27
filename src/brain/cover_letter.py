"""On-the-fly cover letter generation.

Takes the user profile, the job ad and (optionally) the company description,
and produces a clean, mistake-free German cover letter — no headers, no
fluff, ready to paste into a textarea.
"""

from __future__ import annotations

from ..settings import Profile
from ..utils.logger import get_logger
from .gemini_client import GeminiClient

_log = get_logger("brain.cover_letter")


class CoverLetterGenerator:
    SYSTEM = (
        "Du bist ein präziser Bewerbungs-Texter. Du schreibst auf Deutsch, im 'Sie'-Stil, "
        "ohne leere Floskeln, ohne 'Hiermit bewerbe ich mich…', ohne den Lebenslauf "
        "wiederzugeben. Maximal 220 Wörter. Saubere Absätze. Keine Briefkopf-Adressen, "
        "keine Datumszeile, kein 'Mit freundlichen Grüßen' am Ende — nur der Brieftext "
        "selbst, da er in ein Textfeld gepastet wird."
    )

    def __init__(self, gemini: GeminiClient) -> None:
        self._gemini = gemini

    async def generate(
        self,
        *,
        profile: Profile,
        company_name: str,
        job_title: str,
        job_description: str | None = None,
    ) -> str:
        prompt = self._build_prompt(
            profile=profile,
            company_name=company_name,
            job_title=job_title,
            job_description=job_description,
        )
        text = await self._gemini.generate_text(
            prompt, system=self.SYSTEM, temperature=0.55, max_output_tokens=900
        )
        return text.strip()

    def _build_prompt(
        self,
        *,
        profile: Profile,
        company_name: str,
        job_title: str,
        job_description: str | None,
    ) -> str:
        skills = ", ".join(profile.skills.hard[:10])
        experience = "\n".join(
            f"- {e.title} bei {e.company} ({e.start}–{e.end or 'heute'}): {e.description or ''}"
            for e in profile.experience[:3]
        )
        education = "\n".join(
            f"- {e.degree}, {e.institution} (seit {e.start}, Schnitt {e.gpa or '—'})"
            for e in profile.education
        )

        ad = (job_description or "").strip()[:1800] or "(keine Stellenbeschreibung verfügbar)"

        return (
            f"Bewerber-Selbstbeschreibung:\n{profile.self_description}\n\n"
            f"Bewerber-Hard-Skills: {skills}\n"
            f"Ausbildung:\n{education}\n"
            f"Erfahrung:\n{experience or '(keine relevante)'}\n\n"
            f"Zielfirma: {company_name}\n"
            f"Stellentitel: {job_title}\n"
            f"Stellenbeschreibung:\n{ad}\n\n"
            f"Schreibe jetzt den Brieftext (s.o. Vorgaben einhalten)."
        )
