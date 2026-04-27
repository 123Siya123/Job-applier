"""Ask Gemini for industrial cities near a given hub.

Used by the sourcing agent's geo-expansion logic. Gemini does not need to
be perfectly accurate — we deduplicate, blacklist and rate-limit downstream.
"""

from __future__ import annotations

from ..utils.logger import get_logger
from .gemini_client import GeminiClient

_log = get_logger("brain.geo")


class GeoReasoner:
    SYSTEM = (
        "You are a German labour-market expert. When asked for industrial cities "
        "near a hub, return a JSON array of city names (German spelling) sorted "
        "by industrial density. No commentary."
    )

    def __init__(self, gemini: GeminiClient) -> None:
        self._gemini = gemini

    async def neighbouring_industrial_cities(
        self,
        *,
        hub_city: str,
        scope: str = "DE",
        focus_industries: list[str] | None = None,
        max_count: int = 12,
    ) -> list[str]:
        industries = ", ".join(focus_industries or ["mechatronics", "automation", "automotive", "robotics"])
        scope_clause = {
            "DE": "within Germany",
            "EU": "within Europe",
            "GLOBAL": "anywhere on earth",
            "PRIMARY": f"within ~120km of {hub_city}",
        }.get(scope, "within Germany")
        prompt = (
            f"List up to {max_count} industrial cities {scope_clause} relevant for these "
            f"sectors: {industries}. The reference hub is '{hub_city}'. Output JSON: "
            '{"cities":["...","..."]}'
        )
        try:
            data = await self._gemini.generate_json(
                prompt, system=self.SYSTEM, temperature=0.2, max_output_tokens=512
            )
        except Exception as exc:
            _log.warning("geo_reasoner_failed", error=str(exc))
            return []
        cities = data.get("cities", []) if isinstance(data, dict) else []
        return [c for c in cities if isinstance(c, str)][:max_count]
