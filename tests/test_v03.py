from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from research_evolve.engine import ResearchEngine
from research_evolve.explorer import CommandExplorer, ResearchContext
from research_evolve.graph import ResearchGraph
from research_evolve.ideas import IdeaGenome, IdeaMemory, ResearchProposal, SemanticPatch, realize_proposal
from research_evolve.spec import Budget, ExplorerPolicy, Objective, ResearchSpec


def _proposal(parent_id: str, target: int = 5) -> ResearchProposal:
    return ResearchProposal(
        kind="semantic_mutation",
        parent_ids=[parent_id],
        patch=SemanticPatch(set_values={"x": target, "representation": "semantic"}),
        genome=IdeaGenome(
            representation="direct",
            construction="goal-directed parameter update",
            mechanisms=["semantic target inference"],
            tags=["test"],
        ),
        rationale=f"test x={target}",
        expected_effects={"distance": "decrease"},
        confidence=0.9,
    )


def test_semantic_mutation_and_crossover_are_deterministic() -> None:
    mutation = ResearchProposal(
        kind="semantic_mutation",
        parent_ids=["a"],
        patch=SemanticPatch(set_values={"x": 3}, append_values={"terms": [2]}, delete_keys=["drop"]),
        genome=IdeaGenome(construction="mutation"),
    )
    mutated = realize_proposal(mutation, {"a": {"x": 1, "terms": [1], "drop": True}})
    assert mutated == {"x": 3, "terms": [1, 2]}

    crossover = ResearchProposal(
        kind="semantic_crossover",
        parent_ids=["a", "b"],
        inherit_from_secondary=["representation", "shape"],
        patch=SemanticPatch(set_values={"x": 9}),
        genome=IdeaGenome(construction="crossover"),
    )
    crossed = realize_proposal(
        crossover,
        {
            "a": {"x": 1, "representation": "matrix", "shape": [2, 2]},
            "b": {"x": 7, "representation": "graph", "shape": [4, 1]},
        },
    )
    assert crossed == {"x": 9, "representation": "graph", "shape": [4, 1]}


def test_semantic_crossover_rejects_missing_secondary_field() -> None:
    proposal = ResearchProposal(
        kind="semantic_crossover",
        parent_ids=["a", "b"],
        inherit_from_secondary=["missing"],
        genome=IdeaGenome(construction="bad-crossover"),
    )
    with pytest.raises(ValueError, match="does not contain"):
        realize_proposal(proposal, {"a": {"missing": 1}, "b": {"other": 2}})


def test_idea_memory_records_outcomes(tmp_path: Path) -> None:
    memory = IdeaMemory(tmp_path / "ideas.sqlite3")
    proposal = _proposal("parent")
    memory.record_proposal(proposal, generation=2)
    memory.record_outcome(proposal.id, "candidate", True, 3.5)
    feedback = memory.recent_feedback(10)
    ideas = memory.list_ideas(10)
    memory.close()

    assert feedback[0]["status"] == "accepted"
    assert feedback[0]["candidate_id"] == "candidate"
    assert feedback[0]["score"] == 3.5
    assert ideas[0]["id"] == proposal.genome.id


def test_idea_memory_prunes_partial_generations(tmp_path: Path) -> None:
    memory = IdeaMemory(tmp_path / "ideas.sqlite3")
    keep = _proposal("p1", 4)
    drop = _proposal("p2", 6)
    memory.record_proposal(keep, generation=1)
    memory.record_proposal(drop, generation=2)
    removed = memory.prune_after_generation(1)
    remaining = memory.list_proposals(10)
    ideas = memory.list_ideas(10)
    memory.close()

    assert removed == 1
    assert [item["proposal_id"] for item in remaining] == [keep.id]
    assert [item["id"] for item in ideas] == [keep.genome.id]


def test_command_explorer_parses_strict_json_protocol(tmp_path: Path) -> None:
    script = tmp_path / "explorer.py"
    script.write_text(
        """
import json, sys
request = json.load(sys.stdin)
parent = request['context']['candidates'][0]['id']
print(json.dumps({'proposals': [{
    'kind': 'semantic_mutation',
    'parent_ids': [parent],
    'patch': {'set': {'x': 5}},
    'genome': {'construction': 'scripted'},
    'rationale': 'move x',
    'confidence': 0.8
}]}))
""".strip(),
        encoding="utf-8",
    )
    explorer = CommandExplorer([sys.executable, str(script)], timeout_seconds=5)
    context = ResearchContext(
        problem="demo",
        generation=1,
        objectives=[],
        constraints=[],
        candidates=[{"id": "p1"}],
    )
    proposals = explorer.propose(context, 1)
    assert proposals[0].parent_ids == ["p1"]
    assert proposals[0].patch.set_values["x"] == 5
    assert "move x" in proposals[0].rationale


