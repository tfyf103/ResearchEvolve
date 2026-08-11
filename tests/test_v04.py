from __future__ import annotations

from pathlib import Path

import pytest

from research_evolve.candidates import Candidate
from research_evolve.conjecturer import ConjectureContext
from research_evolve.conjectures import (
    Conjecture,
    ConjectureMemory,
    Counterexample,
    Observation,
    ObservationExtractor,
    Predicate,
    ValueRef,
)
from research_evolve.engine import ResearchEngine
from research_evolve.graph import ResearchGraph
from research_evolve.spec import Budget, ConjecturePolicy, Objective, ResearchSpec


def test_predicate_dsl_is_safe_and_testable() -> None:
    candidate = Candidate(
        payload={"x": 4, "nested": {"n": 2}},
        valid=True,
        score=-1.0,
        metrics={"distance": 3.0},
        behavior={"family": "A"},
    )
    assert Predicate(ValueRef("metrics", "distance"), "ge", right_constant=0).evaluate(candidate) is True
    assert Predicate(ValueRef("score"), "lt", right_constant=0).evaluate(candidate) is True
    assert Predicate(ValueRef("behavior", "family"), "eq", right_constant="A").evaluate(candidate) is True
    assert Predicate(ValueRef("metrics", "missing"), "ge", right_constant=0).evaluate(candidate) is None


def test_observation_extractor_summarizes_scores_metrics_and_behavior() -> None:
    candidates = [
        Candidate(payload={"x": 1}, valid=True, score=1.0, metrics={"m": 2.0}, behavior={"family": "A"}),
        Candidate(payload={"x": 2}, valid=True, score=3.0, metrics={"m": 5.0}, behavior={"family": "B"}),
    ]
    observations = ObservationExtractor().extract(candidates, generation=2, limit=10)
    kinds = {observation.kind for observation in observations}
    assert {"score_range", "metric_range", "behavior_values"}.issubset(kinds)
    metric = next(observation for observation in observations if observation.kind == "metric_range")
    assert metric.data["minimum"] == 2.0
    assert metric.data["maximum"] == 5.0


def test_conjecture_memory_never_promotes_empirical_support_to_proof(tmp_path: Path) -> None:
    memory = ConjectureMemory(tmp_path / "conjectures.sqlite3")
    observation = Observation("metric_range", "m >= 0 observed", 0, ["a"])
    memory.record_observation(observation)
    conjecture = Conjecture(
        statement="m is non-negative",
        predicate=Predicate(ValueRef("metrics", "m"), "ge", right_constant=0),
        observation_ids=[observation.id],
    )
    memory.record_conjecture(conjecture, generation=0)
    memory.record_test(conjecture.id, "a", 0, True, "archive")
    memory.record_test(conjecture.id, "b", 0, True, "archive")
    status = memory.refresh_status(conjecture.id, min_evidence=2)
    rows = memory.recent_conjectures(5)
    memory.close()
    assert status == "empirically_supported"
    assert rows[0]["status"] == "empirically_supported"
    assert "proved" not in rows[0]["status"]


def test_prune_after_generation_removes_partial_refutation_and_recomputes_status(tmp_path: Path) -> None:
    memory = ConjectureMemory(tmp_path / "conjectures.sqlite3")
    conjecture = Conjecture(
        statement="score <= 0",
        predicate=Predicate(ValueRef("score"), "le", right_constant=0),
    )
    memory.record_conjecture(conjecture, generation=0)
    memory.record_test(conjecture.id, "a", 0, True, "archive")
    memory.record_test(conjecture.id, "b", 1, False, "counterexample_search")
    memory.record_counterexample(
        Counterexample(conjecture.id, "b", 1, "counterexample_search", {"x": 1}, {}, 1.0)
    )
    assert memory.refresh_status(conjecture.id, min_evidence=1) == "refuted"
    memory.prune_after_generation(0, min_evidence=1)
    row = memory.recent_conjectures(1)[0]
    counters = memory.list_counterexamples(10)
    memory.close()
    assert row["status"] == "empirically_supported"
    assert counters == []


