"""Google search wrapper.

Uses the lightweight `googlesearch-python` library which scrapes Google's
HTML results page. Wrapped here so we can later swap to the official
Custom Search JSON API if quota becomes an issue.

Each result is enriched with the raw page snippet so the extractor can
infer company names without a follow-up fetch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..utils.logger import get_logger
from ..utils.rate_limiter import GlobalRateLimiter

_log = get_logger("sourcing.google")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str | None = None


class GoogleSearch:
    def __init__(self, *, rate_limiter: GlobalRateLimiter | None = None) -> None:
        # tighter limit than global API budget — Google blocks heavy scraping
        self._rate_limiter = rate_limiter or GlobalRateLimiter(rate_per_second=0.4)

    async def search(self, query: str, *, num_results: int = 20) -> list[SearchResult]:
        await self._rate_limiter.acquire()
        try:
            results = await asyncio.to_thread(self._sync_search, query, num_results)
        except Exception as exc:
            _log.warning("google_search_failed", query=query, error=str(exc))
            return []
        return results

    def _sync_search(self, query: str, num_results: int) -> list[SearchResult]:
        from googlesearch import search as gs

        out: list[SearchResult] = []
        for item in gs(query, num_results=num_results, advanced=True, lang="de"):
            try:
                out.append(
                    SearchResult(
                        title=getattr(item, "title", "") or "",
                        url=getattr(item, "url", "") or "",
                        snippet=getattr(item, "description", None),
                    )
                )
            except Exception:
                continue
        return out
