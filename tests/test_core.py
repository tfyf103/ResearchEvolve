from __future__ import annotations

import random

from research_evolve.candidates import Candidate, CandidateDB
from research_evolve.evolution import MAPElitesArchive
from research_evolve.graph import ResearchGraph, ResearchNode
from research_evolve.mutation import FourLevelMutator, MutationLevel
from research_evolve.spec import Objective, ResearchSpec


def test_research_spec_validation() -> None:
    spec = ResearchSpec(name="demo", problem="maximize something", objectives=[Objective("quality")])
    spec.validate()
    assert spec.to_dict()["objectives"][0]["name"] == "quality"


def test_candidate_db_roundtrip(tmp_path) -> None:
    db = CandidateDB(tmp_path / "candidates.sqlite3")
    candidate = Candidate(payload={"x": 1}, valid=True, score=2.0, behavior={"family": "a"})
    db.upsert(candidate)
    loaded = db.get(candidate.id)
    db.close()
    assert loaded is not None
    assert loaded.payload == {"x": 1}
    assert loaded.score == 2.0


def test_map_elites_preserves_behavioral_diversity() -> None:
    archive = MAPElitesArchive(["family"])
    a = Candidate(payload={}, valid=True, score=1.0, behavior={"family": "A"})
    b = Candidate(payload={}, valid=True, score=0.5, behavior={"family": "B"})
    worse_a = Candidate(payload={}, valid=True, score=0.1, behavior={"family": "A"})
    assert archive.add(a)
    assert archive.add(b)
    assert not archive.add(worse_a)
    assert len(archive.cells) == 2


def test_four_mutation_levels_return_payloads() -> None:
    rng = random.Random(1)
    mutator = FourLevelMutator()
    seed = {"x": 10, "structure": [1], "representation": "direct"}
    for level in MutationLevel:
        child = mutator.mutate(seed, level, rng)
        assert isinstance(child, dict)
        assert child is not seed


def test_research_graph_links_nodes(tmp_path) -> None:
    graph = ResearchGraph(tmp_path / "graph.sqlite3")
    a = ResearchNode(type="hypothesis", statement="A")
    b = ResearchNode(type="experiment", statement="B")
    graph.add_node(a)
    graph.add_node(b)
    graph.add_edge(a.id, "tested_by", b.id)
    neighbors = graph.neighbors(a.id)
    graph.close()
    assert neighbors[0]["id"] == b.id
    assert neighbors[0]["relation"] == "tested_by"
