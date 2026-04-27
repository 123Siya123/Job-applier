"""The Orchestrator — heart of the 24h loop.

Responsibilities
----------------
1.  Spawn shared singletons (actor, gemini, repo, event-bus).
2.  Run **Phase 2** until 50 qualified companies exist.
3.  Run **Phase 3** in 50 batches (1 company per batch by default — the user
    asked for "50 batches"). Batch size is configurable.
4.  Run the **Monitoring** loop on its own schedule (every
    `EMAIL_POLL_INTERVAL_MINUTES` minutes).
5.  Run the **Dashboard** in parallel.
6.  Survive errors at agent level — one bad batch never kills the run.

It uses one event loop, async tasks for the dashboard + monitoring, and a
foreground loop for sourcing+application (which both need exclusive use of
the actor/browser).
"""

from __future__ import annotations

import asyncio
import signal

import uvicorn

from ..actor import build_actor
from ..application.application_agent import ApplicationAgent
from ..brain.gemini_client import GeminiClient
from ..dashboard.server import build_app
from ..monitoring.monitoring_agent import MonitoringAgent
from ..persistence.db import init_db
from ..persistence.repository import Repository
from ..persistence.models import CompanyStatus
from ..settings import Settings, get_settings
from ..sourcing.sourcing_agent import SourcingAgent
from ..utils.events import get_event_bus
from ..utils.logger import configure_logging, get_logger

_log = get_logger("orchestrator")


class Orchestrator:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.repo = Repository()
        self.gemini = GeminiClient()
        self.actor = build_actor()
        self.events = get_event_bus()

        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------ run

    async def run(self) -> None:
        configure_logging(self.settings.log_level, self.settings.log_file)
        await init_db()

        self._install_signal_handlers()

        # Background tasks: dashboard + monitor
        self._tasks.append(asyncio.create_task(self._run_dashboard(), name="dashboard"))
        self._tasks.append(asyncio.create_task(self._run_monitor_loop(), name="monitor"))

        # Foreground pipeline: sourcing → applications
        try:
            async with self.actor:
                await self._run_pipeline()
        except Exception:
            _log.exception("orchestrator_pipeline_failed")
            raise
        finally:
            self._stop_event.set()
            for t in self._tasks:
                t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # ------------------------------------------------------------------ pipeline

    async def _run_pipeline(self) -> None:
        await self.events.publish({"agent": "orchestrator", "event": "started"})

        # ---------- Phase 2 ----------
        sourcing = SourcingAgent(
            profile=self.settings.load_profile(),
            search=self.settings.load_search_params(),
            repo=self.repo,
            gemini=self.gemini,
            event_bus=self.events,
            target_count=self.settings.target_company_count,
        )

        try:
            qualified = await sourcing.run()
            _log.info("sourcing_complete", qualified=qualified)
        except Exception as exc:
            _log.exception("sourcing_failed", error=str(exc))
            qualified = 0

        if qualified == 0:
            _log.warning("no_qualified_companies — application phase skipped")
            return

        # ---------- Phase 3 (50 batches) ----------
        application = ApplicationAgent(
            actor=self.actor,
            gemini=self.gemini,
            repo=self.repo,
            profile=self.settings.load_profile(),
            search=self.settings.load_search_params(),
            event_bus=self.events,
        )

        target = self.settings.target_company_count
        batch_size = max(1, self.settings.application_daily_limit // target) if target else 1

        for batch_index in range(target):
            if self._stop_event.is_set():
                break
            await self.events.publish(
                {
                    "agent": "orchestrator",
                    "event": "batch_start",
                    "batch": batch_index + 1,
                    "of": target,
                }
            )
            try:
                processed = await application.process_batch(batch_size=batch_size)
            except Exception as exc:
                _log.exception("batch_failed", batch=batch_index + 1, error=str(exc))
                processed = 0

            await self.events.publish(
                {
                    "agent": "orchestrator",
                    "event": "batch_done",
                    "batch": batch_index + 1,
                    "processed": processed,
                }
            )

            if processed == 0:
                # nothing left to do this round — give monitor time to catch up
                # and check if more companies have shown up via re-sourcing
                _log.info("no_companies_in_batch — re-sourcing")
                try:
                    await sourcing.run()
                except Exception:
                    _log.exception("resourcing_failed")
                # if still nothing, short cool-down then continue
                async with self.repo.session() as s:
                    remaining = await self.repo.count_companies(
                        s, CompanyStatus.QUALIFIED, CompanyStatus.DISCOVERED
                    )
                if remaining == 0:
                    await asyncio.sleep(60)

        await self.events.publish({"agent": "orchestrator", "event": "all_batches_complete"})

    # ------------------------------------------------------------------ services

    async def _run_dashboard(self) -> None:
        s = self.settings
        config = uvicorn.Config(
            build_app(self.repo),
            host=s.dashboard_host,
            port=s.dashboard_port,
            log_level="warning",
            lifespan="off",
        )
        server = uvicorn.Server(config)
        try:
            await server.serve()
        except asyncio.CancelledError:
            await server.shutdown()
            raise

    async def _run_monitor_loop(self) -> None:
        monitor = MonitoringAgent(repo=self.repo, gemini=self.gemini, event_bus=self.events)
        interval_seconds = self.settings.email_poll_interval_minutes * 60

        # initial small delay so we don't IMAP-hit before the user is ready
        await asyncio.sleep(60)
        while not self._stop_event.is_set():
            try:
                await monitor.run_once(since_hours=24)
            except Exception:
                _log.exception("monitor_iteration_failed")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                continue

    # ------------------------------------------------------------------ signals

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop_event.set)
            except (NotImplementedError, RuntimeError):
                # Windows / certain test environments — fall back silently.
                pass
