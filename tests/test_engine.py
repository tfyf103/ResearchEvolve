from __future__ import annotations

from research_evolve.engine import ResearchEngine
from research_evolve.spec import Budget, Objective, ResearchSpec


def test_engine_runs_end_to_end(tmp_path) -> None:
    evaluator = tmp_path / "evaluator.py"
    evaluator.write_text(
        """
import json, sys
candidate = json.load(sys.stdin)
x = float(candidate['x'])
print(json.dumps({
    'valid': True,
    'score': -abs(x - 5.0),
    'metrics': {'x': x},
    'behavior': {'representation': candidate.get('representation', 'direct')},
    'diagnostics': {}
}))
""".strip(),
        encoding="utf-8",
    )

    spec = ResearchSpec(
        name="integration",
        problem="find x close to 5",
        objectives=[Objective("quality")],
        behavior_dimensions=["representation"],
        budget=Budget(generations=2, population_size=4, seed=3, evaluator_timeout_seconds=5),
    )

    workspace = tmp_path / "run"
    with ResearchEngine(spec, workspace=workspace, island_count=2) as engine:
        summary = engine.run([{"x": 0, "representation": "direct"}], evaluator)

    assert summary.evaluated >= 1
    assert summary.valid >= 1
    assert summary.best_candidate_id is not None
    assert (workspace / "summary.json").exists()
    assert (workspace / "candidates.sqlite3").exists()
    assert (workspace / "research_graph.sqlite3").exists()
