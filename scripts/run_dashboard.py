"""Standalone dashboard launcher (no agents)."""

from __future__ import annotations

from src.dashboard.server import run_dashboard
from src.utils.logger import configure_logging


if __name__ == "__main__":
    configure_logging()
    run_dashboard()
