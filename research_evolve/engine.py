from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .candidates import Candidate, CandidateDB
from .checkpoint import CheckpointStore, SearchCheckpoint
from .conjecturer import ConjectureContext, Conjecturer
from .conjectures import Conjecture, ConjectureMemory, Counterexample, Observation, ObservationExtractor
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
    observation_count: int
    conjecture_count: int
    empirically_supported_conjectures: int
    refuted_conjectures: int
    counterexample_count: int
    manifest_fingerprint: str
    workspace: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResearchEngine:
    """v0.4: evolution + semantic exploration + empirical conjecture/counterexample loop."""

    def __init__(
        self,
        spec: ResearchSpec,
        workspace: str | Path = ".researchevolve/run",
        island_count: int = 4,
        mutator: FourLevelMutator | None = None,
        explorer: Explorer | None = None,
        conjecturer: Conjecturer | None = None,
    ) -> None:
        spec.validate()
        self.spec = spec
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.rng = random.Random(spec.budget.seed)
        self.db = CandidateDB(self.workspace / "candidates.sqlite3")
        self.graph = ResearchGraph(self.workspace / "research_graph.sqlite3")
        self.ideas = IdeaMemory(self.workspace / "ideas.sqlite3")
        self.conjectures = ConjectureMemory(self.workspace / "conjectures.sqlite3")
        self.observation_extractor = ObservationExtractor()
        self.population = IslandPopulation(island_count, spec.behavior_dimensions)
        self.pareto = ParetoArchive(spec.objectives)
        self.novelty = NoveltyArchive(spec.behavior_dimensions, k=spec.search.novelty_k)
        self.mutator = mutator or FourLevelMutator()
        self.explorer = explorer
        self.conjecturer = conjecturer
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

    def _record_observation(self, observation: Observation) -> None:
        self.graph.add_node(
            ResearchNode(
                id=observation.id,
                type="observation",
                statement=observation.statement,
                status="observed",
                payload=observation.to_dict(),
                created_at=observation.created_at,
            )
        )
        self.graph.add_edge(self.problem_node_id, "has_observation", observation.id)
        for candidate_id in observation.evidence_candidate_ids:
            self.graph.add_edge(observation.id, "derived_from", candidate_id)

    def _record_conjecture(self, conjecture: Conjecture, generation: int, status: str | None = None, **extra: Any) -> None:
        payload = conjecture.to_dict()
        payload["generation"] = generation
        payload.update(extra)
        node_status = status or conjecture.status
        self.graph.add_node(
            ResearchNode(
                id=conjecture.id,
                type="conjecture",
                statement=conjecture.statement,
                status=node_status,
                payload=payload,
                created_at=conjecture.created_at,
            )
        )
        self.graph.add_edge(self.problem_node_id, "has_conjecture", conjecture.id)
        for observation_id in conjecture.observation_ids:
            self.graph.add_edge(observation_id, "suggests", conjecture.id)
        for parent_id in conjecture.parent_conjecture_ids:
            self.graph.add_edge(parent_id, "refined_into", conjecture.id)
        for candidate_id in conjecture.evidence_candidate_ids:
            self.graph.add_edge(candidate_id, "evidence_for", conjecture.id)

    def _record_counterexample(self, counterexample: Counterexample) -> None:
        self.graph.add_node(
            ResearchNode(
                id=counterexample.id,
                type="counterexample",
                statement=f"Candidate {counterexample.candidate_id[:8]} refutes {counterexample.conjecture_id[:24]}",
                status="verified",
                payload=counterexample.to_dict(),
                created_at=counterexample.created_at,
            )
        )
        self.graph.add_edge(counterexample.candidate_id, "refutes", counterexample.conjecture_id)
        self.graph.add_edge(counterexample.candidate_id, "instantiated_as", counterexample.id)
        self.graph.add_edge(counterexample.id, "counterexample_to", counterexample.conjecture_id)

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
        self.ideas.prune_after_generation(checkpoint.generation)
        self.conjectures.prune_after_generation(
            checkpoint.generation,
            min_evidence=self.spec.conjecture.min_evidence,
        )
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

    def _archive_candidate_pool(self, limit: int) -> list[Candidate]:
        pool: list[Candidate] = []
        pool.extend(self.population.global_elites()[:limit])
        pool.extend(self.pareto.candidates()[:limit])
        novelty_ranked = sorted(self.novelty.members.values(), key=self._novelty_value, reverse=True)
        pool.extend(novelty_ranked[:limit])
        unique: list[Candidate] = []
        seen: set[str] = set()
        for candidate in pool:
            if candidate.id in seen or not candidate.valid:
                continue
            seen.add(candidate.id)
            unique.append(candidate)
            if len(unique) >= limit:
                break
        return unique

    def _build_research_context(self, generation: int) -> ResearchContext:
        limit = self.spec.explorer.context_candidates
        unique = self._archive_candidate_pool(limit)
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

    def _record_external_error(self, node_type: str, relation: str, generation: int, actor: Any, message: str) -> None:
        node = ResearchNode(
            type=node_type,
            statement=message[:240],
            status="failed",
            payload={"generation": generation, "actor": getattr(actor, "name", None), "error": message},
        )
        self.graph.add_node(node)
        self.graph.add_edge(self.problem_node_id, relation, node.id)

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
        except Exception as exc:
            self._record_external_error("explorer_error", "explorer_failed", generation, self.explorer, str(exc))
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

    def _build_conjecture_context(self, generation: int, observations: list[dict[str, Any]]) -> ConjectureContext:
        candidates = self._archive_candidate_pool(self.spec.conjecture.context_candidates)
        return ConjectureContext(
            problem=self.spec.problem,
            generation=generation,
            objectives=[asdict(item) for item in self.spec.objectives],
            constraints=[asdict(item) for item in self.spec.constraints],
            observations=observations,
            candidates=[self._candidate_snapshot(candidate) for candidate in candidates],
            conjectures=self.conjectures.recent_conjectures(self.spec.conjecture.context_conjectures),
            metadata={
                "domain": self.spec.domain,
                "truth_policy": (
                    "Finite experiments can only support or refute a conjecture. Never label a conjecture proved."
                ),
            },
        )

    def _test_candidate_against_conjecture(
        self,
        conjecture: Conjecture,
        candidate: Candidate,
        generation: int,
        source: str,
    ) -> Counterexample | None:
        if not candidate.valid:
            return None
        supported = conjecture.predicate.evaluate(candidate)
        if supported is None:
            return None
        self.conjectures.record_test(conjecture.id, candidate.id, generation, supported, source)
        if supported:
            return None
        counterexample = Counterexample(
            conjecture_id=conjecture.id,
            candidate_id=candidate.id,
            generation=generation,
            source=source,
            payload=dict(candidate.payload),
            metrics=dict(candidate.metrics),
            score=candidate.score,
        )
        self.conjectures.record_counterexample(counterexample)
        self._record_counterexample(counterexample)
        return counterexample

    def _counterexample_search(
        self,
        conjecture: Conjecture,
        generation: int,
        evaluator: EvaluatorCascade,
        context_candidates: list[Candidate],
    ) -> str:
        for candidate in context_candidates:
            if self._test_candidate_against_conjecture(conjecture, candidate, generation, "archive") is not None:
                return self.conjectures.refresh_status(conjecture.id, self.spec.conjecture.min_evidence)

        for _ in range(self.spec.conjecture.counterexample_trials):
            selected = self.population.sample_parent(
                self.rng,
                novelty_probability=self.spec.search.novelty_probability,
            )
            if selected is None:
                break
            island_index, parent = selected
            level = self.mutator.sample_level(self.rng)
            payload = self.mutator.mutate(parent.payload, level, self.rng)
            child = Candidate(
                payload=payload,
                parent_ids=[parent.id],
                mutation_level=f"counterexample:{level.value}",
                generation=generation,
            )
            self._evaluate(child, evaluator, island_index)
            if self._test_candidate_against_conjecture(conjecture, child, generation, "counterexample_search") is not None:
                return self.conjectures.refresh_status(conjecture.id, self.spec.conjecture.min_evidence)

        return self.conjectures.refresh_status(conjecture.id, self.spec.conjecture.min_evidence)

    def _run_conjecture_loop(self, generation: int, evaluator: EvaluatorCascade) -> None:
        if not self.spec.conjecture.enabled or self.conjecturer is None:
            return
        if generation % self.spec.conjecture.interval != 0:
            return

        candidate_pool = self._archive_candidate_pool(self.spec.conjecture.context_candidates)
        observations = self.observation_extractor.extract(
            candidate_pool,
            generation,
            limit=self.spec.conjecture.observations_per_interval,
        )
        for observation in observations:
            self.conjectures.record_observation(observation)
            self._record_observation(observation)

        observation_context = self.conjectures.recent_observations(self.spec.conjecture.observations_per_interval)
        context = self._build_conjecture_context(generation, observation_context)
        allowed_observations = {str(item["id"]) for item in context.observations}
        allowed_candidates = {str(item["id"]) for item in context.candidates}
        allowed_conjectures = {str(item["id"]) for item in context.conjectures}

        try:
            proposed = self.conjecturer.propose(context, self.spec.conjecture.conjectures_per_interval)
        except Exception as exc:
            self._record_external_error(
                "conjecturer_error",
                "conjecturer_failed",
                generation,
                self.conjecturer,
                str(exc),
            )
            return

        for conjecture in proposed[: self.spec.conjecture.conjectures_per_interval]:
            self.conjectures.record_conjecture(conjecture, generation)
            self._record_conjecture(conjecture, generation)

            bad_observations = [item for item in conjecture.observation_ids if item not in allowed_observations]
            bad_evidence = [item for item in conjecture.evidence_candidate_ids if item not in allowed_candidates]
            bad_parents = [item for item in conjecture.parent_conjecture_ids if item not in allowed_conjectures]
            if bad_observations or bad_evidence or bad_parents:
                reason = (
                    f"conjecture references context-external ids: observations={bad_observations}, "
                    f"evidence={bad_evidence}, parents={bad_parents}"
                )
                self.conjectures.mark_invalid(conjecture.id, reason)
                self._record_conjecture(conjecture, generation, status="invalid", error=reason)
                continue

            status = self._counterexample_search(conjecture, generation, evaluator, candidate_pool)
            conjecture.status = status  # type: ignore[assignment]
            self._record_conjecture(conjecture, generation, status=status)

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
        if self.spec.conjecture.enabled and self.conjecturer is None:
            raise ValueError("ResearchSpec enables conjecture generation, but no Conjecturer was supplied")
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
        conjecturer_name = self.conjecturer.name if self.conjecturer is not None else None
        manifest = build_manifest(
            self.spec,
            seeds,
            paths,
            mutator_name=mutator_name,
            domain_pack=domain_pack,
            explorer_name=explorer_name,
            conjecturer_name=conjecturer_name,
        )
        manifest_fingerprint = str(manifest["fingerprint"])
        manifest_path = self.workspace / "manifest.json"

        if resume:
            if not self.checkpoints.exists():
                raise ValueError("--resume requested but checkpoint.json does not exist")
            checkpoint = self.checkpoints.load()
            if checkpoint.manifest_fingerprint != manifest_fingerprint:
                raise ValueError(
                    "checkpoint inputs differ from the current spec/seeds/evaluators/mutator/explorer/conjecturer"
                )
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
            self._run_conjecture_loop(generation, evaluator)

            if generation % self.spec.search.migration_interval == 0:
                self.population.migrate(migrants_per_island=self.spec.search.migrants_per_island)
            generation_completed = generation
            if generation % self.spec.search.checkpoint_interval == 0:
                self._save_checkpoint(generation, manifest_fingerprint)

        self._save_checkpoint(generation_completed, manifest_fingerprint)
        elites = self.population.global_elites()
        best = elites[0] if elites else None
        conjecture_stats = self.conjectures.stats()
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
            observation_count=conjecture_stats["observations"],
            conjecture_count=conjecture_stats["conjectures"],
            empirically_supported_conjectures=conjecture_stats["empirically_supported"],
            refuted_conjectures=conjecture_stats["refuted"],
            counterexample_count=conjecture_stats["counterexamples"],
            manifest_fingerprint=manifest_fingerprint,
            workspace=str(self.workspace),
        )
        (self.workspace / "summary.json").write_text(json.dumps(summary.to_dict(), indent=2), encoding="utf-8")
        return summary

    def close(self) -> None:
        self.db.close()
        self.graph.close()
        self.ideas.close()
        self.conjectures.close()

    def __enter__(self) -> "ResearchEngine":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
