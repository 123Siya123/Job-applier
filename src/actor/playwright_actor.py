"""PlaywrightActor — default backend.

Headed by default (the user wants to *watch* the bot work) with a slow-mo
delay so each action is visible. Maintains exactly one browser context for
the lifetime of the Actor; closing the actor closes the browser.

This is a fully-functional implementation; it does NOT need OpenClaw to run.
OpenClaw replaces this file with `OpenClawActor` later — the rest of the
system never has to change.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    ElementHandle as PWElementHandle,
    Page,
    Playwright,
    TimeoutError as PWTimeoutError,
    async_playwright,
)

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

_log = get_logger("actor.playwright")


class PlaywrightActor(ActorInterface):
    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()

    # ---- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        if self._page is not None:
            return
        s = get_settings()
        self._pw = await async_playwright().start()
        launcher = getattr(self._pw, s.browser_channel, self._pw.chromium)
        self._browser = await launcher.launch(
            headless=not s.browser_headed,
            slow_mo=s.browser_slow_mo_ms,
            args=["--start-maximized"],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="de-DE",
            timezone_id="Europe/Berlin",
            accept_downloads=True,
        )
        self._page = await self._context.new_page()
        _log.info("playwright_started", headed=s.browser_headed, channel=s.browser_channel)

    async def stop(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
            if self._browser is not None:
                await self._browser.close()
            if self._pw is not None:
                await self._pw.stop()
        finally:
            self._pw = self._browser = self._context = self._page = None
            _log.info("playwright_stopped")

    @property
    def page(self) -> Page:
        if self._page is None:
            raise ActorError("Actor.start() was not called")
        return self._page

    # ---- navigation ------------------------------------------------------

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        try:
            await self.page.goto(url, wait_until=wait_until, timeout=30_000)
        except PWTimeoutError as exc:
            raise ActorError(f"timeout navigating to {url}") from exc

    async def current_url(self) -> str:
        return self.page.url

    async def reload(self) -> None:
        await self.page.reload()

    async def go_back(self) -> None:
        await self.page.go_back()

    # ---- observation -----------------------------------------------------

    async def snapshot(self, *, include_dom: bool = True) -> PageSnapshot:
        page = self.page
        png = await page.screenshot(full_page=False, type="png")
        text = await page.evaluate("() => document.body.innerText")
        html = await page.content() if include_dom else None
        viewport = page.viewport_size or {"width": 1440, "height": 900}
        return PageSnapshot(
            url=page.url,
            title=await page.title(),
            screenshot_png=png,
            visible_text=text or "",
            dom_html=html,
            width=viewport["width"],
            height=viewport["height"],
        )

    async def find(
        self,
        *,
        text: str | None = None,
        role: str | None = None,
        selector: str | None = None,
        nth: int = 0,
    ) -> ElementHandle | None:
        locator = await self._build_locator(text=text, role=role, selector=selector)
        try:
            count = await locator.count()
        except Exception:
            return None
        if count <= nth:
            return None
        target = locator.nth(nth)
        return await self._wrap_locator(
            target, label=text or selector or role or "element", role=role
        )

    async def find_all(
        self,
        *,
        text: str | None = None,
        role: str | None = None,
        selector: str | None = None,
        limit: int = 50,
    ) -> list[ElementHandle]:
        locator = await self._build_locator(text=text, role=role, selector=selector)
        count = min(limit, await locator.count())
        out: list[ElementHandle] = []
        for i in range(count):
            wrapped = await self._wrap_locator(
                locator.nth(i), label=text or selector or role or f"el#{i}", role=role
            )
            if wrapped:
                out.append(wrapped)
        return out

    async def _build_locator(
        self, *, text: str | None, role: str | None, selector: str | None
    ):
        page = self.page
        if selector:
            return page.locator(selector)
        if role:
            return page.get_by_role(role, name=text) if text else page.get_by_role(role)
        if text:
            return page.get_by_text(text, exact=False)
        raise ValueError("find requires at least one of: text, role, selector")

    async def _wrap_locator(self, locator, *, label: str, role: str | None) -> ElementHandle | None:
        try:
            box = await locator.bounding_box()
        except Exception:
            box = None
        bbox = BoundingBox(box["x"], box["y"], box["width"], box["height"]) if box else None
        return ElementHandle(
            label=label, selector=None, bbox=bbox, role=role, meta={"locator": locator}
        )

    # ---- interaction -----------------------------------------------------

    async def click(self, target: ElementHandle, *, button: str = "left") -> None:
        loc = target.meta.get("locator")
        if loc is None and target.selector:
            loc = self.page.locator(target.selector)
        if loc is None:
            raise ActorError(f"Cannot click — no locator: {target}")
        await loc.click(button=button, timeout=10_000)

    async def hover(self, target: ElementHandle) -> None:
        loc = target.meta.get("locator") or self.page.locator(target.selector or "")
        await loc.hover(timeout=10_000)

    async def type_text(
        self,
        target: ElementHandle,
        text: str,
        *,
        clear_first: bool = True,
        delay_ms: int = 30,
    ) -> None:
        loc = target.meta.get("locator") or self.page.locator(target.selector or "")
        if clear_first:
            await loc.fill("")
        await loc.type(text, delay=delay_ms, timeout=15_000)

    async def press(self, key: str) -> None:
        await self.page.keyboard.press(key)

    async def select_option(self, target: ElementHandle, value: str | list[str]) -> None:
        loc = target.meta.get("locator") or self.page.locator(target.selector or "")
        await loc.select_option(value=value)

    async def check(self, target: ElementHandle, *, checked: bool = True) -> None:
        loc = target.meta.get("locator") or self.page.locator(target.selector or "")
        if checked:
            await loc.check()
        else:
            await loc.uncheck()

    async def upload_file(self, spec: UploadFileSpec) -> None:
        path = Path(spec.file_path)
        if not path.exists():
            raise ActorError(f"upload file not found: {path}")

        loc = spec.target.meta.get("locator")
        if loc is None and spec.target.selector:
            loc = self.page.locator(spec.target.selector)
        if loc is None:
            raise ActorError("upload target has neither locator nor selector")

        # Easy path: it's a real file input
        try:
            await loc.set_input_files(str(path), timeout=5_000)
            return
        except Exception:
            pass

        # Otherwise: button that opens a file chooser — intercept the chooser
        async with self.page.expect_file_chooser() as fc_info:
            await loc.click(timeout=10_000)
        chooser = await fc_info.value
        await chooser.set_files(str(path))

    async def scroll(self, *, dy: int = 600) -> None:
        await self.page.mouse.wheel(0, dy)

    # ---- extras ----------------------------------------------------------

    async def wait_for_selector(self, selector: str, *, timeout_ms: int = 10000) -> None:
        await self.page.wait_for_selector(selector, timeout=timeout_ms)

    async def evaluate_js(self, script: str) -> Any:
        return await self.page.evaluate(script)