def test_command_explorer_identity_changes_with_wrapper_contents(tmp_path: Path) -> None:
    script = tmp_path / "explorer.py"
    script.write_text("print('one')", encoding="utf-8")
    first = CommandExplorer([sys.executable, str(script)]).name
    script.write_text("print('two')", encoding="utf-8")
    second = CommandExplorer([sys.executable, str(script)]).name
    assert first != second
    assert str(script) not in first


class DemoExplorer:
    name = "demo-explorer-v1"

    def propose(self, context: ResearchContext, count: int) -> list[ResearchProposal]:
        parent_id = str(context.candidates[0]["id"])
        return [_proposal(parent_id, 5)][:count]


class BrokenExplorer:
    name = "broken-explorer"

    def propose(self, context: ResearchContext, count: int) -> list[ResearchProposal]:
        raise RuntimeError("simulated explorer outage")


def _write_evaluator(path: Path) -> None:
    path.write_text(
        """
import json, sys
candidate = json.load(sys.stdin)
x = float(candidate['x'])
print(json.dumps({
    'valid': True,
    'score': -abs(x - 5.0),
    'metrics': {'distance': abs(x - 5.0)},
    'behavior': {'representation': candidate.get('representation', 'direct')},
    'diagnostics': {}
}))
""".strip(),
        encoding="utf-8",
    )


def _spec() -> ResearchSpec:
    return ResearchSpec(
        name="semantic-integration",
        problem="find x close to 5",
        objectives=[Objective("distance", direction="minimize")],
        behavior_dimensions=["representation"],
        budget=Budget(generations=1, population_size=1, seed=2, evaluator_timeout_seconds=5),
        explorer=ExplorerPolicy(
            enabled=True,
            interval=1,
            proposals_per_interval=1,
            context_candidates=4,
            feedback_items=4,
            timeout_seconds=5,
        ),
    )


def test_engine_evaluates_semantic_proposals_and_persists_lineage(tmp_path: Path) -> None:
    evaluator = tmp_path / "evaluator.py"
    _write_evaluator(evaluator)
    workspace = tmp_path / "run"

    with ResearchEngine(_spec(), workspace=workspace, island_count=2, explorer=DemoExplorer()) as engine:
        summary = engine.run([{"x": 0, "representation": "direct"}], evaluator)

    assert summary.best_score == 0.0
    assert (workspace / "ideas.sqlite3").exists()

    memory = IdeaMemory(workspace / "ideas.sqlite3")
    proposals = memory.list_proposals(10)
    memory.close()
    assert proposals[0]["status"] == "accepted"
    assert proposals[0]["valid"] is True

    graph = ResearchGraph(workspace / "research_graph.sqlite3")
    exported = graph.export()
    graph.close()
    node_types = {node["type"] for node in exported["nodes"]}
    relations = {edge["relation"] for edge in exported["edges"]}
    assert {"idea", "proposal", "candidate", "evaluation"}.issubset(node_types)
    assert {"inspired", "proposed_as", "realized_as", "expresses"}.issubset(relations)


def test_explorer_failure_is_recorded_without_aborting_search(tmp_path: Path) -> None:
    evaluator = tmp_path / "evaluator.py"
    _write_evaluator(evaluator)
    workspace = tmp_path / "broken"
    with ResearchEngine(_spec(), workspace=workspace, island_count=1, explorer=BrokenExplorer()) as engine:
        summary = engine.run([{"x": 0, "representation": "direct"}], evaluator)
    assert summary.evaluated >= 2

    graph = ResearchGraph(workspace / "research_graph.sqlite3")
    exported = graph.export()
    graph.close()
    assert any(node["type"] == "explorer_error" for node in exported["nodes"])


def test_enabled_explorer_requires_an_explorer_instance(tmp_path: Path) -> None:
    evaluator = tmp_path / "evaluator.py"
    _write_evaluator(evaluator)
    with ResearchEngine(_spec(), workspace=tmp_path / "missing", island_count=1) as engine:
        with pytest.raises(ValueError, match="no Explorer"):
            engine.run([{"x": 0}], evaluator)
