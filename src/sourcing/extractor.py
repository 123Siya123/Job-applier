"""Extract candidate companies from search results.

Two strategies in order of preference:

1.  **Domain heuristic.** If the result URL points to a company website
    (e.g. `careers.bosch.com`, `jobs.continental.com`) we derive the
    company name from the apex domain.

2.  **LLM fallback.** If the URL points to an aggregator (Indeed, StepStone,
    LinkedIn) we send the title + snippet to Gemini and ask for the hiring
    company's name.

Either way we return `CompanyCandidate` objects which the SourcingAgent
deduplicates and persists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from ..brain.gemini_client import GeminiClient
from ..utils.logger import get_logger
from .google_search import SearchResult

_log = get_logger("sourcing.extractor")


@dataclass
class CompanyCandidate:
    name: str
    domain: str | None = None
    careers_url: str | None = None
    location_hint: str | None = None
    source_query: str | None = None


_AGGREGATORS = {
    "indeed.com", "indeed.de", "stepstone.de", "linkedin.com",
    "xing.com", "monster.de", "glassdoor.com", "kununu.com",
    "google.com", "jobs.de", "google.de",
}

_CAREERS_PREFIXES = ("careers.", "career.", "jobs.", "join.", "jobs-de.", "stellenangebote.")


async def extract_companies(
    results: list[SearchResult],
    *,
    gemini: GeminiClient | None,
    source_query: str,
) -> list[CompanyCandidate]:
    out: list[CompanyCandidate] = []
    aggregator_results: list[SearchResult] = []

    for r in results:
        if not r.url:
            continue
        host = urlparse(r.url).hostname or ""
        host = host.lower().lstrip(".")
        apex = _apex_domain(host)
        if apex in _AGGREGATORS:
            aggregator_results.append(r)
            continue
        # company-direct URL
        candidate = CompanyCandidate(
            name=_company_name_from_domain(apex),
            domain=apex,
            careers_url=_guess_careers_url(r.url, host),
            source_query=source_query,
        )
        out.append(candidate)

    # LLM fallback for aggregator-only results
    if aggregator_results and gemini is not None:
        out.extend(await _extract_via_llm(aggregator_results, gemini, source_query))

    return _dedupe(out)


def _apex_domain(host: str) -> str:
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    # naive but ok: last 2 labels for *.com / *.de, last 3 for *.co.uk-like
    if parts[-2] in {"co", "com"} and parts[-1] in {"uk", "jp", "au"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _company_name_from_domain(domain: str) -> str:
    name = domain.split(".")[0]
    name = re.sub(r"[-_]+", " ", name).strip()
    return name.title() if name else domain


def _guess_careers_url(url: str, host: str) -> str | None:
    if any(host.startswith(prefix) for prefix in _CAREERS_PREFIXES):
        return f"https://{host}/"
    return url  # leave the original landing page as a starting point


async def _extract_via_llm(
    results: list[SearchResult], gemini: GeminiClient, source_query: str
) -> list[CompanyCandidate]:
    items = "\n".join(
        f"- TITLE: {r.title}\n  URL: {r.url}\n  SNIPPET: {(r.snippet or '')[:240]}"
        for r in results[:15]
    )
    prompt = (
        "Aus den folgenden Suchergebnissen einer Jobplattform: extrahiere die "
        "tatsächlich einstellenden Firmen (nicht die Plattform). Gib JSON zurück: "
        '{"companies":[{"name":"...","domain":"...","careers_url":"...","location_hint":"..."}]}\n\n'
        f"Ergebnisse:\n{items}"
    )
    try:
        data = await gemini.generate_json(prompt, temperature=0.1, max_output_tokens=900)
    except Exception as exc:
        _log.warning("llm_extract_failed", error=str(exc))
        return []
    out: list[CompanyCandidate] = []
    for item in (data.get("companies") if isinstance(data, dict) else []) or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        out.append(
            CompanyCandidate(
                name=item["name"].strip(),
                domain=(item.get("domain") or "").lower() or None,
                careers_url=item.get("careers_url") or None,
                location_hint=item.get("location_hint") or None,
                source_query=source_query,
            )
        )
    return out


def _dedupe(candidates: list[CompanyCandidate]) -> list[CompanyCandidate]:
    seen: dict[str, CompanyCandidate] = {}
    for c in candidates:
        key = (c.domain or c.name).lower()
        if key in seen:
            # merge missing fields
            old = seen[key]
            old.careers_url = old.careers_url or c.careers_url
            old.location_hint = old.location_hint or c.location_hint
        else:
            seen[key] = c
    return list(seen.values())
