from __future__ import annotations

from pathlib import Path

import pytest

from research_evolve.candidates import Candidate
from research_evolve.domains.qldpc.common import code_parameters, exact_distance_metrics
from research_evolve.engine import ResearchEngine
from research_evolve.evaluation import EvaluatorCascade
from research_evolve.evolution import NoveltyArchive, ParetoArchive
from research_evolve.spec import Budget, Objective, ResearchSpec, SearchPolicy


def test_evaluator_cascade_short_circuits(tmp_path: Path) -> None:
    marker = tmp_path / "expensive-ran"
    cheap = tmp_path / "cheap.py"
    expensive = tmp_path / "expensive.py"
    cheap.write_text(
        "import json, sys\njson.load(sys.stdin)\nprint(json.dumps({'valid': False, 'diagnostics': {'reason': 'cheap reject'}}))\n",
        encoding="utf-8",
    )
    expensive.write_text(
        f"import json, sys\njson.load(sys.stdin)\nopen({str(marker)!r}, 'w').write('ran')\nprint(json.dumps({{'valid': True, 'score': 1.0}}))\n",
        encoding="utf-8",
    )

    result = EvaluatorCascade([cheap, expensive], timeout_seconds=5).evaluate({"x": 1})
    assert not result.valid
    assert result.diagnostics["rejected_at"] == "cheap"
    assert not marker.exists()


def test_pareto_archive_respects_objective_directions() -> None:
    archive = ParetoArchive([Objective("distance", "maximize"), Objective("cost", "minimize")])
    a = Candidate(payload={}, valid=True, score=1.0, metrics={"distance": 5.0, "cost": 5.0})
    b = Candidate(payload={}, valid=True, score=1.0, metrics={"distance": 6.0, "cost": 6.0})
    c = Candidate(payload={}, valid=True, score=1.0, metrics={"distance": 6.0, "cost": 4.0})
    archive.add(a)
    archive.add(b)
    archive.add(c)
    assert [candidate.id for candidate in archive.candidates()] == [c.id]


def test_novelty_archive_rewards_different_behavior() -> None:
    archive = NoveltyArchive(["family", "size"], k=2)
    base = Candidate(payload={}, valid=True, score=1.0, behavior={"family": "A", "size": 5})
    archive.add(base)
    same = Candidate(payload={}, valid=True, score=1.0, behavior={"family": "A", "size": 5})
    different = Candidate(payload={}, valid=True, score=1.0, behavior={"family": "B", "size": 7})
    assert archive.score(different) > archive.score(same)


def _write_simple_evaluator(path: Path) -> None:
    path.write_text(
        """
import json, sys
candidate = json.load(sys.stdin)
x = float(candidate['x'])
print(json.dumps({
    'valid': True,
    'score': -abs(x - 3.0),
    'metrics': {'quality': -abs(x - 3.0)},
    'behavior': {'representation': candidate.get('representation', 'direct')}
}))
""".strip(),
        encoding="utf-8",
    )


def test_checkpoint_resume_restores_search_state(tmp_path: Path) -> None:
    evaluator = tmp_path / "evaluator.py"
    _write_simple_evaluator(evaluator)
    spec = ResearchSpec(
        name="resume-test",
        problem="find x close to 3",
        objectives=[Objective("quality")],
        behavior_dimensions=["representation"],
        budget=Budget(generations=1, population_size=3, seed=9, evaluator_timeout_seconds=5),
        search=SearchPolicy(checkpoint_interval=1),
    )
    workspace = tmp_path / "run"
    seeds = [{"x": 0, "representation": "direct"}]

    with ResearchEngine(spec, workspace=workspace, island_count=2) as engine:
        first = engine.run(seeds, evaluator)
    with ResearchEngine(spec, workspace=workspace, island_count=2) as engine:
        resumed = engine.run(seeds, evaluator, resume=True)

    assert resumed.evaluated == first.evaluated
    assert resumed.best_candidate_id is not None
    assert resumed.manifest_fingerprint == first.manifest_fingerprint
    assert (workspace / "checkpoint.json").exists()
    assert (workspace / "manifest.json").exists()
    assert (workspace / "pareto.json").exists()


def test_resume_rejects_changed_inputs(tmp_path: Path) -> None:
    evaluator = tmp_path / "evaluator.py"
    _write_simple_evaluator(evaluator)
    spec = ResearchSpec(
        name="resume-mismatch",
        problem="find x close to 3",
        objectives=[Objective("quality")],
        budget=Budget(generations=1, population_size=1, seed=1, evaluator_timeout_seconds=5),
    )
    workspace = tmp_path / "run"
    with ResearchEngine(spec, workspace=workspace) as engine:
        engine.run([{"x": 0}], evaluator)
    with ResearchEngine(spec, workspace=workspace) as engine:
        with pytest.raises(ValueError, match="checkpoint inputs differ"):
            engine.run([{"x": 1}], evaluator, resume=True)


def test_qldpc_reference_seed_has_expected_small_code_parameters() -> None:
    candidate = {
        "family": "bicycle",
        "representation": "circulant",
        "size": 5,
        "a_shifts": [0, 1],
        "b_shifts": [0, 2],
    }
    parameters = code_parameters(candidate)
    distance = exact_distance_metrics(candidate)
    assert parameters["n"] == 10.0
    assert parameters["k"] == 2.0
    assert distance is not None
    assert distance["distance"] == 3.0
