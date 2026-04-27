"""SourcingAgent — Phase 2.

Goal: build a deduplicated list of exactly `target_company_count` (default 50)
qualified companies in the database, hitting `CompanyStatus.QUALIFIED`.

Algorithm
---------
1.  Build the working set of cities (primary city + neighbouring seed list).
2.  For each city, iterate the abstract+platform query templates from the
    user config, formatted with `{city}` / `{region}`.
3.  Run each query through Google.
4.  Extract companies (domain heuristic + LLM fallback for aggregators).
5.  Filter against blacklist; persist as DISCOVERED then QUALIFIED.
6.  After every batch, ask the RelaxationEngine if the next stage should
    apply. If so, expand the city set via Gemini's `GeoReasoner`.
7.  Stop as soon as `target_company_count` qualified companies exist.

The agent is fully restartable — duplicates are collapsed in the upsert path,
so a crashed run can resume without harm.
"""

from __future__ import annotations

import asyncio

from ..brain.gemini_client import GeminiClient
from ..brain.geo_reasoner import GeoReasoner
from ..persistence.models import CompanyStatus
from ..persistence.repository import Repository
from ..settings import Profile, SearchParams
from ..utils.events import EventBus, get_event_bus
from ..utils.exceptions import SourcingError
from ..utils.logger import get_logger
from .extractor import extract_companies
from .google_search import GoogleSearch
from .relaxation import RelaxationEngine, SourcingState

_log = get_logger("agent.sourcing")


