"""ApplicationAgent — Phase 3.

Iterates through qualified companies. For each company:

    1. Discover the careers URL (use what sourcing stored, else best-effort).
    2. Hand to PortalNavigator → list[JobLead].
    3. For each lead:
         a. Open the job page.
         b. Click "apply".
         c. Loop through application form pages:
            - snapshot the page,
            - FormAnalyzer → FormPlan,
            - resolve fields via FieldResolver,
            - fill, upload CV, write cover letter if needed,
            - click submit/next.
         d. Parse confirmation page or detect captcha → mark status.
    4. Decide via Gemini whether the company has more relevant openings;
       if not, mark `schon_fuer_alles_beworben`.

Every company is processed in its own transaction; one bad page can never
crash the whole batch.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from ..actor import ActorInterface, UploadFileSpec
from ..brain.cover_letter import CoverLetterGenerator
from ..brain.form_analyzer import FormAnalyzer, FormPlan, PlannedField
from ..brain.gemini_client import GeminiClient
from ..brain.job_classifier import JobClassifier
from ..persistence.models import (
    ApplicationStatus,
    CompanyStatus,
)
from ..persistence.repository import Repository
from ..settings import Profile, SearchParams
from ..utils.events import EventBus, get_event_bus
from ..utils.exceptions import (
    ActorError,
    BrainError,
    CaptchaEncountered,
    JobApplierError,
    PortalNavigationError,
)
from ..utils.logger import get_logger
from .field_resolver import FieldResolver
from .portal_navigator import JobLead, PortalNavigator

_log = get_logger("agent.application")

MAX_FORM_STEPS = 8           # safety brake: never loop past 8 form pages
MAX_JOBS_PER_COMPANY = 4     # avoid spamming a single careers portal


class ApplicationAgent:
    def __init__(
        self,
        *,
        actor: ActorInterface,
        gemini: GeminiClient,
        repo: Repository,
        profile: Profile,
        search: SearchParams,
        event_bus: EventBus | None = None,
    ) -> None:
        self.actor = actor
        self.gemini = gemini
        self.repo = repo
        self.profile = profile
        self.search = search
        self.events = event_bus or get_event_bus()

        self.analyzer = FormAnalyzer(gemini)
        self.classifier = JobClassifier(gemini)
        self.resolver = FieldResolver(profile)
        self.cover = CoverLetterGenerator(gemini)
        self.navigator = PortalNavigator(
            actor=actor, analyzer=self.analyzer, classifier=self.classifier, search=search
        )

    # ------------------------------------------------------------------ public

    async def process_batch(self, *, batch_size: int) -> int:
        """Process up to `batch_size` companies. Returns count actually processed."""
        async with self.repo.session() as s:
            companies = await self.repo.list_qualified_companies(s, limit=batch_size)
        if not companies:
            return 0

        for company in companies:
            try:
                await self._process_company(company.id, company.name, company.careers_url)
            except JobApplierError as exc:
                _log.warning("company_failed", company=company.name, error=str(exc))
                async with self.repo.session() as s:
                    await self.repo.set_company_status(
                        s, company.id, CompanyStatus.ERROR, note=str(exc)
                    )
            except Exception as exc:  # last-resort safety net
                _log.exception("company_crashed", company=company.name, error=str(exc))
                async with self.repo.session() as s:
                    await self.repo.set_company_status(
                        s, company.id, CompanyStatus.ERROR, note=f"crash: {exc}"
                    )
        return len(companies)

    # ------------------------------------------------------------------ company

    async def _process_company(
        self, company_id: int, company_name: str, careers_url: str | None
    ) -> None:
        _log.info("company_start", company=company_name)
        await self._publish("company_start", {"company": company_name})

        async with self.repo.session() as s:
            await self.repo.set_company_status(s, company_id, CompanyStatus.IN_PROGRESS)

        if not careers_url:
            careers_url = await self._guess_careers_url(company_name)
            if not careers_url:
                async with self.repo.session() as s:
                    await self.repo.set_company_status(
                        s, company_id, CompanyStatus.SKIPPED, note="no careers_url"
                    )
                return

        try:
            leads = await self.navigator.discover_leads(careers_url, max_leads=MAX_JOBS_PER_COMPANY)
        except (ActorError, PortalNavigationError) as exc:
            async with self.repo.session() as s:
                await self.repo.set_company_status(
                    s, company_id, CompanyStatus.ERROR, note=f"navigator: {exc}"
                )
            return

        if not leads:
            async with self.repo.session() as s:
                await self.repo.set_company_status(
                    s, company_id, CompanyStatus.SCHON_FUER_ALLES_BEWORBEN,
                    note="no relevant openings",
                )
            await self._publish("company_done", {"company": company_name, "applied": 0})
            return

        applied = 0
        for lead in leads:
            try:
                if await self._apply_to_lead(company_id, company_name, lead):
                    applied += 1
            except CaptchaEncountered:
                _log.info("captcha_skip", company=company_name, job=lead.title)
                continue
            except JobApplierError as exc:
                _log.warning("job_failed", company=company_name, job=lead.title, error=str(exc))
                continue

        async with self.repo.session() as s:
            new_status = (
                CompanyStatus.APPLIED_TO_SOMETHING
                if applied
                else CompanyStatus.SCHON_FUER_ALLES_BEWORBEN
            )
            await self.repo.set_company_status(
                s, company_id, new_status, note=f"applied_to={applied}"
            )

        await self._publish("company_done", {"company": company_name, "applied": applied})

    # ------------------------------------------------------------------ apply

    async def _apply_to_lead(
        self, company_id: int, company_name: str, lead: JobLead
    ) -> bool:
        # Open the job page
        if lead.url:
            await self.actor.goto(lead.url)
        elif lead.handle is not None:
            await self.actor.click(lead.handle)
        else:
            raise PortalNavigationError("lead has neither url nor handle")

        # Persist the job + an in-progress application row
        job_url = await self.actor.current_url()
        async with self.repo.session() as s:
            job = await self.repo.upsert_job(
                s, company_id=company_id, title=lead.title, url=job_url, job_type=None,
            )
            app = await self.repo.create_application(s, job_id=job.id)
            application_id = app.id

        await self._publish(
            "job_open",
            {"company": company_name, "title": lead.title, "url": job_url},
        )

        # Click the apply button
        snap = await self.actor.snapshot()
        plan = await self.analyzer.analyze(snap)
        await self._click_apply(plan)

        # Step through the application form
        for step in range(MAX_FORM_STEPS):
            snap = await self.actor.snapshot()
            plan = await self.analyzer.analyze(snap)

            if plan.page_kind == "captcha":
                async with self.repo.session() as s:
                    await self.repo.mark_application_failed(
                        s, application_id, reason="captcha encountered"
                    )
                raise CaptchaEncountered("captcha wall")

            if plan.page_kind == "thank_you":
                portal_id = _try_extract_confirmation_id(snap.visible_text)
                async with self.repo.session() as s:
                    await self.repo.mark_application_submitted(
                        s,
                        application_id,
                        cover_letter=getattr(self, "_last_cover_letter", None),
                        portal_confirmation_id=portal_id,
                    )
                _log.info("application_submitted", company=company_name, job=lead.title)
                await self._publish(
                    "application_submitted",
                    {
                        "company": company_name,
                        "title": lead.title,
                        "confirmation": portal_id,
                    },
                )
                return True

            if not plan.is_application_form:
                # we are still on the job description / details page → re-click apply
                if step == 0:
                    continue
                async with self.repo.session() as s:
                    await self.repo.mark_application_failed(
                        s, application_id, reason="no application form found"
                    )
                return False

            await self._fill_form(plan, company_name, lead.title, snap_text=snap.visible_text)
            await self._proceed(plan)
            await asyncio.sleep(1.2)  # let the page settle

        async with self.repo.session() as s:
            await self.repo.mark_application_failed(
                s, application_id, reason=f"exceeded {MAX_FORM_STEPS} form steps"
            )
        return False

    # ------------------------------------------------------------------ form

    async def _fill_form(
        self,
        plan: FormPlan,
        company_name: str,
        job_title: str,
        *,
        snap_text: str,
    ) -> None:
        # We always handle the cover letter textarea explicitly, since it
        # depends on Gemini.
        for field in plan.fields:
            try:
                if field.kind == "cover_letter_text" or (
                    field.kind == "unknown" and plan.requires_cover_letter_text
                ):
                    text = await self.cover.generate(
                        profile=self.profile,
                        company_name=company_name,
                        job_title=job_title,
                        job_description=snap_text[:1800],
                    )
                    self._last_cover_letter = text
                    handle = await self._locate(field)
                    if handle:
                        await self.actor.type_text(handle, text, clear_first=True, delay_ms=10)
                    continue

                value = self.resolver.resolve(field)
                if value is None or value == "":
                    if field.required:
                        _log.warning("required_field_unresolved", kind=field.kind, label=field.label)
                    continue

                handle = await self._locate(field)
                if handle is None:
                    _log.warning("field_handle_missing", kind=field.kind, label=field.label)
                    continue

                if isinstance(value, Path):
                    await self.actor.upload_file(UploadFileSpec(target=handle, file_path=value))
                elif isinstance(value, bool):
                    await self.actor.check(handle, checked=value)
                elif field.candidate_values and isinstance(value, str):
                    if value in field.candidate_values:
                        await self.actor.select_option(handle, value)
                    else:
                        await self.actor.type_text(handle, str(value))
                else:
                    await self.actor.type_text(handle, str(value))
            except (ActorError, BrainError) as exc:
                _log.warning("field_fill_failed", kind=field.kind, label=field.label, error=str(exc))

    async def _proceed(self, plan: FormPlan) -> None:
        """Click submit (preferred) or next. If neither is visible, scroll."""
        target = plan.submit or plan.next_step
        if target is None:
            await self.actor.scroll(dy=600)
            return
        handle = await self._locate(target)
        if handle is None:
            await self.actor.scroll(dy=600)
            return
        await self.actor.click(handle)

    async def _click_apply(self, plan: FormPlan) -> None:
        # the analyzer often calls the "Bewerben" CTA `next_button`
        for label in ("Jetzt bewerben", "Bewerben", "Apply now", "Apply"):
            handle = await self.actor.find(text=label) or await self.actor.find(
                role="button", text=label
            )
            if handle:
                await self.actor.click(handle)
                return
        if plan.next_step:
            handle = await self._locate(plan.next_step)
            if handle:
                await self.actor.click(handle)

    async def _locate(self, field: PlannedField):
        if field.selector_hint:
            h = await self.actor.find(selector=field.selector_hint)
            if h:
                return h
        if field.text_hint:
            h = await self.actor.find(text=field.text_hint)
            if h:
                return h
        if field.label:
            return await self.actor.find(text=field.label)
        return None

    # ------------------------------------------------------------------ misc

    async def _guess_careers_url(self, company_name: str) -> str | None:
        # Best effort: ask Gemini for the canonical careers URL
        try:
            data = await self.gemini.generate_json(
                f"Karriere-Seite (deutsche oder englische URL) der Firma: {company_name}. "
                'Gib JSON: {"url":"..."} oder {"url": null} wenn unbekannt.',
                temperature=0.1,
                max_output_tokens=120,
            )
            url = data.get("url") if isinstance(data, dict) else None
            return url if isinstance(url, str) and url.startswith("http") else None
        except BrainError:
            return None

    async def _publish(self, event: str, payload: dict) -> None:
        await self.events.publish({"agent": "application", "event": event, **payload})


def _try_extract_confirmation_id(text: str) -> str | None:
    import re

    for pattern in (
        r"Referenznummer[:\s]+([A-Za-z0-9\-]+)",
        r"Bewerbungs-?ID[:\s]+([A-Za-z0-9\-]+)",
        r"Application\s*ID[:\s]+([A-Za-z0-9\-]+)",
        r"Confirmation\s*[#:]\s*([A-Za-z0-9\-]+)",
    ):
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip()
    return None
