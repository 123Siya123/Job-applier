"""Factory — `build_actor()` returns the configured backend.

This is the *single switch-point* the rest of the system uses. Set
`ACTOR_BACKEND=openclaw` in `.env` and everything routes through OpenClaw.
"""

from __future__ import annotations

from ..settings import ActorBackend, get_settings
from .actor_interface import ActorInterface


def build_actor() -> ActorInterface:
    backend = get_settings().actor_backend
    if backend == ActorBackend.PLAYWRIGHT:
        from .playwright_actor import PlaywrightActor

        return PlaywrightActor()
    if backend == ActorBackend.OPENCLAW:
        from .openclaw_actor import OpenClawActor

        return OpenClawActor()
    raise ValueError(f"Unknown actor backend: {backend}")
