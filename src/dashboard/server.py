"""FastAPI dashboard.

Single-page UI plus three endpoints:

  GET  /            — HTML
  GET  /api/state   — current snapshot (companies + status counts)
  GET  /api/events  — SSE stream of live events
"""

from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request

from ..persistence.models import CompanyStatus
from ..persistence.repository import Repository
from ..settings import get_settings
from ..utils.events import get_event_bus
from ..utils.logger import get_logger

_log = get_logger("dashboard")
_HERE = Path(__file__).parent


def build_app(repo: Repository | None = None) -> FastAPI:
    repo = repo or Repository()
    bus = get_event_bus()

    app = FastAPI(title="Job-Applier Dashboard")
    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="index.html", context={})

    @app.get("/api/state")
    async def state() -> dict:
        async with repo.session() as s:
            companies = await repo.list_all_companies(s)
            status_counts = await repo.aggregate_status_counts(s)
            recent_events = await repo.recent_events(s, limit=30)

            target = get_settings().target_company_count

            company_payload = []
            for c in companies:
                jobs = await repo.jobs_for_company(s, c.id)
                company_payload.append(
                    {
                        "id": c.id,
                        "name": c.name,
                        "domain": c.domain,
                        "careers_url": c.careers_url,
                        "status": c.status.value,
                        "city": c.location_city,
                        "size": c.size.value,
                        "jobs": [
                            {
                                "id": j.id,
                                "title": j.title,
                                "url": j.url,
                                "applications": [
                                    {
                                        "id": app.id,
                                        "status": app.status.value,
                                        "applied_at": app.applied_at.isoformat()
                                        if app.applied_at else None,
                                    }
                                    for app in j.applications
                                ],
                            }
                            for j in jobs
                        ],
                    }
                )

            return {
                "target": target,
                "counts": {
                    "discovered": sum(
                        1 for c in companies if c.status == CompanyStatus.DISCOVERED
                    ),
                    "qualified": sum(
                        1 for c in companies if c.status == CompanyStatus.QUALIFIED
                    ),
                    "in_progress": sum(
                        1 for c in companies if c.status == CompanyStatus.IN_PROGRESS
                    ),
                    "applied": sum(
                        1 for c in companies if c.status == CompanyStatus.APPLIED_TO_SOMETHING
                    ),
                    "exhausted": sum(
                        1 for c in companies
                        if c.status == CompanyStatus.SCHON_FUER_ALLES_BEWORBEN
                    ),
                    "skipped": sum(
                        1 for c in companies if c.status == CompanyStatus.SKIPPED
                    ),
                    "error": sum(1 for c in companies if c.status == CompanyStatus.ERROR),
                    "total": len(companies),
                },
                "applications": status_counts,
                "companies": company_payload,
                "events": [
                    {
                        "id": ev.id,
                        "type": ev.event_type.value,
                        "payload": ev.payload,
                        "at": ev.created_at.isoformat() if ev.created_at else None,
                    }
                    for ev in recent_events
                ],
            }

    @app.get("/api/events")
    async def events(request: Request) -> EventSourceResponse:
        async def gen():
            async for msg in bus.subscribe():
                if await request.is_disconnected():
                    break
                yield {"event": "update", "data": msg}

        return EventSourceResponse(gen())

    return app


def run_dashboard() -> None:
    s = get_settings()
    app = build_app()
    uvicorn.run(app, host=s.dashboard_host, port=s.dashboard_port, log_level="info")