class DemoConjecturer:
    name = "demo-conjecturer-v1"

    def propose(self, context: ConjectureContext, count: int) -> list[Conjecture]:
        observation_ids = [str(item["id"]) for item in context.observations[:2]]
        evidence_ids = [str(item["id"]) for item in context.candidates[:2]]
        conjectures = [
            Conjecture(
                statement="canonical score is always strictly negative",
                predicate=Predicate(ValueRef("score"), "lt", right_constant=0),
                observation_ids=observation_ids,
                evidence_candidate_ids=evidence_ids,
                rationale="intentionally over-strong claim",
            ),
            Conjecture(
                statement="distance is always non-negative",
                predicate=Predicate(ValueRef("metrics", "distance"), "ge", right_constant=0),
                observation_ids=observation_ids,
                evidence_candidate_ids=evidence_ids,
                rationale="distance is empirically non-negative",
            ),
        ]
        return conjectures[:count]


class BrokenConjecturer:
    name = "broken-conjecturer"

    def propose(self, context: ConjectureContext, count: int) -> list[Conjecture]:
        raise RuntimeError("simulated conjecturer outage")


def _write_evaluator(path: Path) -> None:
    path.write_text(
        """
import json, sys
candidate = json.load(sys.stdin)
x = float(candidate['x'])
distance = abs(x - 5.0)
print(json.dumps({
    'valid': True,
    'score': -distance,
    'metrics': {'distance': distance},
    'behavior': {'representation': candidate.get('representation', 'direct')},
    'diagnostics': {}
}))
""".strip(),
        encoding="utf-8",
    )


def _spec() -> ResearchSpec:
    return ResearchSpec(
        name="conjecture-integration",
        problem="find x close to 5 and form empirical conjectures",
        mode="hybrid",
        objectives=[Objective("distance", direction="minimize")],
        behavior_dimensions=["representation"],
        budget=Budget(generations=1, population_size=1, seed=4, evaluator_timeout_seconds=5),
        conjecture=ConjecturePolicy(
            enabled=True,
            interval=1,
            observations_per_interval=8,
            conjectures_per_interval=2,
            context_candidates=8,
            context_conjectures=8,
            counterexample_trials=1,
            min_evidence=2,
            timeout_seconds=5,
        ),
    )


def test_engine_forms_conjectures_finds_counterexample_and_records_graph(tmp_path: Path) -> None:
    evaluator = tmp_path / "evaluator.py"
    _write_evaluator(evaluator)
    workspace = tmp_path / "run"
    with ResearchEngine(_spec(), workspace=workspace, island_count=2, conjecturer=DemoConjecturer()) as engine:
        summary = engine.run(
            [{"x": 0, "representation": "direct"}, {"x": 5, "representation": "direct"}],
            evaluator,
        )

    assert summary.conjecture_count == 2
    assert summary.refuted_conjectures >= 1
    assert summary.empirically_supported_conjectures >= 1
    assert summary.counterexample_count >= 1
    assert (workspace / "conjectures.sqlite3").exists()

    memory = ConjectureMemory(workspace / "conjectures.sqlite3")
    statuses = {item["status"] for item in memory.recent_conjectures(10)}
    counterexamples = memory.list_counterexamples(10)
    memory.close()
    assert "refuted" in statuses
    assert "empirically_supported" in statuses
    assert counterexamples

    graph = ResearchGraph(workspace / "research_graph.sqlite3")
    exported = graph.export()
    graph.close()
    node_types = {node["type"] for node in exported["nodes"]}
    relations = {edge["relation"] for edge in exported["edges"]}
    assert {"observation", "conjecture", "counterexample"}.issubset(node_types)
    assert {"suggests", "refutes", "counterexample_to"}.issubset(relations)


def test_conjecturer_failure_does_not_abort_search(tmp_path: Path) -> None:
    evaluator = tmp_path / "evaluator.py"
    _write_evaluator(evaluator)
    workspace = tmp_path / "broken"
    with ResearchEngine(_spec(), workspace=workspace, island_count=1, conjecturer=BrokenConjecturer()) as engine:
        summary = engine.run([{"x": 0, "representation": "direct"}], evaluator)
    assert summary.evaluated >= 2

    graph = ResearchGraph(workspace / "research_graph.sqlite3")
    exported = graph.export()
    graph.close()
    assert any(node["type"] == "conjecturer_error" for node in exported["nodes"])


def test_enabled_conjecture_loop_requires_conjecturer(tmp_path: Path) -> None:
    evaluator = tmp_path / "evaluator.py"
    _write_evaluator(evaluator)
    with ResearchEngine(_spec(), workspace=tmp_path / "missing", island_count=1) as engine:
        with pytest.raises(ValueError, match="no Conjecturer"):
            engine.run([{"x": 0}], evaluator)
