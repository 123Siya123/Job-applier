"""ActorInterface — the contract every backend (Playwright, OpenClaw, …) must satisfy.

Design philosophy
-----------------
1.  **Backend-agnostic.** Methods describe *intent*, never how to execute. A
    Playwright backend may dispatch DOM events directly; an OpenClaw backend
    will move the real OS mouse and click visible pixels. Callers don't care.

2.  **Vision-first.** Every read returns a `PageSnapshot` that bundles a PNG
    screenshot, the rendered URL, the visible text, and (optionally) the DOM.
    The Gemini brain consumes this exact shape.

3.  **Coordinate-aware.** Each `ElementHandle` carries either a CSS selector
    (Playwright path) or a `BoundingBox` (OpenClaw path) — both backends fill
    the field they support and leave the other `None`.

4.  **OS-level uploads.** `upload_file` accepts a *file system path* and a
    target field handle; the implementation chooses between dispatching a
    DOM `input[type=file]` change (Playwright) or driving the native Windows
    file-dialog via pywinauto (OpenClaw).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BoundingBox:
    """Pixel rectangle in the visible viewport."""

    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2


@dataclass
class ElementHandle:
    """Opaque handle to a UI element.

    `selector` is filled by DOM-aware backends (Playwright). `bbox` is filled
    by vision-aware backends (OpenClaw). At least one must be non-None.
    `meta` stores backend-specific data (e.g. the underlying Playwright
    Locator object) and is not for caller use.
    """

    label: str
    selector: str | None = None
    bbox: BoundingBox | None = None
    role: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.selector is None and self.bbox is None:
            raise ValueError("ElementHandle needs at least selector or bbox")


@dataclass
class PageSnapshot:
    """Everything the Brain needs to reason about the current page."""

    url: str
    title: str
    screenshot_png: bytes
    visible_text: str
    dom_html: str | None = None
    width: int = 1280
    height: int = 800
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class UploadFileSpec:
    """Argument bundle for `upload_file`.

    `target` identifies the file-input or upload button. `file_path` must
    be an absolute path on the local filesystem (the OpenClaw backend will
    type this path into the OS file dialog).
    """

    target: ElementHandle
    file_path: Path


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class ActorInterface(ABC):
    """The contract.

    All methods are async. Implementations are expected to be safe under
    concurrent use of *separate* Actor instances; a single instance is not
    required to be thread-safe.

    The `start()` / `stop()` lifecycle pair lets the orchestrator share one
    actor (and one browser process) across many agents.
    """

    # ---- lifecycle -------------------------------------------------------

    @abstractmethod
    async def start(self) -> None:
        """Open the browser / connect to OpenClaw. Idempotent."""

    @abstractmethod
    async def stop(self) -> None:
        """Close all resources. Idempotent."""

    async def __aenter__(self) -> ActorInterface:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    # ---- navigation ------------------------------------------------------

    @abstractmethod
    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        """Navigate to URL. `wait_until` mirrors Playwright's load states."""

    @abstractmethod
    async def current_url(self) -> str: ...

    @abstractmethod
    async def reload(self) -> None: ...

    @abstractmethod
    async def go_back(self) -> None: ...

    # ---- observation -----------------------------------------------------

    @abstractmethod
    async def snapshot(self, *, include_dom: bool = True) -> PageSnapshot:
        """Capture screenshot + visible text + (optionally) DOM HTML."""

    @abstractmethod
    async def find(
        self,
        *,
        text: str | None = None,
        role: str | None = None,
        selector: str | None = None,
        nth: int = 0,
    ) -> ElementHandle | None:
        """Locate one element. Backends may use any combination of hints."""

    @abstractmethod
    async def find_all(
        self,
        *,
        text: str | None = None,
        role: str | None = None,
        selector: str | None = None,
        limit: int = 50,
    ) -> list[ElementHandle]: ...

    # ---- interaction -----------------------------------------------------

    @abstractmethod
    async def click(self, target: ElementHandle, *, button: str = "left") -> None: ...

    @abstractmethod
    async def hover(self, target: ElementHandle) -> None: ...

    @abstractmethod
    async def type_text(
        self,
        target: ElementHandle,
        text: str,
        *,
        clear_first: bool = True,
        delay_ms: int = 30,
    ) -> None: ...

    @abstractmethod
    async def press(self, key: str) -> None:
        """Send a keyboard key (e.g. "Enter", "Tab", "ArrowDown")."""

    @abstractmethod
    async def select_option(self, target: ElementHandle, value: str | list[str]) -> None: ...

    @abstractmethod
    async def check(self, target: ElementHandle, *, checked: bool = True) -> None: ...

    @abstractmethod
    async def upload_file(self, spec: UploadFileSpec) -> None:
        """Attach a local file to a file-input or click-to-upload button.

        Implementations:
        - Playwright: dispatch on the `<input type=file>` element directly.
        - OpenClaw:   click the button to open the OS dialog, then drive
          the Windows Explorer dialog with pywinauto to type the path and
          press Enter.
        """

    @abstractmethod
    async def scroll(self, *, dy: int = 600) -> None:
        """Scroll the viewport by `dy` pixels (positive = down)."""

    # ---- extras ----------------------------------------------------------

    @abstractmethod
    async def wait_for_selector(self, selector: str, *, timeout_ms: int = 10000) -> None: ...

    @abstractmethod
    async def evaluate_js(self, script: str) -> Any:
        """Run arbitrary JS in the page; raises on backends that can't.

        OpenClaw may raise NotImplementedError — agents should be ready for
        that and fall back to vision/click flows.
        """
