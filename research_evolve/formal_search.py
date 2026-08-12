from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .formal import FormalArtifact, FormalStatus, FormalizationSpec, KernelResult
from .formal_agents import FormalContext, FormalRepairer, Formalizer
from .formal_pipeline import FormalPipeline
from .goal_retrieval import GoalPremiseSelection, GoalPremiseSelector, GoalRetrievalMemory
from .graph import ResearchNode
from .lean_kernel import LeanKernel
from .reproducibility import stable_json_hash


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True, frozen=True)
class FormalSearchPolicy:
    beam_width: int = 3
    branching_factor: int = 2
    max_rounds: int = 3
    max_kernel_attempts: int = 12

    def validate(self) -> None:
        if self.beam_width < 1:
            raise ValueError("formal search beam_width must be positive")
        if self.branching_factor < 1:
            raise ValueError("formal search branching_factor must be positive")
        if self.max_rounds < 1:
            raise ValueError("formal search max_rounds must be positive")
        if self.max_kernel_attempts < 1:
            raise ValueError("formal search max_kernel_attempts must be positive")

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(slots=True)
class FormalSearchEvent:
    formal_spec_id: str
    round: int
    variant: int
    artifact_id: str
    parent_artifact_id: str | None
    kernel_result_id: str
    status: str
    gate_reason: str
    rank_key: list[Any]
    premise_selection_id: str | None
    source_sha256: str
    id: str = field(default_factory=lambda: f"formal-search-event-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FormalSearchMemory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS search_events (
                id TEXT PRIMARY KEY,
                formal_spec_id TEXT NOT NULL,
                round INTEGER NOT NULL,
                artifact_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_search_events_spec ON search_events(formal_spec_id);
            """
        )
        self.conn.commit()

    def record(self, event: FormalSearchEvent) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO search_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event.id,
                event.formal_spec_id,
                event.round,
                event.artifact_id,
                event.status,
                json.dumps(event.to_dict(), sort_keys=True),
                event.created_at,
            ),
        )
        self.conn.commit()

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT payload FROM search_events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def close(self) -> None:
        self.conn.close()


@dataclass(slots=True)
class _SearchState:
    artifact: FormalArtifact
    result: KernelResult
    source: str
    selection_id: str | None
    round: int
    variant: int


class FormalSearchPipeline(FormalPipeline):
    """v0.8 goal-conditioned premise retrieval plus budgeted beam proof search."""

    def __init__(
        self,
        workspace: str | Path,
        formalizer: Formalizer,
        kernel: LeanKernel,
        repairer: FormalRepairer | None = None,
        *,
        premise_selector: GoalPremiseSelector,
        search_policy: FormalSearchPolicy | None = None,
        max_targets: int = 4,
        evidence_context: int = 24,
    ) -> None:
        self.premise_selector = premise_selector
        self.search_policy = search_policy or FormalSearchPolicy()
        self.search_policy.validate()
        super().__init__(
            workspace,
            formalizer,
            kernel,
            repairer,
            max_targets=max_targets,
            max_repairs=0,
            evidence_context=evidence_context,
        )
        self.goal_retrieval_memory = GoalRetrievalMemory(self.workspace / "formal_goal_retrieval.sqlite3")
        self.search_memory = FormalSearchMemory(self.workspace / "formal_search.sqlite3")

    def _build_manifest(self) -> dict[str, Any]:
        manifest = super()._build_manifest()
        stable = dict(manifest["inputs"])
        stable["goal_premise_selector"] = self.premise_selector.name
        stable["formal_corpus_fingerprint"] = self.premise_selector.corpus.fingerprint
        stable["formal_corpus_project_fingerprint"] = self.premise_selector.corpus.project_fingerprint
        stable["formal_search_policy"] = self.search_policy.to_dict()
        manifest["schema_version"] = 3
        manifest["inputs"] = stable
        manifest["fingerprint"] = stable_json_hash(stable)
        manifest["truth_policy"] = (
            "v0.8 retrieval/search can rank reachable premises and explore theorem bodies, but cannot widen frozen imports, "
            "change the theorem target/project/toolchain/axiom policy, or grant formal_verified. "
            "formal_verified still requires the configured Lean gate."
        )
        return manifest

    @staticmethod
    def _base_query(
        formal_spec: FormalizationSpec,
        proof_spec: dict[str, Any],
        proof_artifact: dict[str, Any],
        conjecture: dict[str, Any],
    ) -> str:
        lemma_arguments = proof_artifact.get("lemma_arguments", {})
        lemma_text = ""
        if isinstance(lemma_arguments, dict):
            lemma_text = " ".join(str(value) for value in lemma_arguments.values())
        return "\n".join(
            [
                formal_spec.theorem_name,
                formal_spec.theorem_signature,
                str(conjecture.get("statement", "")),
                str(proof_spec.get("statement", "")),
                str(proof_artifact.get("final_argument", "")),
                lemma_text,
            ]
        )

    @staticmethod
    def _diagnostic_text(result: KernelResult) -> str:
        lines = [result.gate_reason, result.stdout, result.stderr]
        for item in result.diagnostics:
            lines.append(f"{item.severity} {item.line or ''}:{item.column or ''} {item.message}")
        return "\n".join(part for part in lines if part)

    def _validate_corpus_contract(self, formal_spec: FormalizationSpec) -> None:
        expected_project = str(formal_spec.metadata.get("project_fingerprint", "")).strip()
        expected_corpus = str(formal_spec.metadata.get("formal_corpus_fingerprint", "")).strip()
        if not expected_project:
            raise ValueError("v0.8 formal search requires project_fingerprint in the frozen formal contract")
        if expected_project != self.premise_selector.corpus.project_fingerprint:
            raise ValueError(
                "formal corpus belongs to a different Lean project: "
                f"contract={expected_project}, corpus={self.premise_selector.corpus.project_fingerprint}"
            )
        if not expected_corpus:
            raise ValueError("v0.8 formal search requires formal_corpus_fingerprint in the frozen formal contract")
        if expected_corpus != self.premise_selector.corpus.fingerprint:
            raise ValueError(
                "formal corpus does not match the frozen formal contract: "
                f"contract={expected_corpus}, corpus={self.premise_selector.corpus.fingerprint}"
            )

    def _record_selection_graph(self, selection: GoalPremiseSelection) -> None:
        self.graph.add_node(
            ResearchNode(
                id=selection.id,
                type="goal_premise_selection",
                statement=f"Selected {len(selection.selected)} goal-conditioned premises",
                status="retrieved",
                payload=selection.to_dict(),
                created_at=selection.created_at,
            )
        )
        self.graph.add_edge(selection.id, "selects_for_formalization", selection.formal_spec_id)
        for scored in selection.selected:
            premise = scored.premise
            self.graph.add_node(
                ResearchNode(
                    id=premise.id,
                    type="formal_corpus_premise",
                    statement=f"{premise.name} {premise.statement}".strip(),
                    status="retrieved",
                    payload={**premise.to_dict(), "score": scored.score},
                )
            )
            self.graph.add_edge(selection.id, "selected_premise", premise.id)
            self.graph.add_edge(premise.id, "premise_supports", selection.formal_spec_id)

    def _select(self, spec: FormalizationSpec, query: str, *, diagnostics: str = "") -> GoalPremiseSelection:
        selection = self.premise_selector.select(
            formal_spec_id=spec.id,
            query=query,
            root_imports=spec.imports,
            diagnostics=diagnostics,
        )
        self.goal_retrieval_memory.record(selection)
        self._record_selection_graph(selection)
        return selection

    def _build_context(
        self,
        formal_spec: FormalizationSpec,
        proof_spec: dict[str, Any],
        proof_artifact: dict[str, Any],
        proof_review: dict[str, Any],
        conjecture: dict[str, Any],
        candidates: list[Any],
    ) -> FormalContext:
        context = super()._build_context(
            formal_spec,
            proof_spec,
            proof_artifact,
            proof_review,
            conjecture,
            candidates,
        )
        self._validate_corpus_contract(formal_spec)
        query = self._base_query(formal_spec, proof_spec, proof_artifact, conjecture)
        selection = self._select(formal_spec, query)
        context.retrieved_premises = [item.to_dict() for item in selection.selected]
        context.metadata.update(
            {
                "formal_corpus_fingerprint": selection.corpus_fingerprint,
                "goal_premise_selection_id": selection.id,
                "retrieval_policy": (
                    "Premises are selected only from the transitive module closure of frozen FormalizationSpec.imports. "
                    "Retrieval cannot add imports."
                ),
            }
        )
        return context

    @staticmethod
    def _rank_key(result: KernelResult, source: str) -> tuple[int, int, int, str]:
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if result.status == "formal_verified":
            return (-1, 0, 0, digest)
        error_count = sum(1 for item in result.diagnostics if item.severity == "error")
        gate_penalty = 0 if result.gate_reason in {"lean-exit-nonzero", "lean-reported-error"} else 1
        return (gate_penalty, error_count, len(source), digest)

    def _persist_evaluation(
        self,
        spec: FormalizationSpec,
        artifact: FormalArtifact,
        *,
        selection_id: str | None,
        round_number: int,
        variant: int,
    ) -> _SearchState:
        result, source = self.kernel.verify(spec, artifact, workspace=self.workspace)
        self.memory.record_artifact(artifact, source, status="generated")
        self._persist_source(artifact, source)
        self.memory.record_kernel_result(spec.id, result)
        self._record_artifact_graph(artifact, source, result.status)
        self._record_kernel_graph(spec, result)
        self._record_spec_graph(spec, result.status)
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        rank = self._rank_key(result, source)
        event = FormalSearchEvent(
            formal_spec_id=spec.id,
            round=round_number,
            variant=variant,
            artifact_id=artifact.id,
            parent_artifact_id=artifact.parent_artifact_id,
            kernel_result_id=result.id,
            status=result.status,
            gate_reason=result.gate_reason,
            rank_key=list(rank),
            premise_selection_id=selection_id,
            source_sha256=digest,
        )
        self.search_memory.record(event)
        self.graph.add_node(
            ResearchNode(
                id=event.id,
                type="formal_search_event",
                statement=f"Formal search round {round_number} variant {variant}: {result.status}",
                status=result.status,
                payload=event.to_dict(),
                created_at=event.created_at,
            )
        )
        self.graph.add_edge(event.id, "evaluates_formal_artifact", artifact.id)
        return _SearchState(artifact, result, source, selection_id, round_number, variant)

    def _attempt(self, spec: FormalizationSpec, context: FormalContext) -> FormalStatus:
        self.memory.record_spec(spec, status="planned")
        self._record_spec_graph(spec, "planned")
        try:
            initial = self.formalizer.formalize(context, spec)
            initial.formal_spec_id = spec.id
            initial.attempt = 0
            initial.parent_artifact_id = None
            initial.validate()
        except Exception as exc:
            self.memory.set_spec_status(spec.id, "invalid")
            self._record_spec_graph(spec, "invalid")
            self._record_error("formalizer", spec, str(exc))
            return "invalid"

        initial_selection_id = str(context.metadata.get("goal_premise_selection_id", "")) or None
        state = self._persist_evaluation(spec, initial, selection_id=initial_selection_id, round_number=0, variant=0)
        attempts = 1
        if state.result.status == "formal_verified":
            return "formal_verified"
        if state.result.status == "environment_error":
            return "environment_error"
        if self.repairer is None:
            self.memory.set_spec_status(spec.id, "repair_exhausted")
            self.memory.set_artifact_status(state.artifact.id, "repair_exhausted")
            return "repair_exhausted"

        frontier = [state]
        seen_sources = {hashlib.sha256(state.source.encode("utf-8")).hexdigest()}
        base_query = "\n".join(
            [
                spec.theorem_name,
                spec.theorem_signature,
                str(context.conjecture.get("statement", "")),
                str(context.proof_artifact.get("final_argument", "")),
            ]
        )
        actor_attempt = 0

        for round_number in range(1, self.search_policy.max_rounds + 1):
            generated_states: list[_SearchState] = []
            for parent_index, parent in enumerate(frontier):
                if attempts >= self.search_policy.max_kernel_attempts:
                    break
                diagnostics = self._diagnostic_text(parent.result)
                selection = self._select(spec, base_query, diagnostics=diagnostics)
                selected_payloads = [item.to_dict() for item in selection.selected]
                context.previous_kernel_runs.append(parent.result.to_dict())
                for variant in range(self.search_policy.branching_factor):
                    if attempts >= self.search_policy.max_kernel_attempts:
                        break
                    actor_attempt += 1
                    if selected_payloads:
                        shift = variant % len(selected_payloads)
                        rotated = selected_payloads[shift:] + selected_payloads[:shift]
                    else:
                        rotated = []
                    context.retrieved_premises = rotated
                    context.metadata.update(
                        {
                            "goal_premise_selection_id": selection.id,
                            "search_round": round_number,
                            "search_variant": variant,
                            "search_parent_index": parent_index,
                            "search_policy": self.search_policy.to_dict(),
                        }
                    )
                    try:
                        repaired = self.repairer.repair(context, spec, parent.artifact, parent.result, actor_attempt)
                        repaired.formal_spec_id = spec.id
                        repaired.attempt = actor_attempt
                        repaired.parent_artifact_id = parent.artifact.id
                        repaired.validate()
                    except Exception as exc:
                        self._record_error("formal-repairer", spec, str(exc))
                        continue
                    candidate_source = repaired.build_source(spec)
                    candidate_digest = hashlib.sha256(candidate_source.encode("utf-8")).hexdigest()
                    if candidate_digest in seen_sources:
                        continue
                    seen_sources.add(candidate_digest)
                    child = self._persist_evaluation(
                        spec,
                        repaired,
                        selection_id=selection.id,
                        round_number=round_number,
                        variant=variant,
                    )
                    attempts += 1
                    if child.result.status == "formal_verified":
                        return "formal_verified"
                    if child.result.status == "environment_error":
                        return "environment_error"
                    generated_states.append(child)

            if not generated_states:
                break
            generated_states.sort(key=lambda item: self._rank_key(item.result, item.source))
            frontier = generated_states[: self.search_policy.beam_width]

        self.memory.set_spec_status(spec.id, "repair_exhausted")
        for item in frontier:
            self.memory.set_artifact_status(item.artifact.id, "repair_exhausted")
            self._record_artifact_graph(item.artifact, item.source, "repair_exhausted")
        self._record_spec_graph(spec, "repair_exhausted")
        return "repair_exhausted"

    def close(self) -> None:
        if hasattr(self, "goal_retrieval_memory"):
            self.goal_retrieval_memory.close()
        if hasattr(self, "search_memory"):
            self.search_memory.close()
        self.premise_selector.corpus.close()
        super().close()
