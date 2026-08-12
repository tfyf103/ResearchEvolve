from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from .formal import FormalizationSpec, KernelResult
from .formal_agents import FormalContext, FormalRepairer, Formalizer
from .formal_pipeline import FormalPipeline
from .formal_retrieval import FormalRetrievalMemory, PremiseSelection, PremiseSelector
from .graph import ResearchNode
from .lean_kernel import LeanKernel
from .reproducibility import stable_json_hash
from .semantic_bridge import CertifiedSemanticBridge


class RetrievalFormalPipeline(FormalPipeline):
    """v0.8 FormalPipeline extension for goal-conditioned generation/repair retrieval."""

    def __init__(
        self,
        workspace: str | Path,
        formalizer: Formalizer,
        kernel: LeanKernel,
        repairer: FormalRepairer | None = None,
        *,
        premise_selector: PremiseSelector,
        max_targets: int = 4,
        max_repairs: int = 2,
        evidence_context: int = 24,
        semantic_bridge: CertifiedSemanticBridge | None = None,
    ) -> None:
        self.premise_selector = premise_selector
        super().__init__(
            workspace,
            formalizer,
            kernel,
            repairer,
            max_targets=max_targets,
            max_repairs=max_repairs,
            evidence_context=evidence_context,
            semantic_bridge=semantic_bridge,
        )
        self.retrieval_memory = FormalRetrievalMemory(self.workspace / "formal_retrieval.sqlite3")

    def _build_manifest(self) -> dict[str, Any]:
        manifest = super()._build_manifest()
        stable = dict(manifest["inputs"])
        stable["premise_selector"] = self.premise_selector.name
        stable["premise_index_fingerprint"] = self.premise_selector.index.fingerprint
        stable["premise_project_fingerprint"] = self.premise_selector.index.project_fingerprint
        stable["proof_search_budget"] = asdict(self.premise_selector.budget)
        manifest["schema_version"] = 3
        manifest["inputs"] = stable
        manifest["fingerprint"] = stable_json_hash(stable)
        manifest["truth_policy"] = (
            "formal_verified is granted only by the configured Lean gate on the frozen statement+predicate contract. "
            "v0.8 goal-conditioned retrieval and dependency expansion are advisory, content-addressed, and budgeted; "
            "they cannot change frozen imports, definitions, theorem signature, project/index fingerprints, or toolchain."
        )
        return manifest

    @staticmethod
    def _query(
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

    def _record_selection_graph(self, selection: PremiseSelection) -> None:
        self.graph.add_node(
            ResearchNode(
                id=selection.id,
                type="premise_selection",
                statement=f"Selected {len(selection.selected)} formal premises",
                status="retrieved",
                payload=selection.to_dict(),
                created_at=selection.created_at,
            )
        )
        self.graph.add_edge(selection.id, "selects_for_formalization", selection.formal_spec_id)
        for scored in selection.selected:
            premise = scored.premise
            premise_id = "premise-" + stable_json_hash(
                {
                    "index": selection.index_fingerprint,
                    "module": premise.module,
                    "name": premise.name,
                    "statement": premise.statement,
                }
            )[:32]
            self.graph.add_node(
                ResearchNode(
                    id=premise_id,
                    type="formal_premise",
                    statement=f"{premise.name} {premise.statement}".strip(),
                    status="retrieved",
                    payload={**premise.to_dict(), "index_fingerprint": selection.index_fingerprint, "score": scored.score},
                )
            )
            self.graph.add_edge(selection.id, "selected_premise", premise_id)
            self.graph.add_edge(premise_id, "premise_supports", selection.formal_spec_id)

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
        expected_project = str(formal_spec.metadata.get("project_fingerprint", "")).strip()
        if not expected_project:
            raise ValueError("retrieval mode requires project_fingerprint in the frozen formal contract")
        if expected_project != self.premise_selector.index.project_fingerprint:
            raise ValueError(
                "premise index belongs to a different Lean project: "
                f"contract={expected_project}, index={self.premise_selector.index.project_fingerprint}"
            )
        expected_index = str(formal_spec.metadata.get("premise_index_fingerprint", "")).strip()
        if not expected_index:
            raise ValueError("retrieval mode requires premise_index_fingerprint in the frozen formal contract")
        if expected_index != self.premise_selector.index.fingerprint:
            raise ValueError(
                "premise index does not match the frozen formal contract: "
                f"contract={expected_index}, index={self.premise_selector.index.fingerprint}"
            )
        query = self._query(formal_spec, proof_spec, proof_artifact, conjecture)
        selection = self.premise_selector.select(
            formal_spec_id=formal_spec.id,
            query=query,
            allowed_modules=formal_spec.imports,
            goal_state=formal_spec.theorem_signature,
        )
        self.retrieval_memory.record(selection)
        self._record_selection_graph(selection)
        context.retrieved_premises = [item.to_dict() for item in selection.selected]
        context.metadata["premise_index_fingerprint"] = selection.index_fingerprint
        context.metadata["premise_project_fingerprint"] = self.premise_selector.index.project_fingerprint
        context.metadata["retrieval_round"] = 0
        context.metadata["retrieval_goal_state"] = formal_spec.theorem_signature
        context.metadata["retrieval_budget"] = selection.budget
        context.metadata["retrieval_stats"] = selection.stats
        context.metadata["retrieval_policy"] = (
            "Retrieved premises are advisory, goal-conditioned, budgeted, and restricted to modules already present in the frozen FormalizationSpec imports."
        )
        return context

    def _prepare_repair_context(
        self,
        context: FormalContext,
        spec: FormalizationSpec,
        result: KernelResult,
        attempt: int,
    ) -> FormalContext:
        diagnostics = "\n".join(item.message for item in result.diagnostics)
        goal_state = "\n".join([spec.theorem_signature, diagnostics, result.stderr[-4000:]])
        query = "\n".join([spec.theorem_name, spec.theorem_signature, diagnostics, result.stderr[-4000:]])
        previous_names = [str(item.get("name", "")) for item in context.retrieved_premises]
        selection = self.premise_selector.select(
            formal_spec_id=spec.id,
            query=query,
            goal_state=goal_state,
            allowed_modules=spec.imports,
            round=attempt,
            excluded_names=previous_names,
        )
        self.retrieval_memory.record(selection)
        self._record_selection_graph(selection)
        fresh = [item.to_dict() for item in selection.selected]
        previous = list(context.retrieved_premises)
        reserve_previous = min(len(previous), self.premise_selector.budget.max_results // 2)
        fresh_limit = max(0, self.premise_selector.budget.max_results - reserve_previous)
        ordered = [*fresh[:fresh_limit], *previous[:reserve_previous], *fresh[fresh_limit:], *previous[reserve_previous:]]
        combined: list[dict[str, Any]] = []
        seen: set[str] = set()
        context_chars = 0
        for item in ordered:
            name = str(item.get("name", ""))
            if not name or name in seen:
                continue
            size = len(json.dumps(item, ensure_ascii=False))
            if context_chars + size > self.premise_selector.budget.max_context_chars:
                continue
            combined.append(item)
            seen.add(name)
            context_chars += size
            if len(combined) >= self.premise_selector.budget.max_results:
                break
        context.retrieved_premises = combined
        context.metadata["retrieval_round"] = attempt
        context.metadata["retrieval_goal_state"] = goal_state
        context.metadata["retrieval_budget"] = selection.budget
        context.metadata["retrieval_stats"] = selection.stats
        context.metadata["retrieval_combined_context_chars"] = context_chars
        return context

    def close(self) -> None:
        if hasattr(self, "retrieval_memory"):
            self.retrieval_memory.close()
        super().close()
