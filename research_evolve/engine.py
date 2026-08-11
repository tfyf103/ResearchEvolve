from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .candidates import Candidate, CandidateDB
from .checkpoint import CheckpointStore, SearchCheckpoint
from .evaluation import EvaluatorCascade
from .evolution import IslandPopulation, NoveltyArchive, ParetoArchive
from .graph import ResearchGraph, ResearchNode
from .mutation import FourLevelMutator
from .reproducibility import build_manifest, stable_json_hash, write_manifest
from .spec import ResearchSpec


@dataclass(slots=True)
class RunSummary:
    research_name: str
    evaluated: int
    valid: int
    archive_size: int
    pareto_size: int
    generation_completed: int
    best_candidate_id: str | None
    best_score: float | None
    best_payload: dict[str, Any] | None
    manifest_fingerprint: str
    workspace: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResearchEngine:
    """v0.2 discovery loop: mutate -> cascade-evaluate -> diversify -> archive -> checkpoint."""

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
        self.pareto = ParetoArchive(spec.objectives)
        self.novelty = NoveltyArchive(spec.behavior_dimensions, k=spec.search.novelty_k)
        self.mutator = mutator or FourLevelMutator()
        self.checkpoints = CheckpointStore(self.workspace / "checkpoint.json")
        self.evaluated = 0
        self.valid = 0

        problem_key = stable_json_hash({"name": spec.name, "problem": spec.problem})[:20]
        self.problem_node_id = self.graph.add_node(
            ResearchNode(
                id=f"problem-{problem_key}",
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

    def _evaluate(self, candidate: Candidate, evaluator: EvaluatorCascade, island_index: int) -> None:
        result = evaluator.evaluate(candidate.payload)
        candidate.valid = result.valid
        candidate.score = result.score
        candidate.metrics = result.metrics
        candidate.behavior = result.behavior
        candidate.diagnostics = dict(result.diagnostics)

        novelty_score = self.novelty.score(candidate) if result.valid else 0.0
        search_diagnostics = dict(candidate.diagnostics.get("search", {}))
        search_diagnostics["novelty"] = novelty_score
        candidate.diagnostics["search"] = search_diagnostics

        self.evaluated += 1
        self.valid += int(result.valid)
        self._record_candidate(candidate)
        self.population.add(candidate, island_index)
        self.pareto.add(candidate)
        self.novelty.add(candidate)

    def _resolve_candidates(self, candidate_ids: Iterable[str]) -> list[Candidate]:
        resolved: list[Candidate] = []
        for candidate_id in candidate_ids:
            candidate = self.db.get(candidate_id)
            if candidate is not None:
                resolved.append(candidate)
        return resolved

    def _restore_checkpoint(self, checkpoint: SearchCheckpoint) -> None:
        self.evaluated = checkpoint.evaluated
        self.valid = checkpoint.valid
        self.rng.setstate(checkpoint.rng_state)
        islands = [self._resolve_candidates(candidate_ids) for candidate_ids in checkpoint.island_candidate_ids]
        self.population.restore(islands)
        self.pareto.restore(self._resolve_candidates(checkpoint.pareto_candidate_ids))
        self.novelty.restore(self._resolve_candidates(checkpoint.novelty_candidate_ids))

    def _save_frontier(self) -> None:
        frontier = [candidate.to_dict() for candidate in self.pareto.candidates()]
        (self.workspace / "pareto.json").write_text(json.dumps(frontier, indent=2), encoding="utf-8")

    def _save_checkpoint(self, generation: int, manifest_fingerprint: str) -> None:
        checkpoint = SearchCheckpoint(
            generation=generation,
            evaluated=self.evaluated,
            valid=self.valid,
            rng_state=self.rng.getstate(),
            island_candidate_ids=self.population.candidate_ids_by_island(),
            pareto_candidate_ids=[candidate.id for candidate in self.pareto.candidates()],
            novelty_candidate_ids=self.novelty.candidate_ids(),
            manifest_fingerprint=manifest_fingerprint,
        )
        self.checkpoints.save(checkpoint)
        self._save_frontier()

    def run(
        self,
        seed_payloads: Iterable[dict[str, Any]],
        evaluator_paths: str | Path | Iterable[str | Path],
        *,
        resume: bool = False,
        domain_pack: str | None = None,
    ) -> RunSummary:
        if isinstance(evaluator_paths, (str, Path)):
            paths = [Path(evaluator_paths)]
        else:
            paths = [Path(path) for path in evaluator_paths]
        evaluator = EvaluatorCascade(paths, self.spec.budget.evaluator_timeout_seconds)
        seeds = [dict(payload) for payload in seed_payloads]
        if not seeds:
            raise ValueError("at least one seed payload is required")

        mutator_name = f"{self.mutator.__class__.__module__}:{self.mutator.__class__.__qualname__}"
        manifest = build_manifest(
            self.spec,
            seeds,
            paths,
            mutator_name=mutator_name,
            domain_pack=domain_pack,
        )
        manifest_fingerprint = str(manifest["fingerprint"])
        manifest_path = self.workspace / "manifest.json"

        if resume:
            if not self.checkpoints.exists():
                raise ValueError("--resume requested but checkpoint.json does not exist")
            checkpoint = self.checkpoints.load()
            if checkpoint.manifest_fingerprint != manifest_fingerprint:
                raise ValueError("checkpoint inputs differ from the current spec/seeds/evaluators/mutator")
            self._restore_checkpoint(checkpoint)
            start_generation = checkpoint.generation + 1
        else:
            if self.checkpoints.exists():
                raise ValueError("workspace already contains a checkpoint; use --resume or choose a new workspace")
            write_manifest(manifest_path, manifest)
            for index, payload in enumerate(seeds):
                candidate = Candidate(payload=payload, generation=0, mutation_level="seed")
                self._evaluate(candidate, evaluator, index % len(self.population.islands))
            self._save_checkpoint(0, manifest_fingerprint)
            start_generation = 1

        generation_completed = start_generation - 1
        for generation in range(start_generation, self.spec.budget.generations + 1):
            for _ in range(self.spec.budget.population_size):
                selected = self.population.sample_parent(
                    self.rng,
                    novelty_probability=self.spec.search.novelty_probability,
                )
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

            if generation % self.spec.search.migration_interval == 0:
                self.population.migrate(migrants_per_island=self.spec.search.migrants_per_island)
            generation_completed = generation
            if generation % self.spec.search.checkpoint_interval == 0:
                self._save_checkpoint(generation, manifest_fingerprint)

        self._save_checkpoint(generation_completed, manifest_fingerprint)
        elites = self.population.global_elites()
        best = elites[0] if elites else None
        summary = RunSummary(
            research_name=self.spec.name,
            evaluated=self.evaluated,
            valid=self.valid,
            archive_size=len(elites),
            pareto_size=len(self.pareto.members),
            generation_completed=generation_completed,
            best_candidate_id=best.id if best else None,
            best_score=best.score if best else None,
            best_payload=best.payload if best else None,
            manifest_fingerprint=manifest_fingerprint,
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
