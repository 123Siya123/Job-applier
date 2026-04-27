"""RelaxationEngine — pure logic, no I/O."""

from __future__ import annotations

from src.settings import (
    GeoScope,
    PrimaryLocation,
    RelaxationStage,
    RelaxationStrategy,
    SearchKeywords,
    SearchParams,
)
from src.sourcing.relaxation import RelaxationEngine, _evaluate_trigger, SourcingState


def _params() -> SearchParams:
    return SearchParams(
        job_types=["Praktikum"],
        fields=["Mechatronik"],
        primary_location=PrimaryLocation(city="Frankfurt am Main", radius_km=50),
        relaxation_strategy=RelaxationStrategy(
            enabled=True,
            stages=[
                RelaxationStage(name="wider", trigger="saturated_companies >= 30", expand_radius_km=120),
                RelaxationStage(name="de", trigger="saturated_companies >= 60", geo_scope=GeoScope.DE),
                RelaxationStage(name="loose", trigger="saturated_companies >= 80", extra_job_types=["Vollzeit"]),
            ],
        ),
        search_keywords=SearchKeywords(),
        target_company_count=50,
    )


def test_trigger_parser():
    state = SourcingState(saturated_companies=30)
    assert _evaluate_trigger("saturated_companies >= 30", state)
    assert not _evaluate_trigger("saturated_companies > 30", state)
    assert _evaluate_trigger("saturated_companies < 31", state)
    assert not _evaluate_trigger("missing_var >= 1", state)


def test_stage_progression():
    eng = RelaxationEngine(_params())
    eng.state.saturated_companies = 31
    assert eng.next_stage_if_needed() == "wider"
    assert eng.state.radius_km == 120
    # second call without further trigger increments returns None
    assert eng.next_stage_if_needed() is None

    eng.state.saturated_companies = 70
    assert eng.next_stage_if_needed() == "de"
    assert eng.state.geo_scope == "DE"

    eng.state.saturated_companies = 90
    assert eng.next_stage_if_needed() == "loose"
    assert "Vollzeit" in eng.state.job_types


def test_stage_idempotent():
    eng = RelaxationEngine(_params())
    eng.state.saturated_companies = 100  # all triggers true
    seen = []
    while True:
        s = eng.next_stage_if_needed()
        if s is None:
            break
        seen.append(s)
    assert seen == ["wider", "de", "loose"]
