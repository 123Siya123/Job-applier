"""Thin async wrapper around the Gemini SDK.

We use `google-genai` (the unified Google GenAI SDK). All higher-level brain
modules (form analyzer, cover letter, geo reasoner, …) go through this one
client so:

  * rate limiting is centralised,
  * structured-JSON parsing is uniform,
  * model selection is configurable from `.env`,
  * **multi-key rotation** survives per-key quotas in 24/7 operation,
  * tests can swap in a stub.

The SDK is sync; we run calls inside a thread pool via `asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
from typing import Any

from google import genai
from google.genai import types as genai_types

from ..settings import get_settings
from ..utils.exceptions import BrainError
from ..utils.logger import get_logger
from ..utils.rate_limiter import GlobalRateLimiter
from ..utils.retry import retry_with_backoff

_log = get_logger("brain.gemini")
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_QUOTA_MARKERS = ("quota", "rate limit", "resource_exhausted", "429", "exhausted")


class GeminiClient:
    """One client for both text and vision calls.

    Holds N underlying `genai.Client` instances — one per API key — and
    rotates them round-robin. When a call fails with a quota / rate-limit
    error we mark that key as cooling-down and pick the next.
    """

    def __init__(self, *, rate_limiter: GlobalRateLimiter | None = None) -> None:
        s = get_settings()
        keys = s.gemini_key_list()
        if not keys:
            raise BrainError(
                "no Gemini API keys configured — set GEMINI_API_KEY or GEMINI_API_KEYS in .env"
            )
        self._clients: list[genai.Client] = [genai.Client(api_key=k) for k in keys]
        self._cycle = itertools.cycle(range(len(self._clients)))
        self._cooldown_until: list[float] = [0.0] * len(self._clients)
        self._key_count = len(self._clients)

        self._text_model = s.gemini_model
        self._vision_model = s.gemini_vision_model
        self._rate_limiter = rate_limiter or GlobalRateLimiter(
            rate_per_second=s.global_rate_limit_rps
        )
        self._lock = asyncio.Lock()
        _log.info("gemini_client_ready", keys=self._key_count)

    async def generate_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.4,
        max_output_tokens: int = 2048,
    ) -> str:
        return await self._generate(
            model=self._text_model,
            contents=[prompt],
            system=system,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

    async def generate_with_image(
        self,
        prompt: str,
        image_png: bytes,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
    ) -> str:
        image_part = genai_types.Part.from_bytes(data=image_png, mime_type="image/png")
        return await self._generate(
            model=self._vision_model,
            contents=[image_part, prompt],
            system=system,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

    async def generate_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        image_png: bytes | None = None,
        temperature: float = 0.1,
        max_output_tokens: int = 2048,
    ) -> Any:
        """Like generate_text/with_image, but parses the model output as JSON.

        We instruct the model to return JSON-only and tolerate fenced blocks.
        """
        instruction = (
            (system or "")
            + "\n\nRespond with strict JSON only. No markdown, no commentary."
        ).strip()

        if image_png is not None:
            raw = await self.generate_with_image(
                prompt,
                image_png,
                system=instruction,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
        else:
            raw = await self.generate_text(
                prompt,
                system=instruction,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
        return _parse_json(raw)

    # ---- internals -------------------------------------------------------

    async def _generate(
        self,
        *,
        model: str,
        contents: list[Any],
        system: str | None,
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        await self._rate_limiter.acquire()

        cfg = genai_types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            system_instruction=system,
        )

        # Try every available key once before giving up. Each attempt itself
        # gets retry-with-backoff for transient network errors; on quota
        # errors we move to the next key immediately.
        last_exc: Exception | None = None
        for _ in range(self._key_count):
            idx = await self._next_index()

            async def _call(i: int = idx) -> str:
                return await asyncio.to_thread(self._sync_generate, i, model, contents, cfg)

            try:
                return await retry_with_backoff(
                    _call,
                    max_attempts=3,
                    initial_wait=2.0,
                    max_wait=20.0,
                    label=f"gemini[{idx}]:{model}",
                )
            except Exception as exc:
                last_exc = exc
                if _is_quota_error(exc):
                    await self._mark_cooldown(idx, seconds=60)
                    _log.warning("gemini_key_quota", key_index=idx, error=str(exc))
                    continue
                raise BrainError(f"Gemini call failed: {exc}") from exc

        raise BrainError(f"Gemini call failed across all {self._key_count} keys: {last_exc}")

    def _sync_generate(self, idx: int, model: str, contents: list[Any], cfg) -> str:
        client = self._clients[idx]
        resp = client.models.generate_content(model=model, contents=contents, config=cfg)
        text = getattr(resp, "text", None)
        if not text:
            raise BrainError("Gemini returned empty response")
        return text

    async def _next_index(self) -> int:
        """Pick the next non-cooling-down client. Falls back to round-robin
        if all keys are cooling — in that case the call may still succeed if
        the cooldown was conservative."""
        import time

        async with self._lock:
            now = time.monotonic()
            for _ in range(self._key_count):
                idx = next(self._cycle)
                if self._cooldown_until[idx] <= now:
                    return idx
            # everyone cooling — return least-cool
            return min(range(self._key_count), key=lambda i: self._cooldown_until[i])

    async def _mark_cooldown(self, idx: int, *, seconds: int) -> None:
        import time

        async with self._lock:
            self._cooldown_until[idx] = time.monotonic() + seconds


def _is_quota_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _QUOTA_MARKERS)


def _parse_json(text: str) -> Any:
    """Extract JSON from a model response. Tolerates ```json fences."""
    text = text.strip()
    match = _JSON_BLOCK_RE.search(text)
    if match:
        text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise BrainError(f"Model did not return valid JSON: {exc}\nraw=\n{text[:500]}") from exc
