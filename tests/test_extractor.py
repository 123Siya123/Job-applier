"""Sourcing extractor — domain heuristic without Gemini."""

from __future__ import annotations

import pytest

from src.sourcing.extractor import extract_companies
from src.sourcing.google_search import SearchResult


@pytest.mark.asyncio
async def test_company_url_yields_candidate():
    results = [
        SearchResult(title="Karriere bei Bosch", url="https://careers.bosch.com/de/jobs", snippet=""),
        SearchResult(title="Continental Jobs", url="https://jobs.continental.com/", snippet=""),
    ]
    out = await extract_companies(results, gemini=None, source_query="Robotik Frankfurt")
    names = sorted(c.name for c in out)
    assert "Bosch.Com" in names or "Bosch" in names[0]
    assert any("Continental" in c.name for c in out)


@pytest.mark.asyncio
async def test_aggregator_only_results_skipped_without_gemini():
    results = [
        SearchResult(title="Werkstudent — Indeed", url="https://de.indeed.com/job/123", snippet=""),
    ]
    out = await extract_companies(results, gemini=None, source_query="x")
    assert out == []


@pytest.mark.asyncio
async def test_dedup_collapses_same_domain():
    results = [
        SearchResult(title="Karriere", url="https://careers.foo.de/a", snippet=""),
        SearchResult(title="Stellenangebote", url="https://careers.foo.de/b", snippet=""),
    ]
    out = await extract_companies(results, gemini=None, source_query="x")
    assert len(out) == 1