class SourcingAgent:
    def __init__(
        self,
        *,
        profile: Profile,
        search: SearchParams,
        repo: Repository,
        gemini: GeminiClient,
        google: GoogleSearch | None = None,
        event_bus: EventBus | None = None,
        target_count: int | None = None,
    ) -> None:
        self.profile = profile
        self.search = search
        self.repo = repo
        self.gemini = gemini
        self.google = google or GoogleSearch()
        self.geo = GeoReasoner(gemini)
        self.events = event_bus or get_event_bus()
        self.target = target_count or search.target_company_count

        self.relaxer = RelaxationEngine(search)
        self._cities: list[str] = list(
            dict.fromkeys(
                [search.primary_location.city, *search.neighbouring_cities_seed]
            )
        )

    async def run(self) -> int:
        """Drive the funnel until we have `target` qualified companies. Returns
        the number of qualified companies in the DB at the end."""
        _log.info("sourcing_started", target=self.target, cities=self._cities)
        await self._publish("sourcing_started", {"target": self.target})

        while True:
            qualified = await self._count_qualified()
            if qualified >= self.target:
                _log.info("sourcing_target_reached", qualified=qualified)
                await self._publish("sourcing_target_reached", {"qualified": qualified})
                return qualified

            queries = self._build_queries()
            if not queries:
                # nothing left to try → expand
                if not await self._expand_cities():
                    raise SourcingError(
                        f"sourcing exhausted with only {qualified}/{self.target} qualified"
                    )
                continue

            for query in queries:
                if await self._count_qualified() >= self.target:
                    break
                await self._process_query(query)
                stage = self.relaxer.next_stage_if_needed()
                if stage:
                    await self._publish("relaxation_stage", {"stage": stage})
                    await self._expand_cities()

            # mark queries as attempted so we don't retry them in a tight loop
            self.relaxer.state.queries_attempted += len(queries)

        # unreachable
        return await self._count_qualified()

    # ------------------------------------------------------------------

    def _build_queries(self) -> list[str]:
        out: list[str] = []
        templates = (
            self.search.search_keywords.abstract_queries
            + self.search.search_keywords.platform_queries
        )
        for city in self._cities:
            region = self._region_for_city(city)
            for tpl in templates:
                if "{city}" in tpl or "{region}" in tpl:
                    out.append(tpl.format(city=city, region=region))
                else:
                    out.append(f"{tpl} {city}")
        # sprinkle in field+job-type combos for breadth
        for jt in self.relaxer.state.job_types:
            for field in self.search.fields:
                for city in self._cities[:3]:
                    out.append(f"{jt} {field} {city}")
        return list(dict.fromkeys(out))  # dedupe, keep order

    @staticmethod
    def _region_for_city(city: str) -> str:
        # tiny map; Gemini fills the gaps via geo expansion
        regions = {
            "Frankfurt am Main": "Hessen",
            "Wiesbaden": "Hessen",
            "Mainz": "Rheinland-Pfalz",
            "Darmstadt": "Hessen",
            "Offenbach": "Hessen",
            "Hanau": "Hessen",
            "Aschaffenburg": "Bayern",
            "Friedberg": "Hessen",
            "Stuttgart": "Baden-Württemberg",
            "München": "Bayern",
            "Berlin": "Berlin",
            "Hamburg": "Hamburg",
        }
        return regions.get(city, "Deutschland")

    async def _process_query(self, query: str) -> None:
        _log.info("sourcing_query", query=query)
        await self._publish("sourcing_query", {"query": query})

        results = await self.google.search(query, num_results=20)
        if not results:
            return

        candidates = await extract_companies(
            results, gemini=self.gemini, source_query=query
        )

        added = 0
        async with self.repo.session() as s:
            for c in candidates:
                if self._is_blacklisted(c.name, c.domain):
                    continue
                company = await self.repo.upsert_company(
                    s,
                    name=c.name,
                    domain=c.domain,
                    careers_url=c.careers_url,
                    location_city=c.location_hint,
                    sourcing_query=query,
                    sourcing_stage=self.relaxer.state.geo_scope,
                )
                if company.status == CompanyStatus.DISCOVERED:
                    await self.repo.set_company_status(
                        s, company.id, CompanyStatus.QUALIFIED
                    )
                    added += 1
        if added:
            self.relaxer.state.qualified_companies += added
            _log.info("sourcing_added", count=added, query=query)
            await self._publish("sourcing_added", {"count": added, "query": query})

    def _is_blacklisted(self, name: str, domain: str | None) -> bool:
        if name and name.lower() in (b.lower() for b in self.search.blacklist_companies):
            return True
        if domain:
            for pattern in self.search.blacklist_domains:
                if _glob_match(pattern, domain):
                    return True
        return False

    async def _expand_cities(self) -> bool:
        """Use Gemini to add more industrial cities matching the current scope.
        Returns True if at least one new city was added."""
        scope = self.relaxer.state.geo_scope
        new = await self.geo.neighbouring_industrial_cities(
            hub_city=self.search.primary_location.city,
            scope=scope,
            focus_industries=self.search.fields,
            max_count=12,
        )
        before = len(self._cities)
        for c in new:
            if c not in self._cities:
                self._cities.append(c)
        if len(self._cities) > before:
            _log.info(
                "geo_expanded", scope=scope, added=len(self._cities) - before, total=len(self._cities)
            )
            await self._publish(
                "geo_expanded",
                {"scope": scope, "added": len(self._cities) - before, "total": len(self._cities)},
            )
            return True
        return False

    async def _count_qualified(self) -> int:
        async with self.repo.session() as s:
            n = await self.repo.count_companies(
                s, CompanyStatus.QUALIFIED, CompanyStatus.IN_PROGRESS,
                CompanyStatus.APPLIED_TO_SOMETHING, CompanyStatus.SCHON_FUER_ALLES_BEWORBEN,
            )
            return n

    async def _publish(self, event: str, payload: dict) -> None:
        await self.events.publish({"agent": "sourcing", "event": event, **payload})
        # cooperative breakpoint — lets the orchestrator interrupt cleanly
        await asyncio.sleep(0)


def _glob_match(pattern: str, value: str) -> bool:
    import fnmatch
    return fnmatch.fnmatch(value.lower(), pattern.lower())
