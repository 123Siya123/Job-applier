"""Single entry point: `python -m src.main`.

Boots the orchestrator and runs until SIGINT/SIGTERM. On Windows you can run
this under the Task Scheduler — see docs/DEPLOYMENT.md.
"""

from __future__ import annotations

import asyncio

from .orchestrator import Orchestrator
from .utils.logger import configure_logging, get_logger


async def _amain() -> None:
    configure_logging()
    log = get_logger("main")
    log.info("job_applier_starting")
    orch = Orchestrator()
    await orch.run()
    log.info("job_applier_stopped")


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
