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
from .explorer import Explorer, ResearchContext
from .graph import ResearchGraph, ResearchNode
from .ideas import IdeaMemory, ResearchProposal, realize_proposal
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
    """v0.3 loop: deterministic evolution + optional semantic Explorer proposals."""

    def __init__(
        self,
        spec: ResearchSpec,
        workspace: str | Path = ".researchevolve/run",
        island_count: int = 4,
        mutator: FourLevelMutator | None = None,
        explorer: Explorer | None = None,
    ) -> None:
        spec.validate()
        self.spec = spec
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.rng = random.Random(spec.budget.seed)
        self.db = CandidateDB(self.workspace / "candidates.sqlite3")
        self.graph = ResearchGraph(self.workspace / "research_graph.sqlite3")
        self.ideas = IdeaMemory(self.workspace / "ideas.sqlite3")
        self.population = IslandPopulation(island_count, spec.behavior_dimensions)
        self.pareto = ParetoArchive(spec.objectives)
        self.novelty = NoveltyArchive(spec.behavior_dimensions, k=spec.search.novelty_k)
        self.mutator = mutator or FourLevelMutator()
        self.explorer = explorer
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

    def _record_proposal(self, proposal: ResearchProposal, generation: int, status: str = "proposed", **extra: Any) -> None:
        self.graph.add_node(
            ResearchNode(
                id=proposal.genome.id,
                type="idea",
                statement=f"{proposal.genome.construction} via {proposal.genome.representation}",
                status="active" if status == "proposed" else status,
                payload=proposal.genome.to_dict(),
                created_at=proposal.created_at,
            )
        )
        payload = proposal.to_dict()
        payload["generation"] = generation
        payload.update(extra)
        self.graph.add_node(
            ResearchNode(
                id=proposal.id,
                type="proposal",
                statement=proposal.rationale or proposal.kind,
                status=status,
                payload=payload,
                created_at=proposal.created_at,
            )
        )
        self.graph.add_edge(proposal.genome.id, "proposed_as", proposal.id)
        for parent_id in proposal.parent_ids:
            self.graph.add_edge(parent_id, "inspired", proposal.id, {"kind": proposal.kind})

    def _evaluate(
        self,
        candidate: Candidate,
        evaluator: EvaluatorCascade,
        island_index: int,
        proposal: ResearchProposal | None = None,
    ) -> None:
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

        if proposal is not None:
            candidate.diagnostics["research"] = {
                "proposal_id": proposal.id,
                "idea_id": proposal.genome.id,
                "proposal_kind": proposal.kind,
                "confidence": proposal.confidence,
            }

        self.evaluated += 1
        self.valid += int(result.valid)
        self._record_candidate(candidate)
        self.population.add(candidate, island_index)
        self.pareto.add(candidate)
        self.novelty.add(candidate)

        if proposal is not None:
            self.ideas.record_outcome(proposal.id, candidate.id, result.valid, result.score)
            status = "accepted" if result.valid else "rejected"
            self._record_proposal(
                proposal,
                candidate.generation,
                status=status,
                candidate_id=candidate.id,
                valid=result.valid,
                score=result.score,
                metrics=result.metrics,
            )
            self.graph.add_edge(proposal.id, "realized_as", candidate.id)
            self.graph.add_edge(candidate.id, "expresses", proposal.genome.id)

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

    @staticmethod
    def _novelty_value(candidate: Candidate) -> float:
        search = candidate.diagnostics.get("search", {}) if isinstance(candidate.diagnostics, dict) else {}
        try:
            return float(search.get("novelty", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _candidate_snapshot(self, candidate: Candidate) -> dict[str, Any]:
        return {
            "id": candidate.id,
            "generation": candidate.generation,
            "payload": candidate.payload,
            "score": candidate.score,
            "metrics": candidate.metrics,
            "behavior": candidate.behavior,
            "novelty": self._novelty_value(candidate),
            "idea_genome": self.ideas.genome_for_candidate(candidate.id),
        }

    def _build_research_context(self, generation: int) -> ResearchContext:
        limit = self.spec.explorer.context_candidates
        pool: list[Candidate] = []
        pool.extend(self.db.best(limit))
        pool.extend(self.pareto.candidates()[:limit])
        novelty_ranked = sorted(self.novelty.members.values(), key=self._novelty_value, reverse=True)
        pool.extend(novelty_ranked[:limit])

        unique: list[Candidate] = []
        seen: set[str] = set()
        for candidate in pool:
            if candidate.id in seen:
                continue
            seen.add(candidate.id)
            unique.append(candidate)
            if len(unique) >= limit:
                break

        return ResearchContext(
            problem=self.spec.problem,
            generation=generation,
            objectives=[asdict(item) for item in self.spec.objectives],
            constraints=[asdict(item) for item in self.spec.constraints],
            candidates=[self._candidate_snapshot(candidate) for candidate in unique],
            pareto=[self._candidate_snapshot(candidate) for candidate in self.pareto.candidates()[:limit]],
            feedback=self.ideas.recent_feedback(self.spec.explorer.feedback_items),
            metadata={
                "domain": self.spec.domain,
                "behavior_dimensions": list(self.spec.behavior_dimensions),
                "instruction": (
                    "Propose auditable semantic mutations/crossovers. Do not claim validity; "
                    "every realized candidate will be independently evaluated."
                ),
            },
        )

    def _island_for_parent(self, candidate_id: str) -> int:
        for index, island in enumerate(self.population.islands):
            if any(candidate.id == candidate_id for candidate in island.archive.cells.values()):
                return index
        return self.rng.randrange(len(self.population.islands))

    def _record_explorer_error(self, generation: int, message: str) -> None:
        node = ResearchNode(
            type="explorer_error",
            statement=message[:240],
            status="failed",
            payload={"generation": generation, "explorer": getattr(self.explorer, "name", None), "error": message},
        )
        self.graph.add_node(node)
        self.graph.add_edge(self.problem_node_id, "explorer_failed", node.id)

    def _run_explorer(self, generation: int, evaluator: EvaluatorCascade) -> None:
        if not self.spec.explorer.enabled or self.explorer is None:
            return
        if generation % self.spec.explorer.interval != 0:
            return

        context = self._build_research_context(generation)
        allowed_ids = {str(item["id"]) for item in context.candidates}
        allowed_ids.update(str(item["id"]) for item in context.pareto)
        if not allowed_ids:
            return

        try:
            proposals = self.explorer.propose(context, self.spec.explorer.proposals_per_interval)
        except Exception as exc:  # Explorer failures should not invalidate the deterministic search run.
            self._record_explorer_error(generation, str(exc))
            return

        for proposal in proposals[: self.spec.explorer.proposals_per_interval]:
            self.ideas.record_proposal(proposal, generation)
            self._record_proposal(proposal, generation)
            unavailable = [parent_id for parent_id in proposal.parent_ids if parent_id not in allowed_ids]
            if unavailable:
                reason = f"proposal used parent ids outside the supplied research context: {unavailable}"
                self.ideas.mark_invalid(proposal.id, reason)
                self._record_proposal(proposal, generation, status="invalid", error=reason)
                continue

            parent_candidates = self._resolve_candidates(proposal.parent_ids)
            parents = {candidate.id: candidate.payload for candidate in parent_candidates}
            try:
                payload = realize_proposal(proposal, parents)
            except (TypeError, ValueError) as exc:
                self.ideas.mark_invalid(proposal.id, str(exc))
                self._record_proposal(proposal, generation, status="invalid", error=str(exc))
                continue

            child = Candidate(
                payload=payload,
                parent_ids=list(proposal.parent_ids),
                mutation_level=f"semantic:{proposal.kind}",
                generation=generation,
            )
            island_index = self._island_for_parent(proposal.parent_ids[0])
            self._evaluate(child, evaluator, island_index, proposal=proposal)

    def run(
        self,
        seed_payloads: Iterable[dict[str, Any]],
        evaluator_paths: str | Path | Iterable[str | Path],
        *,
        resume: bool = False,
        domain_pack: str | None = None,
    ) -> RunSummary:
        if self.spec.explorer.enabled and self.explorer is None:
            raise ValueError("ResearchSpec enables explorer proposals, but no Explorer was supplied")
        if isinstance(evaluator_paths, (str, Path)):
            paths = [Path(evaluator_paths)]
        else:
            paths = [Path(path) for path in evaluator_paths]
        evaluator = EvaluatorCascade(paths, self.spec.budget.evaluator_timeout_seconds)
        seeds = [dict(payload) for payload in seed_payloads]
        if not seeds:
            raise ValueError("at least one seed payload is required")

        mutator_name = f"{self.mutator.__class__.__module__}:{self.mutator.__class__.__qualname__}"
        explorer_name = self.explorer.name if self.explorer is not None else None
        manifest = build_manifest(
            self.spec,
            seeds,
            paths,
            mutator_name=mutator_name,
            domain_pack=domain_pack,
            explorer_name=explorer_name,
        )
        manifest_fingerprint = str(manifest["fingerprint"])
        manifest_path = self.workspace / "manifest.json"

        if resume:
            if not self.checkpoints.exists():
                raise ValueError("--resume requested but checkpoint.json does not exist")
            checkpoint = self.checkpoints.load()
            if checkpoint.manifest_fingerprint != manifest_fingerprint:
                raise ValueError("checkpoint inputs differ from the current spec/seeds/evaluators/mutator/explorer")
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

            self._run_explorer(generation, evaluator)

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
        self.ideas.close()

    def __enter__(self) -> "ResearchEngine":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
