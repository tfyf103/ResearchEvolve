from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .candidates import Candidate, CandidateDB
from .evaluation import HiddenEvaluator
from .evolution import IslandPopulation
from .graph import ResearchGraph, ResearchNode
from .mutation import FourLevelMutator
from .spec import ResearchSpec


@dataclass(slots=True)
class RunSummary:
    research_name: str
    evaluated: int
    valid: int
    archive_size: int
    best_candidate_id: str | None
    best_score: float | None
    best_payload: dict[str, Any] | None
    workspace: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResearchEngine:
    """Minimal v0.1 discovery loop: mutate -> evaluate -> archive -> remember."""

    def __init__(
        self,
        spec: ResearchSpec,
        workspace: str | Path = ".researchevolve/run",
        island_count: int = 4,
        mutator: FourLevelMutator | None = None,
    ) -> None:
        spec.validate()
        self.spec = spec
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.rng = random.Random(spec.budget.seed)
        self.db = CandidateDB(self.workspace / "candidates.sqlite3")
        self.graph = ResearchGraph(self.workspace / "research_graph.sqlite3")
        self.population = IslandPopulation(island_count, spec.behavior_dimensions)
        self.mutator = mutator or FourLevelMutator()
        self.evaluated = 0
        self.valid = 0

        self.problem_node_id = self.graph.add_node(
            ResearchNode(
                type="problem",
                statement=spec.problem,
                status="active",
                payload=spec.to_dict(),
            )
        )

    def _record_candidate(self, candidate: Candidate) -> None:
        self.db.upsert(candidate)
        self.graph.add_node(
            ResearchNode(
                id=candidate.id,
                type="candidate",
                statement=f"Candidate {candidate.id[:8]} (generation {candidate.generation})",
                status="valid" if candidate.valid else "rejected",
                payload=candidate.to_dict(),
                created_at=candidate.created_at,
            )
        )
        self.graph.add_edge(self.problem_node_id, "investigates", candidate.id)
        for parent_id in candidate.parent_ids:
            self.graph.add_edge(parent_id, "mutated_to", candidate.id, {"level": candidate.mutation_level})

        result_node = ResearchNode(
            type="evaluation",
            statement=f"score={candidate.score}" if candidate.valid else "invalid candidate",
            status="passed" if candidate.valid else "failed",
            payload={
                "score": candidate.score,
                "metrics": candidate.metrics,
                "behavior": candidate.behavior,
                "diagnostics": candidate.diagnostics,
            },
        )
        self.graph.add_node(result_node)
        self.graph.add_edge(candidate.id, "evaluated_as", result_node.id)

    def _evaluate(self, candidate: Candidate, evaluator: HiddenEvaluator, island_index: int) -> None:
        result = evaluator.evaluate(candidate.payload)
        candidate.valid = result.valid
        candidate.score = result.score
        candidate.metrics = result.metrics
        candidate.behavior = result.behavior
        candidate.diagnostics = result.diagnostics
        self.evaluated += 1
        self.valid += int(result.valid)
        self._record_candidate(candidate)
        self.population.add(candidate, island_index)

    def run(self, seed_payloads: Iterable[dict[str, Any]], evaluator_path: str | Path) -> RunSummary:
        evaluator = HiddenEvaluator(evaluator_path, self.spec.budget.evaluator_timeout_seconds)
        seeds = list(seed_payloads)
        if not seeds:
            raise ValueError("at least one seed payload is required")

        for index, payload in enumerate(seeds):
            candidate = Candidate(payload=dict(payload), generation=0, mutation_level="seed")
            self._evaluate(candidate, evaluator, index % len(self.population.islands))

        for generation in range(1, self.spec.budget.generations + 1):
            for _ in range(self.spec.budget.population_size):
                selected = self.population.sample_parent(self.rng)
                if selected is None:
                    break
                island_index, parent = selected
                level = self.mutator.sample_level(self.rng)
                child_payload = self.mutator.mutate(parent.payload, level, self.rng)
                child = Candidate(
                    payload=child_payload,
                    parent_ids=[parent.id],
                    mutation_level=level.value,
                    generation=generation,
                )
                self._evaluate(child, evaluator, island_index)

            if generation % 5 == 0:
                self.population.migrate(migrants_per_island=1)

        elites = self.population.global_elites()
        best = elites[0] if elites else None
        summary = RunSummary(
            research_name=self.spec.name,
            evaluated=self.evaluated,
            valid=self.valid,
            archive_size=len(elites),
            best_candidate_id=best.id if best else None,
            best_score=best.score if best else None,
            best_payload=best.payload if best else None,
            workspace=str(self.workspace),
        )
        (self.workspace / "summary.json").write_text(json.dumps(summary.to_dict(), indent=2), encoding="utf-8")
        return summary

    def close(self) -> None:
        self.db.close()
        self.graph.close()

    def __enter__(self) -> "ResearchEngine":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
