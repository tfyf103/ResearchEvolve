from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .candidates import Candidate, CandidateDB
from .conjectures import ConjectureMemory, Predicate
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

    Contracts are matched against both the exact natural-language conjecture
    statement and the normalized v0.4 machine predicate. This prevents two
    conjectures with identical prose but different executable semantics from
    silently sharing the same Lean target.
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

        required = {
            "source_manifest": self.workspace / "manifest.json",
            "proof_manifest": self.workspace / "proof_manifest.json",
            "proof_db": self.workspace / "proofs.sqlite3",
            "candidate_db": self.workspace / "candidates.sqlite3",
            "conjecture_db": self.workspace / "conjectures.sqlite3",
            "graph_db": self.workspace / "research_graph.sqlite3",
            "summary": self.workspace / "summary.json",
        }
        for label, path in required.items():
            if not path.is_file():
                raise ValueError(f"formal pipeline requires {label}: {path}")

        self.source_manifest = json.loads(required["source_manifest"].read_text(encoding="utf-8"))
        self.proof_manifest = json.loads(required["proof_manifest"].read_text(encoding="utf-8"))
        summary = json.loads(required["summary"].read_text(encoding="utf-8"))
        self.source_generation = int(summary.get("generation_completed", 0))

        proof_inputs = self.proof_manifest.get("inputs", {})
        if not isinstance(proof_inputs, dict):
            raise ValueError("proof_manifest inputs must be an object")
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

        formal_db = self.workspace / "formal.sqlite3"
        formal_manifest = self.workspace / "formal_manifest.json"
        if formal_db.is_file() and not formal_manifest.is_file():
            raise ValueError("formal.sqlite3 exists without formal_manifest.json; refusing unaudited formal journal")

        self.db = CandidateDB(required["candidate_db"])
        self.conjectures = ConjectureMemory(required["conjecture_db"])
        self.proofs = ProofMemory(required["proof_db"])
        self.graph = ResearchGraph(required["graph_db"])
        self.memory = FormalMemory(formal_db)
        self.sources_dir = self.workspace / "formal_sources"
        self.sources_dir.mkdir(parents=True, exist_ok=True)

        self.formal_manifest = self._build_manifest()
        self.formal_manifest_fingerprint = str(self.formal_manifest["fingerprint"])
        self._ensure_manifest(formal_manifest)

    @staticmethod
    def _contract_key(statement: str, predicate: dict[str, Any]) -> str:
        normalized = Predicate.from_dict(dict(predicate)).to_dict()
        return stable_json_hash({"statement": statement.strip(), "predicate": normalized})

    @classmethod
    def _validate_contracts(cls, raw_contracts: list[Any]) -> dict[str, dict[str, Any]]:
        contracts: dict[str, dict[str, Any]] = {}
        for index, raw in enumerate(raw_contracts):
            if not isinstance(raw, dict):
                raise ValueError(f"formal contract #{index} must be an object")
            statement = str(raw.get("conjecture_statement", "")).strip()
            if not statement:
                raise ValueError(f"formal contract #{index} requires conjecture_statement")
            raw_predicate = raw.get("conjecture_predicate")
            if not isinstance(raw_predicate, dict):
                raise ValueError(f"formal contract #{index} requires conjecture_predicate object")
            normalized_predicate = Predicate.from_dict(dict(raw_predicate)).to_dict()
            key = cls._contract_key(statement, normalized_predicate)
            if key in contracts:
                raise ValueError(f"duplicate formal contract for statement/predicate pair: {statement!r}")
            imports = raw.get("imports", [])
            if not isinstance(imports, list):
                raise ValueError(f"formal contract #{index} imports must be a list")
            contract_metadata = raw.get("metadata", {})
            if not isinstance(contract_metadata, dict):
                raise ValueError(f"formal contract #{index} metadata must be an object")
            contracts[key] = {
                "conjecture_statement": statement,
                "conjecture_predicate": normalized_predicate,
                "backend": str(raw.get("backend", "lean4")),
                "toolchain": str(raw.get("toolchain", "leanprover/lean4:v4.30.0")),
                "theorem_name": str(raw.get("theorem_name", "")).strip(),
                "theorem_signature": str(raw.get("theorem_signature", "")).strip(),
                "imports": [str(item) for item in imports],
                "preamble": str(raw.get("preamble", "")),
                "metadata": dict(contract_metadata),
            }
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
                "formal_verified is granted only by the configured Lean kernel gate on a frozen statement+predicate formal contract. "
                "Natural-language actors cannot directly assign formal_verified."
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

    def _conjecture_records(self) -> dict[str, dict[str, Any]]:
        return {str(item["id"]): item for item in self.conjectures.recent_conjectures(100000)}

    @staticmethod
    def _latest_verified_artifact(proof_spec_id: str, artifacts: list[dict[str, Any]]) -> dict[str, Any] | None:
        for artifact in artifacts:
            if artifact.get("proof_spec_id") == proof_spec_id and artifact.get("status") == "verified_natural_language":
                return artifact
        return None

    @staticmethod
    def _verified_review(artifact_id: str, reviews: list[dict[str, Any]]) -> dict[str, Any] | None:
        for review in reviews:
            if review.get("proof_artifact_id") == artifact_id and review.get("gated_status") == "verified_natural_language":
                return review
        return None

    def _formal_spec(
        self,
        proof_spec: dict[str, Any],
        proof_artifact: dict[str, Any],
        conjecture: dict[str, Any],
        contract: dict[str, Any],
    ) -> FormalizationSpec:
        metadata = dict(contract.get("metadata", {}))
        metadata["frozen_conjecture_predicate"] = dict(contract["conjecture_predicate"])
        spec = FormalizationSpec(
            proof_spec_id=str(proof_spec["id"]),
            proof_artifact_id=str(proof_artifact["id"]),
            conjecture_id=str(conjecture["id"]),
            conjecture_statement=str(conjecture["statement"]),
            theorem_name=str(contract["theorem_name"]),
            theorem_signature=str(contract["theorem_signature"]),
            imports=list(contract["imports"]),
            preamble=str(contract.get("preamble", "")),
            backend=str(contract["backend"]),
            toolchain=str(contract["toolchain"]),
            generation=self.source_generation,
            metadata=metadata,
        )
        spec.validate()
        return spec

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
        known = {candidate.id for candidate in evidence}
        for candidate in sorted(
            candidates,
            key=lambda item: item.score if item.score is not None else float("-inf"),
            reverse=True,
        ):
            if len(evidence) >= self.evidence_context:
                break
            if candidate.id in known:
                continue
            evidence.append(candidate)
            known.add(candidate.id)
        observation_ids = set(conjecture.get("observation_ids", []))
        observations = [item for item in self.conjectures.recent_observations(1000) if item.get("id") in observation_ids]
        return FormalContext(
            problem=self.problem,
            generation=self.source_generation,
            formal_spec=formal_spec.to_dict(),
            proof_spec=proof_spec,
            proof_artifact=proof_artifact,
            proof_review=proof_review,
            conjecture=conjecture,
            observations=observations,
            evidence_candidates=[self._candidate_snapshot(item) for item in evidence[: self.evidence_context]],
            previous_kernel_runs=[],
            metadata={
                "domain": self.domain,
                "truth_policy": (
                    "The conjecture statement+machine predicate, Lean imports, preamble definitions, and theorem signature are frozen. "
                    "Empirical/natural-language evidence is context only; formal_verified requires the Lean kernel gate."
                ),
            },
        )

    def _record_spec_graph(self, spec: FormalizationSpec, status: FormalStatus) -> None:
        self.graph.add_node(
            ResearchNode(
                id=spec.id,
                type="formalization_spec",
                statement=spec.theorem_signature,
                status=status,
                payload=spec.to_dict(),
                created_at=spec.created_at,
            )
        )
        self.graph.add_edge(spec.id, "formalizes_proof", spec.proof_spec_id)
        self.graph.add_edge(spec.id, "formal_target_for", spec.conjecture_id)

    def _record_artifact_graph(self, artifact: FormalArtifact, source: str, status: FormalStatus) -> None:
        self.graph.add_node(
            ResearchNode(
                id=artifact.id,
                type="formal_artifact",
                statement=f"Lean proof attempt {artifact.attempt}",
                status=status,
                payload={**artifact.to_dict(), "generation": self.source_generation, "source": source},
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
                    else f"Lean gate failed: {result.gate_reason or result.status}"
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

    def _persist_source(self, artifact: FormalArtifact, source: str) -> None:
        (self.sources_dir / f"{artifact.id}.lean").write_text(source, encoding="utf-8")

    def _sync_invalidated_graph(self, proof_spec_id: str) -> None:
        specs = [item for item in self.memory.list_specs(100000) if item.get("proof_spec_id") == proof_spec_id]
        spec_ids = {str(item["id"]) for item in specs}
        artifacts = [item for item in self.memory.list_artifacts(100000) if item.get("formal_spec_id") in spec_ids]
        artifact_ids = {str(item["id"]) for item in artifacts}
        runs = [item for item in self.memory.list_kernel_runs(100000) if item.get("formal_artifact_id") in artifact_ids]
        for item in specs:
            self.graph.add_node(
                ResearchNode(
                    id=str(item["id"]),
                    type="formalization_spec",
                    statement=str(item.get("theorem_signature", "formalization")),
                    status="invalidated",
                    payload=item,
                    created_at=str(item.get("created_at", "")),
                )
            )
        for item in artifacts:
            self.graph.add_node(
                ResearchNode(
                    id=str(item["id"]),
                    type="formal_artifact",
                    statement=f"Lean proof attempt {item.get('attempt', '?')}",
                    status="invalidated",
                    payload=item,
                    created_at=str(item.get("created_at", "")),
                )
            )
        for item in runs:
            self.graph.add_node(
                ResearchNode(
                    id=str(item["id"]),
                    type="lean_kernel_result",
                    statement="Historical Lean result invalidated by stale research proof lineage",
                    status="invalidated",
                    payload=item,
                    created_at=str(item.get("created_at", "")),
                )
            )

    def _invalidate_stale(self, proof_specs: list[dict[str, Any]], conjectures: dict[str, dict[str, Any]]) -> int:
        current = {str(item["id"]): item for item in proof_specs}
        invalidated_count = 0
        for formal_spec in self.memory.list_specs(100000):
            if formal_spec.get("status") == "invalidated":
                continue
            proof_spec_id = str(formal_spec.get("proof_spec_id", ""))
            proof_spec = current.get(proof_spec_id)
            conjecture = conjectures.get(str(formal_spec.get("conjecture_id", "")))
            reason = None
            if proof_spec is None or proof_spec.get("status") != "verified_natural_language":
                reason = "source natural-language proof is no longer verified_natural_language"
            elif conjecture is None or conjecture.get("status") != "empirically_supported":
                reason = "source conjecture is no longer empirically_supported"
            if reason:
                invalidated = self.memory.invalidate_for_proof_spec(proof_spec_id, reason)
                if invalidated:
                    invalidated_count += len(invalidated)
                    self._sync_invalidated_graph(proof_spec_id)
        return invalidated_count

    def _attempt(self, spec: FormalizationSpec, context: FormalContext) -> FormalStatus:
        self.memory.record_spec(spec, status="planned")
        self._record_spec_graph(spec, "planned")
        try:
            current = self.formalizer.formalize(context, spec)
            current.formal_spec_id = spec.id
            current.attempt = 0
            current.parent_artifact_id = None
            current.validate()
        except Exception as exc:
            self.memory.set_spec_status(spec.id, "invalid")
            self._record_spec_graph(spec, "invalid")
            self._record_error("formalizer", spec, str(exc))
            return "invalid"

        attempt = 0
        while True:
            result, source = self.kernel.verify(spec, current, workspace=self.workspace)
            self.memory.record_artifact(current, source, status="generated")
            self._persist_source(current, source)
            self.memory.record_kernel_result(spec.id, result)
            self._record_artifact_graph(current, source, result.status)
            self._record_kernel_graph(spec, result)
            self._record_spec_graph(spec, result.status)
            if result.status == "formal_verified":
                return "formal_verified"
            if result.status == "environment_error":
                return "environment_error"
            if attempt >= self.max_repairs or self.repairer is None:
                self.memory.set_spec_status(spec.id, "repair_exhausted")
                self.memory.set_artifact_status(current.id, "repair_exhausted")
                self._record_artifact_graph(current, source, "repair_exhausted")
                self._record_spec_graph(spec, "repair_exhausted")
                return "repair_exhausted"

            attempt += 1
            context.previous_kernel_runs.append(result.to_dict())
            try:
                repaired = self.repairer.repair(context, spec, current, result, attempt)
                repaired.formal_spec_id = spec.id
                repaired.attempt = attempt
                repaired.parent_artifact_id = current.id
                repaired.validate()
            except Exception as exc:
                self.memory.set_spec_status(spec.id, "invalid")
                self._record_spec_graph(spec, "invalid")
                self._record_error("repairer", spec, str(exc))
                return "invalid"
            current = repaired

    def run(self) -> FormalRunSummary:
        proof_specs = self.proofs.list_specs(100000)
        proof_artifacts = self.proofs.list_artifacts(100000)
        proof_reviews = self.proofs.list_reviews(100000)
        conjectures = self._conjecture_records()
        invalidated_stale = self._invalidate_stale(proof_specs, conjectures)
        candidates = self._valid_candidates()

        considered = attempted = already = verified = exhausted = env_error = invalid = missing = 0
        verified_specs = [item for item in proof_specs if item.get("status") == "verified_natural_language"]
        for proof_spec in verified_specs[: self.max_targets]:
            considered += 1
            proof_spec_id = str(proof_spec["id"])
            conjecture = conjectures.get(str(proof_spec.get("conjecture_id", "")))
            if conjecture is None or conjecture.get("status") != "empirically_supported":
                continue
            if self.memory.has_formal_verified_for_proof_spec(proof_spec_id):
                already += 1
                continue

            predicate = conjecture.get("predicate")
            if not isinstance(predicate, dict):
                invalid += 1
                self._record_error("formal-contract", None, "conjecture has no machine predicate")
                continue
            try:
                contract_key = self._contract_key(str(conjecture.get("statement", "")), predicate)
            except (TypeError, ValueError) as exc:
                invalid += 1
                self._record_error("formal-contract", None, f"could not normalize conjecture predicate: {exc}")
                continue
            contract = self.contracts.get(contract_key)
            if contract is None:
                missing += 1
                self._record_error(
                    "formal-contract",
                    None,
                    f"no frozen formal contract for exact conjecture statement+predicate: {conjecture.get('statement', '')}",
                )
                continue

            proof_artifact = self._latest_verified_artifact(proof_spec_id, proof_artifacts)
            if proof_artifact is None:
                invalid += 1
                self._record_error("proof-lineage", None, f"verified ProofSpec {proof_spec_id} has no verified artifact")
                continue
            proof_review = self._verified_review(str(proof_artifact["id"]), proof_reviews)
            if proof_review is None:
                invalid += 1
                self._record_error(
                    "proof-lineage",
                    None,
                    f"verified proof artifact {proof_artifact['id']} has no verified independent review",
                )
                continue

            try:
                formal_spec = self._formal_spec(proof_spec, proof_artifact, conjecture, contract)
            except Exception as exc:
                invalid += 1
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
            attempted += 1
            status = self._attempt(formal_spec, context)
            if status == "formal_verified":
                verified += 1
            elif status == "environment_error":
                env_error += 1
            elif status == "repair_exhausted":
                exhausted += 1
            else:
                invalid += 1

        summary = FormalRunSummary(
            considered_proofs=considered,
            attempted_formalizations=attempted,
            already_formal_verified=already,
            formal_verified=verified,
            repair_exhausted=exhausted,
            environment_error=env_error,
            invalid=invalid,
            invalidated_stale=invalidated_stale,
            missing_contract=missing,
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
