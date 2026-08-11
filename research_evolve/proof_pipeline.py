from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .candidates import Candidate, CandidateDB
from .conjectures import Conjecture, ConjectureMemory, Counterexample
from .graph import ResearchGraph, ResearchNode
from .proof_agents import ProofContext, ProofPlanner, Prover, ProofVerifier
from .proofs import ProofArtifact, ProofMemory, ProofPlan, ProofReview, ProofSpec
from .reproducibility import stable_json_hash


@dataclass(slots=True)
class ProofRunSummary:
    considered_conjectures: int
    attempted_proofs: int
    verified_natural_language: int
    rejected: int
    inconclusive: int
    invalid: int
    refuted_before_proof: int
    proof_manifest_fingerprint: str
    workspace: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProofPipeline:
    """v0.5 natural-language proof planning/proving/verification pipeline.

    This stage deliberately runs after the evolutionary research run. It consumes
    empirically supported conjectures and previously evaluated candidates, but it
    does not alter the evolutionary checkpoint or source manifest.
    """

    def __init__(
        self,
        workspace: str | Path,
        planner: ProofPlanner,
        prover: Prover,
        verifier: ProofVerifier,
        *,
        max_conjectures: int = 4,
        max_lemmas: int = 24,
        evidence_context: int = 24,
        min_verifier_confidence: float = 0.7,
    ) -> None:
        if max_conjectures < 1 or max_lemmas < 1 or evidence_context < 1:
            raise ValueError("proof pipeline size limits must be positive")
        if not 0.0 <= min_verifier_confidence <= 1.0:
            raise ValueError("min_verifier_confidence must be in [0, 1]")
        if prover.name == verifier.name:
            raise ValueError("proof verifier must be independent from the prover")

        self.workspace = Path(workspace)
        self.planner = planner
        self.prover = prover
        self.verifier = verifier
        self.max_conjectures = max_conjectures
        self.max_lemmas = max_lemmas
        self.evidence_context = evidence_context
        self.min_verifier_confidence = min_verifier_confidence

        source_manifest_path = self.workspace / "manifest.json"
        if not source_manifest_path.is_file():
            raise ValueError(f"research workspace has no manifest.json: {self.workspace}")
        self.source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        spec = self.source_manifest.get("inputs", {}).get("spec", {})
        self.problem = str(spec.get("problem", ""))
        self.constraints = [dict(item) for item in spec.get("constraints", []) if isinstance(item, dict)]
        self.domain = str(spec.get("domain", "generic"))

        summary_path = self.workspace / "summary.json"
        self.source_generation = 0
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.source_generation = int(summary.get("generation_completed", 0))

        self.db = CandidateDB(self.workspace / "candidates.sqlite3")
        self.conjectures = ConjectureMemory(self.workspace / "conjectures.sqlite3")
        self.graph = ResearchGraph(self.workspace / "research_graph.sqlite3")
        self.memory = ProofMemory(self.workspace / "proofs.sqlite3")

        self.proof_manifest = self._build_proof_manifest()
        self.proof_manifest_fingerprint = str(self.proof_manifest["fingerprint"])
        self._ensure_proof_manifest()

    def _build_proof_manifest(self) -> dict[str, Any]:
        stable = {
            "source_run_fingerprint": self.source_manifest.get("fingerprint"),
            "planner": self.planner.name,
            "prover": self.prover.name,
            "verifier": self.verifier.name,
            "max_conjectures": self.max_conjectures,
            "max_lemmas": self.max_lemmas,
            "evidence_context": self.evidence_context,
            "min_verifier_confidence": self.min_verifier_confidence,
        }
        return {
            "schema_version": 1,
            "fingerprint": stable_json_hash(stable),
            "inputs": stable,
            "truth_policy": (
                "verified_natural_language means an independent verifier accepted a natural-language proof artifact. "
                "It is not a formal proof and must never be reported as Lean/Coq/Isabelle verification."
            ),
        }

    def _ensure_proof_manifest(self) -> None:
        path = self.workspace / "proof_manifest.json"
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("fingerprint") != self.proof_manifest_fingerprint:
                raise ValueError(
                    "proof_manifest.json was created with different planner/prover/verifier or proof policy; "
                    "use a fresh research workspace or keep the same proof configuration"
                )
            return
        path.write_text(json.dumps(self.proof_manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _candidate_snapshot(candidate: Candidate) -> dict[str, Any]:
        return {
            "id": candidate.id,
            "generation": candidate.generation,
            "payload": candidate.payload,
            "score": candidate.score,
            "metrics": candidate.metrics,
            "behavior": candidate.behavior,
            "mutation_level": candidate.mutation_level,
        }

    def _valid_candidates(self) -> list[Candidate]:
        return [candidate for candidate in self.db.all() if candidate.valid]

    def _record_counterexample(self, conjecture: Conjecture, candidate: Candidate) -> None:
        counterexample = Counterexample(
            conjecture_id=conjecture.id,
            candidate_id=candidate.id,
            generation=self.source_generation,
            source="proof_preflight",
            payload=dict(candidate.payload),
            metrics=dict(candidate.metrics),
            score=candidate.score,
        )
        self.conjectures.record_test(
            conjecture.id,
            candidate.id,
            self.source_generation,
            False,
            "proof_preflight",
        )
        self.conjectures.record_counterexample(counterexample)
        self.conjectures.refresh_status(conjecture.id, min_evidence=1)
        self.graph.add_node(
            ResearchNode(
                id=counterexample.id,
                type="counterexample",
                statement=f"Proof preflight found candidate {candidate.id[:8]} refuting {conjecture.id[:24]}",
                status="verified",
                payload=counterexample.to_dict(),
                created_at=counterexample.created_at,
            )
        )
        self.graph.add_edge(candidate.id, "refutes", conjecture.id, {"source": "proof_preflight"})
        self.graph.add_edge(counterexample.id, "counterexample_to", conjecture.id)
        record = next(
            (item for item in self.conjectures.recent_conjectures(100000) if item["id"] == conjecture.id),
            None,
        )
        if record is not None:
            self.graph.add_node(
                ResearchNode(
                    id=conjecture.id,
                    type="conjecture",
                    statement=conjecture.statement,
                    status="refuted",
                    payload=record,
                    created_at=conjecture.created_at,
                )
            )

    def _empirical_preflight(self, conjecture: Conjecture, candidates: list[Candidate]) -> Candidate | None:
        """Re-attack a conjecture using every already evaluated valid candidate."""

        for candidate in candidates:
            result = conjecture.predicate.evaluate(candidate)
            if result is False:
                self._record_counterexample(conjecture, candidate)
                return candidate
            if result is True:
                self.conjectures.record_test(
                    conjecture.id,
                    candidate.id,
                    self.source_generation,
                    True,
                    "proof_preflight",
                )
        self.conjectures.refresh_status(conjecture.id, min_evidence=1)
        return None

    def _build_context(
        self,
        proof_spec: ProofSpec,
        conjecture_record: dict[str, Any],
        candidates: list[Candidate],
    ) -> ProofContext:
        evidence_ids = list(dict.fromkeys(conjecture_record.get("evidence_candidate_ids", [])))
        evidence = [candidate for candidate in candidates if candidate.id in evidence_ids]
        if len(evidence) < self.evidence_context:
            known = {candidate.id for candidate in evidence}
            for candidate in sorted(
                candidates,
                key=lambda item: item.score if item.score is not None else float("-inf"),
                reverse=True,
            ):
                if candidate.id in known:
                    continue
                evidence.append(candidate)
                known.add(candidate.id)
                if len(evidence) >= self.evidence_context:
                    break
        observation_ids = set(conjecture_record.get("observation_ids", []))
        observations = [
            item
            for item in self.conjectures.recent_observations(1000)
            if item["id"] in observation_ids
        ]
        return ProofContext(
            problem=self.problem,
            generation=self.source_generation,
            proof_spec=proof_spec.to_dict(),
            conjecture=conjecture_record,
            evidence_candidates=[self._candidate_snapshot(candidate) for candidate in evidence[: self.evidence_context]],
            observations=observations,
            prior_proofs=self.memory.list_specs(12),
            metadata={
                "domain": self.domain,
                "constraints": self.constraints,
                "truth_policy": (
                    "Empirical evidence is context, not proof. The prover must establish the exact ProofSpec; "
                    "the verifier must independently attack it."
                ),
            },
        )

    def _record_spec_graph(self, proof_spec: ProofSpec) -> None:
        self.graph.add_node(
            ResearchNode(
                id=proof_spec.id,
                type="proof_spec",
                statement=proof_spec.statement,
                status="planned",
                payload=proof_spec.to_dict(),
                created_at=proof_spec.created_at,
            )
        )
        self.graph.add_edge(proof_spec.id, "targets_conjecture", proof_spec.conjecture_id)

    def _record_plan_graph(self, plan: ProofPlan, generation: int) -> None:
        payload = plan.to_dict()
        payload["generation"] = generation
        self.graph.add_node(
            ResearchNode(
                id=plan.id,
                type="proof_plan",
                statement=plan.strategy,
                status="planned",
                payload=payload,
                created_at=plan.created_at,
            )
        )
        self.graph.add_edge(plan.id, "plans_for", plan.proof_spec_id)
        label_to_id = {lemma.label: lemma.id for lemma in plan.lemmas}
        for lemma in plan.lemmas:
            self.graph.add_node(
                ResearchNode(
                    id=lemma.id,
                    type="lemma",
                    statement=lemma.statement,
                    status="planned",
                    payload={**lemma.to_dict(), "generation": generation, "proof_plan_id": plan.id},
                )
            )
            self.graph.add_edge(plan.id, "decomposes_into", lemma.id, {"label": lemma.label})
            for dependency in lemma.depends_on:
                self.graph.add_edge(lemma.id, "depends_on", label_to_id[dependency])

    def _record_artifact_graph(self, artifact: ProofArtifact, generation: int, status: str = "drafted") -> None:
        payload = artifact.to_dict()
        payload["generation"] = generation
        self.graph.add_node(
            ResearchNode(
                id=artifact.id,
                type="proof_artifact",
                statement=artifact.final_argument[:240],
                status=status,
                payload=payload,
                created_at=artifact.created_at,
            )
        )
        self.graph.add_edge(artifact.id, "implements_plan", artifact.proof_plan_id)
        self.graph.add_edge(artifact.id, "claims_proof_of", artifact.proof_spec_id)

    def _record_review_graph(self, review: ProofReview, generation: int, status: str, conjecture_id: str) -> None:
        payload = review.to_dict()
        payload["generation"] = generation
        payload["gated_status"] = status
        self.graph.add_node(
            ResearchNode(
                id=review.id,
                type="proof_review",
                statement=f"{review.decision} at confidence={review.confidence:.3f}",
                status=status,
                payload=payload,
                created_at=review.created_at,
            )
        )
        self.graph.add_edge(review.id, "reviews", review.proof_artifact_id)
        if status == "verified_natural_language":
            self.graph.add_edge(review.id, "supports_natural_language_proof_of", conjecture_id)
        elif status == "rejected":
            self.graph.add_edge(review.id, "rejects_proof_for", conjecture_id)

    def _record_actor_error(self, actor_type: str, actor: Any, proof_spec: ProofSpec, message: str) -> None:
        node = ResearchNode(
            type="proof_actor_error",
            statement=message[:240],
            status="failed",
            payload={
                "generation": self.source_generation,
                "actor_type": actor_type,
                "actor": getattr(actor, "name", None),
                "proof_spec_id": proof_spec.id,
                "error": message,
            },
        )
        self.graph.add_node(node)
        self.graph.add_edge(proof_spec.id, "proof_actor_failed", node.id)

    def _attempt(
        self,
        conjecture: Conjecture,
        conjecture_record: dict[str, Any],
        candidates: list[Candidate],
    ) -> str:
        assumptions = [
            str(item.get("description") or item.get("name"))
            for item in self.constraints
            if item.get("hard", True)
        ]
        proof_spec = ProofSpec.from_conjecture(
            conjecture,
            generation=self.source_generation,
            assumptions=assumptions,
            metadata={
                "source_conjecture_status": conjecture_record.get("status"),
                "source_conjecture_tests": conjecture_record.get("tests", 0),
            },
        )
        self.memory.record_spec(proof_spec)
        self._record_spec_graph(proof_spec)
        context = self._build_context(proof_spec, conjecture_record, candidates)

        try:
            plan = self.planner.plan(context, proof_spec)
            plan.proof_spec_id = proof_spec.id
            plan.validate(max_lemmas=self.max_lemmas)
        except Exception as exc:
            self.memory.mark_spec_invalid(proof_spec.id)
            self._record_actor_error("planner", self.planner, proof_spec, str(exc))
            return "invalid"
        self.memory.record_plan(plan, self.source_generation)
        self._record_plan_graph(plan, self.source_generation)

        try:
            artifact = self.prover.prove(context, proof_spec, plan)
            artifact.proof_spec_id = proof_spec.id
            artifact.proof_plan_id = plan.id
            artifact.validate(plan)
            hidden_assumptions = sorted(set(artifact.assumptions_used) - set(proof_spec.assumptions))
            if hidden_assumptions:
                raise ValueError(f"proof artifact introduced assumptions outside ProofSpec: {hidden_assumptions}")
        except Exception as exc:
            self.memory.mark_spec_invalid(proof_spec.id)
            self._record_actor_error("prover", self.prover, proof_spec, str(exc))
            return "invalid"
        self.memory.record_artifact(artifact, self.source_generation)
        self._record_artifact_graph(artifact, self.source_generation)

        try:
            review = self.verifier.verify(context, proof_spec, plan, artifact)
            review.proof_artifact_id = artifact.id
            review.verifier = self.verifier.name
            review.validate()
        except Exception as exc:
            self._record_actor_error("verifier", self.verifier, proof_spec, str(exc))
            self._record_artifact_graph(artifact, self.source_generation, status="inconclusive")
            return "inconclusive"

        status = self.memory.record_review(
            review,
            self.source_generation,
            min_confidence=self.min_verifier_confidence,
        )
        self._record_artifact_graph(artifact, self.source_generation, status=status)
        self._record_review_graph(review, self.source_generation, status, conjecture.id)
        return status

    def run(self) -> ProofRunSummary:
        candidates = self._valid_candidates()
        records = [
            record
            for record in self.conjectures.recent_conjectures(100000)
            if record["status"] == "empirically_supported"
        ]
        records = records[: self.max_conjectures]

        considered = 0
        attempted = 0
        refuted_before = 0
        statuses: list[str] = []
        for record in records:
            conjecture_id = str(record["id"])
            if self.memory.has_verified_for_conjecture(conjecture_id):
                continue
            considered += 1
            conjecture = Conjecture.from_dict(record)
            conjecture.status = "empirically_supported"
            if self._empirical_preflight(conjecture, candidates) is not None:
                refuted_before += 1
                continue

            refreshed = next(
                (item for item in self.conjectures.recent_conjectures(100000) if item["id"] == conjecture_id),
                record,
            )
            if refreshed["status"] != "empirically_supported":
                continue
            attempted += 1
            statuses.append(self._attempt(conjecture, refreshed, candidates))

        summary = ProofRunSummary(
            considered_conjectures=considered,
            attempted_proofs=attempted,
            verified_natural_language=sum(status == "verified_natural_language" for status in statuses),
            rejected=sum(status == "rejected" for status in statuses),
            inconclusive=sum(status == "inconclusive" for status in statuses),
            invalid=sum(status == "invalid" for status in statuses),
            refuted_before_proof=refuted_before,
            proof_manifest_fingerprint=self.proof_manifest_fingerprint,
            workspace=str(self.workspace),
        )
        (self.workspace / "proof_summary.json").write_text(
            json.dumps(summary.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return summary

    def close(self) -> None:
        self.db.close()
        self.conjectures.close()
        self.graph.close()
        self.memory.close()

    def __enter__(self) -> "ProofPipeline":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
