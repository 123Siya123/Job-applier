"""Relaxation engine — geo + job-type expansion.

Holds the *current* sourcing stage. After every batch the SourcingAgent
asks `next_stage_if_needed()` whether to escalate. Stages match the example
config: primary radius → wider radius → Germany → Europe → global → loosen
job types.

Stage transitions are evaluated against simple counters held in the
`SourcingState` so the rules can be tested without a database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..settings import RelaxationStrategy, SearchParams
from ..utils.logger import get_logger

_log = get_logger("sourcing.relaxation")


@dataclass
class SourcingState:
    """Lightweight running counters the relaxation engine reads."""

    qualified_companies: int = 0
    saturated_companies: int = 0  # companies marked schon_fuer_alles_beworben
    queries_attempted: int = 0
    cities_searched: list[str] = field(default_factory=list)
    job_types: list[str] = field(default_factory=list)
    radius_km: int = 50
    geo_scope: str = "PRIMARY"


class RelaxationEngine:
    """Applies the configured relaxation_strategy stages."""

    def __init__(self, search: SearchParams, state: SourcingState | None = None) -> None:
        self._search = search
        self._strategy: RelaxationStrategy = search.relaxation_strategy
        self.state = state or SourcingState(
            radius_km=search.primary_location.radius_km,
            geo_scope="PRIMARY",
            job_types=list(search.job_types),
        )
        self._applied_stages: set[str] = set()

    def next_stage_if_needed(self) -> str | None:
        """Apply any stage whose trigger evaluates to True. Returns the stage
        name applied (or None if nothing changed)."""
        if not self._strategy.enabled:
            return None
        for stage in self._strategy.stages:
            if stage.name in self._applied_stages:
                continue
            if not _evaluate_trigger(stage.trigger, self.state):
                continue
            self._apply(stage)
            self._applied_stages.add(stage.name)
            _log.info("relaxation_applied", stage=stage.name, state=self.state.__dict__)
            return stage.name
        return None

    def _apply(self, stage) -> None:
        if stage.expand_radius_km is not None:
            self.state.radius_km = stage.expand_radius_km
        if stage.geo_scope is not None:
            self.state.geo_scope = stage.geo_scope.value
        if stage.extra_job_types:
            for jt in stage.extra_job_types:
                if jt not in self.state.job_types:
                    self.state.job_types.append(jt)


_TRIGGER_RE = re.compile(r"\s*(\w+)\s*(>=|>|<=|<|==|!=)\s*(-?\d+)\s*")


def _evaluate_trigger(expr: str, state: SourcingState) -> bool:
    """Evaluates expressions like `saturated_companies >= 30`.

    Deliberately tiny — no `eval`, no surprises.
    """
    m = _TRIGGER_RE.fullmatch(expr or "")
    if not m:
        return False
    var, op, raw_val = m.group(1), m.group(2), int(m.group(3))
    val = getattr(state, var, None)
    if not isinstance(val, int):
        return False
    return {
        ">=": val >= raw_val,
        ">":  val > raw_val,
        "<=": val <= raw_val,
        "<":  val < raw_val,
        "==": val == raw_val,
        "!=": val != raw_val,
    }[op]
