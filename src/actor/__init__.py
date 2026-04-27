"""Actor layer: the only thing in the system that touches the OS or browser.

The Actor abstraction is the swap-point for OpenClaw. Everything in
`src/agents`, `src/application` and `src/sourcing` depends on `ActorInterface`
(never on Playwright or OpenClaw directly), so switching backends is one
factory call.
"""

from .actor_interface import (
    ActorInterface,
    BoundingBox,
    ElementHandle,
    PageSnapshot,
    UploadFileSpec,
)
from .factory import build_actor

__all__ = [
    "ActorInterface",
    "BoundingBox",
    "ElementHandle",
    "PageSnapshot",
    "UploadFileSpec",
    "build_actor",
]
