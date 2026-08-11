from __future__ import annotations

from pathlib import Path

from research_evolve.candidates import Candidate, CandidateDB
from research_evolve.conjecturer import ConjectureContext
from research_evolve.conjectures import Conjecture, ConjectureMemory, Predicate, ValueRef
from research_evolve.engine import ResearchEngine
from research_evolve.graph import ResearchGraph, ResearchNode
from research_evolve.mutation import FourLevelMutator, MutationLevel
from research_evolve.spec import Budget, ConjecturePolicy, Objective, ResearchSpec, SearchPolicy


def _write_identity_evaluator(path: Path) -> None:
    path.write_text(
        """
import json, sys
candidate = json.load(sys.stdin)
x = float(candidate['x'])
print(json.dumps({
    'valid': True,
    'score': x,
    'metrics': {'x': x},
    'behavior': {'representation': candidate.get('representation', 'direct')},
    'diagnostics': {}
}))
""".strip(),
        encoding="utf-8",
    )


def test_resume_prunes_partial_candidate_and_graph_artifacts(tmp_path: Path) -> None:
    evaluator = tmp_path / "evaluator.py"
    _write_identity_evaluator(evaluator)
    spec = ResearchSpec(
        name="resume-cleanup",
        problem="maximize x",
        objectives=[Objective("x")],
        behavior_dimensions=["representation"],
        budget=Budget(generations=1, population_size=1, seed=3, evaluator_timeout_seconds=5),
        search=SearchPolicy(checkpoint_interval=1),
    )
    workspace = tmp_path / "run"
    seeds = [{"x": 0, "representation": "direct"}]

    with ResearchEngine(spec, workspace=workspace, island_count=1) as engine:
        first = engine.run(seeds, evaluator)

    ghost = Candidate(
        id="ghost-candidate",
        payload={"x": 999, "representation": "direct"},
        generation=2,
        valid=True,
        score=999.0,
        metrics={"x": 999.0},
        behavior={"representation": "direct"},
    )
    db = CandidateDB(workspace / "candidates.sqlite3")
    db.upsert(ghost)
    db.close()

    graph = ResearchGraph(workspace / "research_graph.sqlite3")
    graph.add_node(
        ResearchNode(
            id=ghost.id,
            type="candidate",
            statement="ghost",
            status="valid",
            payload=ghost.to_dict(),
        )
    )
    evaluation = ResearchNode(type="evaluation", statement="ghost evaluation", status="passed", payload={"score": 999})
    graph.add_node(evaluation)
    graph.add_edge(ghost.id, "evaluated_as", evaluation.id)
    graph.close()

    with ResearchEngine(spec, workspace=workspace, island_count=1) as engine:
        resumed = engine.run(seeds, evaluator, resume=True)

    assert resumed.evaluated == first.evaluated
    db = CandidateDB(workspace / "candidates.sqlite3")
    assert db.get(ghost.id) is None
    db.close()
    graph = ResearchGraph(workspace / "research_graph.sqlite3")
    exported = graph.export()
    graph.close()
    node_ids = {node["id"] for node in exported["nodes"]}
    assert ghost.id not in node_ids
    assert evaluation.id not in node_ids


class TwoStepMutator(FourLevelMutator):
    def __init__(self) -> None:
        self.calls = 0

    @staticmethod
    def sample_level(rng):
        return MutationLevel.LOCAL

    def mutate(self, payload, level, rng):
        self.calls += 1
        child = dict(payload)
        child["x"] = 0 if self.calls == 1 else 5
        return child


class OnceConjecturer:
    name = "once-conjecturer"

    def __init__(self) -> None:
        self.calls = 0

    def propose(self, context: ConjectureContext, count: int):
        self.calls += 1
        if self.calls > 1:
            return []
        observation_ids = [str(item["id"]) for item in context.observations[:1]]
        evidence_ids = [str(item["id"]) for item in context.candidates[:1]]
        return [
            Conjecture(
                statement="x remains below 5",
                predicate=Predicate(ValueRef("metrics", "x"), "lt", right_constant=5),
                observation_ids=observation_ids,
                evidence_candidate_ids=evidence_ids,
            )
        ][:count]


def test_supported_conjecture_is_retested_and_refuted_by_later_archive_candidate(tmp_path: Path) -> None:
    evaluator = tmp_path / "evaluator.py"
    _write_identity_evaluator(evaluator)
    spec = ResearchSpec(
        name="retest-conjecture",
        problem="increase x",
        mode="hybrid",
        objectives=[Objective("x", direction="maximize")],
        behavior_dimensions=["representation"],
        budget=Budget(generations=2, population_size=1, seed=1, evaluator_timeout_seconds=5),
        conjecture=ConjecturePolicy(
            enabled=True,
            interval=1,
            observations_per_interval=4,
            conjectures_per_interval=1,
            context_candidates=4,
            context_conjectures=4,
            counterexample_trials=0,
            min_evidence=1,
            timeout_seconds=5,
        ),
    )
    workspace = tmp_path / "retest"
    with ResearchEngine(
        spec,
        workspace=workspace,
        island_count=1,
        mutator=TwoStepMutator(),
        conjecturer=OnceConjecturer(),
    ) as engine:
        summary = engine.run([{"x": 0, "representation": "direct"}], evaluator)

    assert summary.conjecture_count == 1
    assert summary.refuted_conjectures == 1
    memory = ConjectureMemory(workspace / "conjectures.sqlite3")
    rows = memory.recent_conjectures(5)
    counterexamples = memory.list_counterexamples(5)
    memory.close()
    assert rows[0]["status"] == "refuted"
    assert rows[0]["tests"] >= 2
    assert counterexamples[0]["payload"]["x"] == 5
