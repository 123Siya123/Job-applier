"""Create the SQLite schema. Run once after first checkout."""

from __future__ import annotations

import asyncio

from src.persistence.db import init_db
from src.utils.logger import configure_logging, get_logger


async def _amain() -> None:
    configure_logging()
    log = get_logger("init_db")
    await init_db()
    log.info("database_initialised")


if __name__ == "__main__":
    asyncio.run(_amain())
