from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .candidates import Candidate, CandidateDB
from .conjectures import ConjectureMemory
from .formal import FormalArtifact, FormalMemory, FormalStatus, FormalizationSpec, KernelResult
from .formal_agents import FormalContext, Formalizer, FormalRepairer
from .graph import ResearchGraph, ResearchNode
from .lean_kernel import LeanKernel
from .proofs import ProofMemory
from .reproducibility import stable_json_hash


@dataclass(slots=True)
class FormalRunSummary:
    considered_proofs: int
    attempted_formalizations: int
    already_formal_verified: int
    formal_verified: int
    kernel_rejected: int
    repair_exhausted: int
    environment_error: int
    invalid: int
    invalidated_stale: int
    missing_contract: int
    formal_manifest_fingerprint: str
    workspace: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FormalPipeline:
    """v0.6 Lean formalization + kernel verification stage.

    This pipeline consumes current `verified_natural_language` proof lineages.
    The source ResearchSpec freezes each Lean target through
    `metadata.formal_contracts`. Formalizer/Repairer actors may only supply proof
    terms and helper declarations; Lean itself is the final gate.
    """

    def __init__(
        self,
        workspace: str | Path,
        formalizer: Formalizer,
        kernel: LeanKernel,
        repairer: FormalRepairer | None = None,
        *,
        max_targets: int = 4,
        max_repairs: int = 2,
        evidence_context: int = 24,
    ) -> None:
        if max_targets < 1 or max_repairs < 0 or evidence_context < 1:
            raise ValueError("formal pipeline size/repair limits are invalid")
        self.workspace = Path(workspace)
        self.formalizer = formalizer
        self.repairer = repairer
        self.kernel = kernel
        self.max_targets = int(max_targets)
        self.max_repairs = int(max_repairs)
        self.evidence_context = int(evidence_context)

        source_manifest_path = self.workspace / "manifest.json"
        proof_manifest_path = self.workspace / "proof_manifest.json"
        proof_db_path = self.workspace / "proofs.sqlite3"
        candidate_path = self.workspace / "candidates.sqlite3"
        conjecture_path = self.workspace / "conjectures.sqlite3"
        graph_path = self.workspace / "research_graph.sqlite3"
        summary_path = self.workspace / "summary.json"
        for path in [
            source_manifest_path,
            proof_manifest_path,
            proof_db_path,
            candidate_path,
            conjecture_path,
            graph_path,
            summary_path,
        ]:
            if not path.is_file():
                raise ValueError(f"formal pipeline requires existing artifact: {path}")

        self.source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        self.proof_manifest = json.loads(proof_manifest_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.source_generation = int(summary.get("generation_completed", 0))
        proof_inputs = self.proof_manifest.get("inputs", {})
        if proof_inputs.get("source_run_fingerprint") != self.source_manifest.get("fingerprint"):
            raise ValueError("proof_manifest source fingerprint does not match research manifest")
        if int(proof_inputs.get("source_generation", -1)) != self.source_generation:
            raise ValueError("proof_manifest source generation does not match current research summary")

        source_spec = self.source_manifest.get("inputs", {}).get("spec", {})
        if not isinstance(source_spec, dict):
            raise ValueError("research manifest does not contain a valid source ResearchSpec")
        self.problem = str(source_spec.get("problem", ""))
        self.domain = str(source_spec.get("domain", "generic"))
        metadata = source_spec.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("ResearchSpec.metadata must be an object")
        raw_contracts = metadata.get("formal_contracts", [])
        if not isinstance(raw_contracts, list):
            raise ValueError("ResearchSpec.metadata.formal_contracts must be a list")
        self.contracts = self._validate_contracts(raw_contracts)

        formal_db_path = self.workspace / "formal.sqlite3"
        formal_manifest_path = self.workspace / "formal_manifest.json"
        if formal_db_path.is_file() and not formal_manifest_path.is_file():
            raise ValueError("formal.sqlite3 exists without formal_manifest.json; refusing unaudited formal journal")

        self.db = CandidateDB(candidate_path)
        self.conjectures = ConjectureMemory(conjecture_path)
        self.proofs = ProofMemory(proof_db_path)
        self.graph = ResearchGraph(graph_path)
        self.memory = FormalMemory(formal_db_path)
        self.sources_dir = self.workspace / "formal_sources"
        self.sources_dir.mkdir(parents=True, exist_ok=True)

        self.formal_manifest = self._build_manifest()
        self.formal_manifest_fingerprint = str(self.formal_manifest["fingerprint"])
        self._ensure_manifest(formal_manifest_path)

    @staticmethod
    def _validate_contracts(raw_contracts: list[Any]) -> dict[str, dict[str, Any]]:
        contracts: dict[str, dict[str, Any]] = {}
        for index, raw in enumerate(raw_contracts):
            if not isinstance(raw, dict):
                raise ValueError(f"formal contract #{index} must be an object")
            statement = str(raw.get("conjecture_statement", "")).strip()
            if not statement:
                raise ValueError(f"formal contract #{index} requires conjecture_statement")
            if statement in contracts:
                raise ValueError(f"duplicate formal contract for conjecture statement: {statement!r}")
            contract = {
                "conjecture_statement": statement,
                "backend": str(raw.get("backend", "lean4")),
                "toolchain": str(raw.get("toolchain", "leanprover/lean4:v4.30.0")),
                "theorem_name": str(raw.get("theorem_name", "")).strip(),
                "theorem_signature": str(raw.get("theorem_signature", "")).strip(),
                "imports": [str(item) for item in raw.get("imports", [])],
                "metadata": dict(raw.get("metadata", {})),
            }
            contracts[statement] = contract
        return contracts

    def _build_manifest(self) -> dict[str, Any]:
        stable = {
            "source_run_fingerprint": self.source_manifest.get("fingerprint"),
            "source_generation": self.source_generation,
            "proof_manifest_fingerprint": self.proof_manifest.get("fingerprint"),
            "formalizer": self.formalizer.name,
            "repairer": self.repairer.name if self.repairer is not None else None,
            "kernel": self.kernel.name,
            "max_targets": self.max_targets,
            "max_repairs": self.max_repairs,
            "evidence_context": self.evidence_context,
            "formal_contracts_hash": stable_json_hash(self.contracts),
        }
        return {
            "schema_version": 1,
            "fingerprint": stable_json_hash(stable),
            "inputs": stable,
            "truth_policy": (
                "formal_verified is granted only by the configured Lean kernel gate on the frozen theorem signature. "
                "Natural-language models cannot directly assign formal_verified."
            ),
        }

    def _ensure_manifest(self, path: Path) -> None:
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("fingerprint") != self.formal_manifest_fingerprint:
                raise ValueError(
                    "formal_manifest.json was created with different source proof state, actors, contracts, or kernel policy; "
                    "use a fresh workspace or keep the same formal configuration"
                )
            return
        path.write_text(json.dumps(self.formal_manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _candidate_snapshot(candidate: Candidate) -> dict[str, Any]:
        return {
            "id": candidate.id,
            "generation": candidate.generation,
            "payload": candidate.payload,
            "score": candidate.score,
            "metrics": candidate.metrics,
            "behavior": candidate.behavior,
        }

    def _valid_candidates(self) -> list[Candidate]:
        return [candidate for candidate in self.db.all() if candidate.valid]

    @staticmethod
    def _by_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {str(item["id"]): item for item in records if item.get("id") is not None}

    def _proof_state(self) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        specs = self.proofs.list_specs(100000)
        artifacts = self.proofs.list_artifacts(100000)
        reviews = self.proofs.list_reviews(100000)
        return specs, self._by_id(artifacts), self._by_id(reviews)

    def _latest_verified_artifact(self, proof_spec_id: str, artifacts: list[dict[str, Any]]) -> dict[str, Any] | None:
        for artifact in artifacts:
            if artifact.get("proof_spec_id") == proof_spec_id and artifact.get("status") == "verified_natural_language":
                return artifact
        return None

    @staticmethod
    def _review_for_artifact(artifact_id: str, reviews: list[dict[str, Any]]) -> dict[str, Any] | None:
        for review in reviews:
            if review.get("proof_artifact_id") == artifact_id and review.get("gated_status") == "verified_natural_language":
                return review
        return None

    def _conjecture_record(self, conjecture_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.conjectures.recent_conjectures(100000) if item.get("id") == conjecture_id),
            None,
        )

    def _build_context(
        self,
        formal_spec: FormalizationSpec,
        proof_spec: dict[str, Any],
        proof_artifact: dict[str, Any],
        proof_review: dict[str, Any],
        conjecture: dict[str, Any],
        candidates: list[Candidate],
    ) -> FormalContext:
        evidence_ids = list(dict.fromkeys(conjecture.get("evidence_candidate_ids", [])))
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
        observation_ids = set(conjecture.get("observation_ids", []))
        observations = [
            item
            for item in self.conjectures.recent_observations(1000)
            if item.get("id") in observation_ids
        ]
        return FormalContext(
            problem=self.problem,
            generation=self.source_generation,
            formal_spec=formal_spec.to_dict(),
            proof_spec=proof_spec,
            proof_artifact=proof_artifact,
            proof_review=proof_review,
            conjecture=conjecture,
            observations=observations,
            evidence_candidates=[self._candidate_snapshot(candidate) for candidate in evidence[: self.evidence_context]],
            previous_kernel_runs=[],
            metadata={
                "domain": self.domain,
                "truth_policy": (
                    "The Lean theorem signature is frozen. Empirical candidates and natural-language proofs are context only; "
                    "formal_verified requires the Lean kernel gate."
                ),
            },
        )

    def _formal_spec_from_contract(
        self,
        proof_spec: dict[str, Any],
        proof_artifact: dict[str, Any],
        conjecture: dict[str, Any],
        contract: dict[str, Any],
    ) -> FormalizationSpec:
        spec = FormalizationSpec(
            proof_spec_id=str(proof_spec["id"]),
            proof_artifact_id=str(proof_artifact["id"]),
            conjecture_id=str(conjecture["id"]),
            conjecture_statement=str(conjecture["statement"]),
            theorem_name=str(contract["theorem_name"]),
            theorem_signature=str(contract["theorem_signature"]),
            imports=list(contract["imports"]),
            backend=str(contract["backend"]),
            toolchain=str(contract["toolchain"]),
            generation=self.source_generation,
            metadata=dict(contract.get("metadata", {})),
        )
        spec.validate()
        return spec

    def _record_spec_graph(self, spec: FormalizationSpec, status: FormalStatus = "planned") -> None:
        payload = spec.to_dict()
        payload["generation"] = self.source_generation
        self.graph.add_node(
            ResearchNode(
                id=spec.id,
                type="formalization_spec",
                statement=spec.theorem_signature,
                status=status,
                payload=payload,
                created_at=spec.created_at,
            )
        )
        self.graph.add_edge(spec.id, "formalizes_proof", spec.proof_spec_id)
        self.graph.add_edge(spec.id, "formal_target_for", spec.conjecture_id)

    def _record_artifact_graph(self, artifact: FormalArtifact, source: str, status: FormalStatus) -> None:
        payload = artifact.to_dict()
        payload.update({"generation": self.source_generation, "source": source})
        self.graph.add_node(
            ResearchNode(
                id=artifact.id,
                type="formal_artifact",
                statement=f"Lean attempt {artifact.attempt}",
                status=status,
                payload=payload,
                created_at=artifact.created_at,
            )
        )
        self.graph.add_edge(artifact.id, "implements_formalization", artifact.formal_spec_id)
        if artifact.parent_artifact_id:
            self.graph.add_edge(artifact.parent_artifact_id, "repaired_into", artifact.id)

    def _record_kernel_graph(self, spec: FormalizationSpec, result: KernelResult) -> None:
        self.graph.add_node(
            ResearchNode(
                id=result.id,
                type="lean_kernel_result",
                statement=(
                    f"Lean formal verification passed ({result.detected_version})"
                    if result.passed
                    else f"Lean formal verification failed: {result.gate_reason or result.status}"
                ),
                status=result.status,
                payload={**result.to_dict(), "generation": self.source_generation},
                created_at=result.created_at,
            )
        )
        self.graph.add_edge(result.id, "checks_formal_artifact", result.formal_artifact_id)
        if result.passed:
            self.graph.add_edge(result.id, "formally_verifies", spec.conjecture_id)
            self.graph.add_edge(result.id, "formally_verifies_proof", spec.proof_spec_id)

    def _record_error(self, actor: str, formal_spec: FormalizationSpec | None, message: str) -> None:
        node = ResearchNode(
            type="formalization_error",
            statement=message[:240],
            status="failed",
            payload={
                "generation": self.source_generation,
                "actor": actor,
                "formal_spec_id": formal_spec.id if formal_spec else None,
                "error": message,
            },
        )
        self.graph.add_node(node)
        if formal_spec is not None:
            self.graph.add_edge(formal_spec.id, "formalization_failed", node.id)

    def _persist_source(self, artifact: FormalArtifact, source: str) -> Path:
        path = self.sources_dir / f"{artifact.id}.lean"
        path.write_text(source, encoding="utf-8")
        return path

    def _sync_invalidated_graph(self, proof_spec_id: str) -> None:
        spec_records = [item for item in self.memory.list_specs(100000) if item.get("proof_spec_id") == proof_spec_id]
        spec_ids = {str(item["id"]) for item in spec_records}
        artifact_records = [item for item in self.memory.list_artifacts(100000) if item.get("formal_spec_id") in spec_ids]
        artifact_ids = {str(item["id"]) for item in artifact_records}
        run_records = [item for item in self.memory.list_kernel_runs(100000) if item.get("formal_artifact_id") in artifact_ids]
        for record in spec_records:
            self.graph.add_node(
                ResearchNode(
                    id=str(record["id"]),
                    type="formalization_spec",
                    statement=str(record.get("theorem_signature", "formalization")),
                    status="invalidated",
                    payload=record,
                    created_at=str(record.get("created_at", "")),
                )
            )
        for record in artifact_records:
            self.graph.add_node(
                ResearchNode(
                    id=str(record["id"]),
                    type="formal_artifact",
                    statement=f"Lean attempt {record.get('attempt', '?')}",
                    status="invalidated",
                    payload=record,
                    created_at=str(record.get("created_at", "")),
                )
            )
        for record in run_records:
            self.graph.add_node(
                ResearchNode(
                    id=str(record["id"]),
                    type="lean_kernel_result",
                    statement="Historical Lean result invalidated by stale research proof lineage",
                    status="invalidated",
                    payload=record,
                    created_at=str(record.get("created_at", "")),
                )
            )

    def _invalidate_stale(self, proof_specs: list[dict[str, Any]]) -> int:
        current = {str(item["id"]): item for item in proof_specs}
        conjectures = {str(item["id"]): item for item in self.conjectures.recent_conjectures(100000)}
        count = 0
        for formal_spec in self.memory.list_specs(100000):
            if formal_spec.get("status") == "invalidated":
                continue
            proof_spec_id = str(formal_spec.get("proof_spec_id", ""))
            proof_spec = current.get(proof_spec_id)
            conjecture = conjectures.get(str(formal_spec.get("conjecture_id", "")))
            stale_reason = None
            if proof_spec is None or proof_spec.get("status") != "verified_natural_language":
                stale_reason = "source natural-language proof is no longer verified_natural_language"
            elif conjecture is None or conjecture.get("status") != "empirically_supported":
                stale_reason = "source conjecture is no longer empirically_supported"
            if stale_reason is not None:
                invalidated = self.memory.invalidate_for_proof_spec(proof_spec_id, stale_reason)
                if invalidated:
                    self._sync_invalidated_graph(proof_spec_id)
                    count += len(invalidated)
        return count

    def _attempt(
        self,
        formal_spec: FormalizationSpec,
        context: FormalContext,
    ) -> FormalStatus:
        self.memory.record_spec(formal_spec)
        self._record_spec_graph(formal_spec)
        try:
            artifact = self.formalizer.formalize(context, formal_spec)
            artifact.formal_spec_id = formal_spec.id
            artifact.attempt = 0
            artifact.parent_artifact_id = None
            artifact.validate()
        except Exception as exc:
            self.memory.set_spec_status(formal_spec.id, "invalid")
            self._record_spec_graph(formal_spec, status="invalid")
            self._record_error("formalizer", formal_spec, str(exc))
            return "invalid"

        attempt = 0
        current = artifact
        while True:
            result, source = self.kernel.verify(formal_spec, current, workspace=self.workspace)
            self.memory.record_artifact(current, source, status="generated")
            self._persist_source(current, source)
            self.memory.record_kernel_result(formal_spec.id, result)
            self._record_artifact_graph(current, source, result.status)
            self._record_kernel_graph(formal_spec, result)
            self._record_spec_graph(formal_spec, status=result.status)
            if result.status == "formal_verified":
                return "formal_verified"
            if result.status == "environment_error":
                return "environment_error"
            if attempt >= self.max_repairs or self.repairer is None:
                self.memory.set_spec_status(formal_spec.id, "repair_exhausted")
                self.memory.set_artifact_status(current.id, "repair_exhausted")
                self._record_artifact_graph(current, source, "repair_exhausted")
                self._record_spec_graph(formal_spec, status="repair_exhausted")
                return "repair_exhausted"

            attempt += 1
            context.previous_kernel_runs.append(result.to_dict())
            try:
                repaired = self.repairer.repair(
                    context,
                    formal_spec,
                    current,
                    result,
                    attempt,
                )
                repaired.formal_spec_id = formal_spec.id
                repaired.attempt = attempt
                repaired.parent_artifact_id = current.id
                repaired.validate()
            except Exception as exc:
                self.memory.set_spec_status(formal_spec.id, "invalid")
                self._record_spec_graph(formal_spec, status="invalid")
                self._record_error("repairer", formal_spec, str(exc))
                return "invalid"
            current = repaired

    def run(self) -> FormalRunSummary:
        proof_specs = self.proofs.list_specs(100000)
        proof_artifacts = self.proofs.list_artifacts(100000)
        proof_reviews = self.proofs.list_reviews(100000)
        invalidated_stale = self._invalidate_stale(proof_specs)
        candidates = self._valid_candidates()

        counts = {
            "considered": 0,
            "attempted": 0,
            "already": 0,
            "formal_verified": 0,
            "kernel_rejected": 0,
            "repair_exhausted": 0,
            "environment_error": 0,
            "invalid": 0,
            "missing_contract": 0,
        }

        verified_specs = [item for item in proof_specs if item.get("status") == "verified_natural_language"]
        for proof_spec in verified_specs[: self.max_targets]:
            counts["considered"] += 1
            proof_spec_id = str(proof_spec["id"])
            conjecture_id = str(proof_spec.get("conjecture_id", ""))
            conjecture = self._conjecture_record(conjecture_id)
            if conjecture is None or conjecture.get("status") != "empirically_supported":
                continue

            if self.memory.has_formal_verified_for_proof_spec(proof_spec_id):
                counts["already"] += 1
                continue

            contract = self.contracts.get(str(conjecture.get("statement", "")))
            if contract is None:
                counts["missing_contract"] += 1
                self._record_error(
                    "formal-contract",
                    None,
                    f"no frozen formal contract for conjecture: {conjecture.get('statement', '')}",
                )
                continue

            proof_artifact = self._latest_verified_artifact(proof_spec_id, proof_artifacts)
            if proof_artifact is None:
                counts["invalid"] += 1
                self._record_error("proof-lineage", None, f"verified ProofSpec {proof_spec_id} has no verified proof artifact")
                continue
            proof_review = self._review_for_artifact(str(proof_artifact["id"]), proof_reviews)
            if proof_review is None:
                counts["invalid"] += 1
                self._record_error("proof-lineage", None, f"verified proof artifact {proof_artifact['id']} has no verified review")
                continue

            try:
                formal_spec = self._formal_spec_from_contract(
                    proof_spec,
                    proof_artifact,
                    conjecture,
                    contract,
                )
            except Exception as exc:
                counts["invalid"] += 1
                self._record_error("formal-contract", None, str(exc))
                continue

            context = self._build_context(
                formal_spec,
                proof_spec,
                proof_artifact,
                proof_review,
                conjecture,
                candidates,
            )
            counts["attempted"] += 1
            status = self._attempt(formal_spec, context)
            if status == "formal_verified":
                counts["formal_verified"] += 1
            elif status == "environment_error":
                counts["environment_error"] += 1
            elif status == "repair_exhausted":
                counts["repair_exhausted"] += 1
            elif status == "kernel_rejected":
                counts["kernel_rejected"] += 1
            else:
                counts["invalid"] += 1

        summary = FormalRunSummary(
            considered_proofs=counts["considered"],
            attempted_formalizations=counts["attempted"],
            already_formal_verified=counts["already"],
            formal_verified=counts["formal_verified"],
            kernel_rejected=counts["kernel_rejected"],
            repair_exhausted=counts["repair_exhausted"],
            environment_error=counts["environment_error"],
            invalid=counts["invalid"],
            invalidated_stale=invalidated_stale,
            missing_contract=counts["missing_contract"],
            formal_manifest_fingerprint=self.formal_manifest_fingerprint,
            workspace=str(self.workspace),
        )
        (self.workspace / "formal_summary.json").write_text(
            json.dumps(summary.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return summary

    def close(self) -> None:
        self.db.close()
        self.conjectures.close()
        self.proofs.close()
        self.graph.close()
        self.memory.close()

    def __enter__(self) -> "FormalPipeline":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
