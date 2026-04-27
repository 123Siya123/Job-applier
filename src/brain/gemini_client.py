"""Thin async wrapper around the Gemini SDK.

We use `google-genai` (the unified Google GenAI SDK). All higher-level brain
modules (form analyzer, cover letter, geo reasoner, …) go through this one
client so:

  * rate limiting is centralised,
  * structured-JSON parsing is uniform,
  * model selection is configurable from `.env`,
  * tests can swap in a stub.

The SDK is sync; we run calls inside a thread pool via `asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
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


class GeminiClient:
    """One client for both text and vision calls."""

    def __init__(self, *, rate_limiter: GlobalRateLimiter | None = None) -> None:
        s = get_settings()
        self._client = genai.Client(api_key=s.gemini_api_key)
        self._text_model = s.gemini_model
        self._vision_model = s.gemini_vision_model
        self._rate_limiter = rate_limiter or GlobalRateLimiter(
            rate_per_second=s.global_rate_limit_rps
        )
        self._lock = asyncio.Lock()

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

        async def _call() -> str:
            return await asyncio.to_thread(self._sync_generate, model, contents, cfg)

        try:
            return await retry_with_backoff(
                _call,
                max_attempts=4,
                initial_wait=2.0,
                max_wait=30.0,
                label=f"gemini:{model}",
            )
        except Exception as exc:
            raise BrainError(f"Gemini call failed: {exc}") from exc

    def _sync_generate(self, model: str, contents: list[Any], cfg) -> str:
        resp = self._client.models.generate_content(
            model=model, contents=contents, config=cfg
        )
        text = getattr(resp, "text", None)
        if not text:
            raise BrainError("Gemini returned empty response")
        return text


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
