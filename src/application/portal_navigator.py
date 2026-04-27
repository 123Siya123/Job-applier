"""PortalNavigator — adaptive search inside a company careers page.

Two strategies, picked dynamically:

* **Strict portal**: a search box exists. We try keyword search first
  ("Werkstudent Mechatronik"); if zero results, fall back to a wide
  filter (job-type only, no keyword) and visually scroll through the list.
* **Loose portal**: no proper search → page is a flat list. Scroll +
  classify titles via Gemini.

The navigator returns a list of `JobLead` (title + URL) for the
ApplicationAgent to actually apply to.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from ..actor import ActorInterface, ElementHandle
from ..brain.form_analyzer import FormAnalyzer
from ..brain.job_classifier import JobClassifier
from ..settings import SearchParams
from ..utils.exceptions import PortalNavigationError
from ..utils.logger import get_logger

_log = get_logger("application.portal")


@dataclass
class JobLead:
    title: str
    url: str | None
    handle: ElementHandle | None = None


class PortalNavigator:
    def __init__(
        self,
        *,
        actor: ActorInterface,
        analyzer: FormAnalyzer,
        classifier: JobClassifier,
        search: SearchParams,
    ) -> None:
        self.actor = actor
        self.analyzer = analyzer
        self.classifier = classifier
        self.search = search

    async def discover_leads(self, careers_url: str, *, max_leads: int = 6) -> list[JobLead]:
        await self.actor.goto(careers_url)
        leads = await self._strict_search()
        if leads:
            return leads[:max_leads]
        leads = await self._loose_scroll()
        return leads[:max_leads]

    # ------------------------------------------------------------------

    async def _strict_search(self) -> list[JobLead]:
        # 1) try targeted keyword search
        for term in self._search_terms():
            search_box = await self.actor.find(role="searchbox") or await self.actor.find(
                selector="input[type='search']"
            )
            if search_box is None:
                return []
            await self.actor.type_text(search_box, term)
            await self.actor.press("Enter")
            try:
                await self.actor.wait_for_selector(
                    "a[href*='job'], a[href*='stelle'], li.job, .job-card", timeout_ms=8000
                )
            except Exception:
                continue
            leads = await self._collect_visible_jobs()
            if leads:
                _log.info("strict_keyword_hit", term=term, hits=len(leads))
                return leads

        # 2) clear keyword, broaden via job-type filter only
        try:
            await self.actor.goto(await self.actor.current_url())  # reset filters
        except Exception:
            pass
        for jt_label in ("Werkstudent", "Praktikum", "Studentenjobs", "Studierende"):
            btn = await self.actor.find(text=jt_label, role="link") or await self.actor.find(
                text=jt_label
            )
            if btn:
                await self.actor.click(btn)
                leads = await self._collect_visible_jobs()
                if leads:
                    _log.info("strict_filter_hit", filter=jt_label, hits=len(leads))
                    return await self._classify(leads)
        return []

    async def _loose_scroll(self) -> list[JobLead]:
        leads: list[JobLead] = []
        seen_titles: set[str] = set()
        for _ in range(8):
            visible = await self._collect_visible_jobs()
            for lead in visible:
                if lead.title and lead.title not in seen_titles:
                    seen_titles.add(lead.title)
                    leads.append(lead)
            await self.actor.scroll(dy=900)
        if not leads:
            return []
        return await self._classify(leads)

    async def _collect_visible_jobs(self) -> list[JobLead]:
        # any anchor whose href looks job-ish; the analyzer/classifier will filter
        candidates = await self.actor.find_all(
            selector=(
                "a[href*='/job/'], a[href*='/jobs/'], a[href*='/stelle'], "
                "a[href*='/career'], a[href*='/karriere/'], a[href*='/position/'], "
                "a[role='link']"
            ),
            limit=80,
        )
        out: list[JobLead] = []
        for c in candidates:
            label = (c.label or "").strip()
            if not label or len(label) < 6:
                continue
            url = await self._href_of(c)
            if url and not _looks_like_job_url(url):
                continue
            out.append(JobLead(title=label[:200], url=url, handle=c))
        return out

    async def _href_of(self, h: ElementHandle) -> str | None:
        loc = h.meta.get("locator")
        if loc is None:
            return None
        try:
            return await loc.get_attribute("href")
        except Exception:
            return None

    async def _classify(self, leads: list[JobLead]) -> list[JobLead]:
        kept: list[JobLead] = []
        for lead in leads:
            match = await self.classifier.classify(
                title=lead.title, description=None, search=self.search
            )
            if match.is_match and match.score >= 0.55:
                kept.append(lead)
            if len(kept) >= 10:
                break
        return kept

    def _search_terms(self) -> list[str]:
        terms: list[str] = []
        for jt in self.search.job_types:
            for f in self.search.fields:
                terms.append(f"{jt} {f}")
        terms.extend(self.search.fields)
        return terms[:6]


def _looks_like_job_url(url: str) -> bool:
    try:
        path = (urlparse(url).path or "").lower()
    except Exception:
        return False
    keywords = ("/job", "/jobs", "/stelle", "/karriere", "/career", "/position", "/vacanc")
    return any(k in path for k in keywords)
