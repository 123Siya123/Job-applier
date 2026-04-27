"""Single-shot monitoring run — the Windows Task Scheduler / cron entry point.

Use this from `schtasks` (Windows) or `cron` (unix) when you want monitoring
to run *outside* of the orchestrator. The orchestrator already runs it on a
loop when the main system is up; this is for the case where the user only
wants periodic email parsing.
"""

from __future__ import annotations

import asyncio

from src.brain.gemini_client import GeminiClient
from src.monitoring.monitoring_agent import MonitoringAgent
from src.persistence.db import init_db
from src.persistence.repository import Repository
from src.utils.logger import configure_logging, get_logger


async def _amain() -> None:
    configure_logging()
    log = get_logger("run_monitor")
    await init_db()
    monitor = MonitoringAgent(repo=Repository(), gemini=GeminiClient())
    n = await monitor.run_once(since_hours=24)
    log.info("run_monitor_done", processed=n)


if __name__ == "__main__":
    asyncio.run(_amain())
