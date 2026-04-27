"""OpenClawActor — drop-in replacement for PlaywrightActor.

This file is intentionally a *clean stub* designed for trivial future
implementation. Every method maps 1:1 to an OpenClaw RPC call. To complete
the integration, fill in the four `_rpc_*` helpers at the bottom and keep
the rest as-is — the public interface is already correct.

Wire format
-----------
We assume OpenClaw exposes a JSON-RPC over TCP/HTTP server (configurable via
`OPENCLAW_HOST` / `OPENCLAW_PORT`). The RPCs below mirror typical computer-use
agents:

    move_mouse(x, y)            click(button)            type(text, delay_ms)
    press_key(key)              screenshot()             read_visible_text()
    open_url(url)               find_by_text(query)      bbox_of(handle)
    upload_via_dialog(path)     scroll(dy)

If your real OpenClaw build uses a different transport (gRPC, named pipe,
WebSocket) only the `_rpc_*` helpers need to change.

OS-level file dialog
--------------------
`upload_file` first clicks the upload trigger (so the OS dialog opens), then
invokes `upload_via_dialog`, which on the OpenClaw side typically uses
`pywinauto` to:
    1. wait for an "Öffnen" / "Open" / "File Upload" window,
    2. focus the Filename field,
    3. type the absolute path,
    4. press Enter.

That logic lives inside OpenClaw — the actor only sends the path.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx

from ..settings import get_settings
from ..utils.exceptions import ActorError
from ..utils.logger import get_logger
from .actor_interface import (
    ActorInterface,
    BoundingBox,
    ElementHandle,
    PageSnapshot,
    UploadFileSpec,
)

_log = get_logger("actor.openclaw")


class OpenClawActor(ActorInterface):
    """OpenClaw-driven actor.

    Holds an HTTP client to the local OpenClaw daemon. All RPCs return
    dictionaries; we translate them into the framework-neutral types from
    `actor_interface`.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    # ---- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        if self._client is not None:
            return
        s = get_settings()
        base_url = f"http://{s.openclaw_host}:{s.openclaw_port}"
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=s.openclaw_timeout_seconds
        )
        await self._rpc("session.start", {})
        _log.info("openclaw_started", base_url=base_url)

    async def stop(self) -> None:
        if self._client is None:
            return
        try:
            await self._rpc("session.stop", {})
        finally:
            await self._client.aclose()
            self._client = None
            _log.info("openclaw_stopped")

    # ---- navigation ------------------------------------------------------

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        await self._rpc("browser.goto", {"url": url, "wait_until": wait_until})

    async def current_url(self) -> str:
        return (await self._rpc("browser.current_url", {}))["url"]

    async def reload(self) -> None:
        await self._rpc("browser.reload", {})

    async def go_back(self) -> None:
        await self._rpc("browser.back", {})

    # ---- observation -----------------------------------------------------

    async def snapshot(self, *, include_dom: bool = True) -> PageSnapshot:
        resp = await self._rpc("page.snapshot", {"include_dom": include_dom})
        png = base64.b64decode(resp["screenshot_b64"])
        return PageSnapshot(
            url=resp.get("url", ""),
            title=resp.get("title", ""),
            screenshot_png=png,
            visible_text=resp.get("visible_text", ""),
            dom_html=resp.get("dom_html"),
            width=resp.get("width", 1440),
            height=resp.get("height", 900),
        )

    async def find(
        self,
        *,
        text: str | None = None,
        role: str | None = None,
        selector: str | None = None,
        nth: int = 0,
    ) -> ElementHandle | None:
        resp = await self._rpc(
            "page.find",
            {"text": text, "role": role, "selector": selector, "nth": nth},
        )
        if not resp.get("found"):
            return None
        return self._handle_from_rpc(resp, label=text or selector or role or "element", role=role)

    async def find_all(
        self,
        *,
        text: str | None = None,
        role: str | None = None,
        selector: str | None = None,
        limit: int = 50,
    ) -> list[ElementHandle]:
        resp = await self._rpc(
            "page.find_all",
            {"text": text, "role": role, "selector": selector, "limit": limit},
        )
        return [
            self._handle_from_rpc(item, label=item.get("label", "element"), role=role)
            for item in resp.get("elements", [])
        ]

    # ---- interaction -----------------------------------------------------

    async def click(self, target: ElementHandle, *, button: str = "left") -> None:
        await self._rpc(
            "input.click", {"target": self._handle_to_rpc(target), "button": button}
        )

    async def hover(self, target: ElementHandle) -> None:
        await self._rpc("input.hover", {"target": self._handle_to_rpc(target)})

    async def type_text(
        self,
        target: ElementHandle,
        text: str,
        *,
        clear_first: bool = True,
        delay_ms: int = 30,
    ) -> None:
        await self._rpc(
            "input.type",
            {
                "target": self._handle_to_rpc(target),
                "text": text,
                "clear_first": clear_first,
                "delay_ms": delay_ms,
            },
        )

    async def press(self, key: str) -> None:
        await self._rpc("input.press", {"key": key})

    async def select_option(self, target: ElementHandle, value: str | list[str]) -> None:
        await self._rpc(
            "input.select", {"target": self._handle_to_rpc(target), "value": value}
        )

    async def check(self, target: ElementHandle, *, checked: bool = True) -> None:
        await self._rpc(
            "input.check", {"target": self._handle_to_rpc(target), "checked": checked}
        )

    async def upload_file(self, spec: UploadFileSpec) -> None:
        path = Path(spec.file_path)
        if not path.exists():
            raise ActorError(f"upload file not found: {path}")
        await self._rpc(
            "input.upload_file",
            {
                "target": self._handle_to_rpc(spec.target),
                "absolute_path": str(path.resolve()),
            },
        )

    async def scroll(self, *, dy: int = 600) -> None:
        await self._rpc("input.scroll", {"dy": dy})

    # ---- extras ----------------------------------------------------------

    async def wait_for_selector(self, selector: str, *, timeout_ms: int = 10000) -> None:
        await self._rpc(
            "page.wait_for_selector",
            {"selector": selector, "timeout_ms": timeout_ms},
        )

    async def evaluate_js(self, script: str) -> Any:
        # OpenClaw is vision-driven; agents should fall back gracefully.
        raise NotImplementedError(
            "OpenClawActor does not execute JS — use vision/click flows."
        )

    # ---- internals -------------------------------------------------------

    def _handle_to_rpc(self, h: ElementHandle) -> dict[str, Any]:
        return {
            "label": h.label,
            "selector": h.selector,
            "bbox": (
                {"x": h.bbox.x, "y": h.bbox.y, "w": h.bbox.width, "h": h.bbox.height}
                if h.bbox
                else None
            ),
            "role": h.role,
        }

    def _handle_from_rpc(self, payload: dict[str, Any], *, label: str, role: str | None) -> ElementHandle:
        bbox_raw = payload.get("bbox")
        bbox = (
            BoundingBox(bbox_raw["x"], bbox_raw["y"], bbox_raw["w"], bbox_raw["h"])
            if bbox_raw
            else None
        )
        return ElementHandle(
            label=payload.get("label", label),
            selector=payload.get("selector"),
            bbox=bbox,
            role=role,
            meta={"openclaw_id": payload.get("id")},
        )

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            raise ActorError("OpenClawActor.start() was not called")
        async with self._lock:
            try:
                resp = await self._client.post(
                    "/rpc",
                    content=json.dumps(
                        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                    ),
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise ActorError(f"OpenClaw RPC {method} failed: {exc}") from exc
            data = resp.json()
            if "error" in data:
                raise ActorError(f"OpenClaw RPC error ({method}): {data['error']}")
            return data.get("result", {})
