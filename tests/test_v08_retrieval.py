from __future__ import annotations

from pathlib import Path

from research_evolve.formal_project import LeanProjectLock
from research_evolve.formal_retrieval import PremiseIndex, PremiseSelector, ProofSearchBudget
from research_evolve.formal_retrieval_pipeline import RetrievalFormalPipeline
from research_evolve.formal import FormalArtifact, FormalizationSpec, KernelResult, LeanDiagnostic
from research_evolve.formal_agents import FormalContext


TOOLCHAIN = "leanprover/lean4:v4.30.0"


def _project(tmp_path: Path) -> tuple[Path, LeanProjectLock, PremiseIndex]:
    root = tmp_path / "project"
    (root / "Demo").mkdir(parents=True)
    (root / "lean-toolchain").write_text(TOOLCHAIN + "\n", encoding="utf-8")
    (root / "lakefile.toml").write_text(
        'name = "demo"\nversion = "0.1.0"\n\n[[lean_lib]]\nname = "Demo"\n', encoding="utf-8"
    )
    (root / "Demo.lean").write_text("import Demo.Premises\n", encoding="utf-8")
    (root / "Demo" / "Premises.lean").write_text(
        """namespace Demo

theorem zero_le (n : Nat) : 0 ≤ n := Nat.zero_le n

theorem distance_nonnegative (d : Nat) : 0 ≤ d := by
  exact zero_le d

theorem unrelated (p : Prop) : p → p := by
  intro h
  exact h

end Demo
""",
        encoding="utf-8",
    )
    lock = LeanProjectLock.capture(root)
    return root, lock, PremiseIndex.build_from_project(root, lock)


def test_v08_index_has_type_features_and_dependency_graph(tmp_path: Path) -> None:
    _, lock, index = _project(tmp_path)
    assert index.schema_version == 2
    assert index.project_fingerprint == lock.fingerprint
    by_name = {premise.name: premise for premise in index.premises}
    assert {"nat", "le"}.issubset(set(by_name["Demo.distance_nonnegative"].type_features))
    assert by_name["Demo.distance_nonnegative"].dependencies == ("Demo.zero_le",)

    path = tmp_path / "index.json"
    index.write(path)
    loaded = PremiseIndex.read(path)
    assert loaded.fingerprint == index.fingerprint
    assert loaded.premises == index.premises


def test_goal_conditioning_and_dependency_expansion_are_budgeted(tmp_path: Path) -> None:
    _, _, index = _project(tmp_path)
    selector = PremiseSelector(
        index,
        limit=3,
        budget=ProofSearchBudget(max_candidates=50, max_results=3, max_dependency_expansions=1, max_context_chars=10000),
    )
    selection = selector.select(
        formal_spec_id="spec",
        query="distance nonnegative",
        goal_state="distance target",
        allowed_modules=["Demo.Premises"],
    )
    selected = {item.premise.name: item for item in selection.selected}
    assert "Demo.distance_nonnegative" in selected
    assert "Demo.zero_le" in selected
    assert selected["Demo.zero_le"].retrieval_depth == 1
    assert selection.stats["dependency_expansions"] == 1
    assert selection.stats["candidates_scanned"] <= 50


def test_goal_type_can_retrieve_without_lexical_query_overlap(tmp_path: Path) -> None:
    _, _, index = _project(tmp_path)
    selector = PremiseSelector(index, limit=3)
    selection = selector.select(
        formal_spec_id="spec",
        query="prove target",
        goal_state="⊢ 0 ≤ d",
        allowed_modules=["Demo.Premises"],
    )
    names = {item.premise.name for item in selection.selected}
    assert "Demo.zero_le" in names or "Demo.distance_nonnegative" in names


def test_empty_allowlist_and_context_budget_remain_fail_closed(tmp_path: Path) -> None:
    _, _, index = _project(tmp_path)
    empty = PremiseSelector(index, limit=3).select(
        formal_spec_id="spec", query="distance", goal_state="⊢ 0 ≤ d", allowed_modules=[]
    )
    assert empty.selected == []

    tiny = PremiseSelector(
        index,
        limit=3,
        budget=ProofSearchBudget(max_candidates=50, max_results=3, max_dependency_expansions=1, max_context_chars=1),
    ).select(formal_spec_id="spec", query="distance", goal_state="⊢ 0 ≤ d", allowed_modules=["Demo.Premises"])
    assert tiny.selected == []
    assert tiny.stats["context_budget_exhausted"] is True


def test_repair_round_excludes_already_seen_premises(tmp_path: Path) -> None:
    _, _, index = _project(tmp_path)
    selector = PremiseSelector(index, limit=2)
    first = selector.select(
        formal_spec_id="spec", query="distance nonnegative", goal_state="⊢ 0 ≤ d",
        allowed_modules=["Demo.Premises"],
    )
    second = selector.select(
        formal_spec_id="spec", query="type mismatch Nat", goal_state="⊢ 0 ≤ d",
        allowed_modules=["Demo.Premises"], round=1,
        excluded_names=[item.premise.name for item in first.selected],
    )
    assert second.round == 1
    assert not ({item.premise.name for item in first.selected} & {item.premise.name for item in second.selected})


def test_repair_context_retrieves_from_kernel_diagnostics_and_records_round(tmp_path: Path) -> None:
    _, _, index = _project(tmp_path)
    selector = PremiseSelector(index, limit=1)
    pipeline = object.__new__(RetrievalFormalPipeline)
    pipeline.premise_selector = selector
    recorded = []
    pipeline.retrieval_memory = type("Memory", (), {"record": lambda self, selection: recorded.append(selection)})()
    pipeline._record_selection_graph = lambda selection: None

    spec = FormalizationSpec(
        proof_spec_id="proof", proof_artifact_id="artifact", conjecture_id="conjecture",
        conjecture_statement="statement", theorem_name="target",
        theorem_signature="theorem target (d : Nat) : 0 ≤ d", imports=["Demo.Premises"],
    )
    context = FormalContext(
        problem="problem", generation=1, formal_spec=spec.to_dict(), proof_spec={}, proof_artifact={},
        proof_review={}, conjecture={}, retrieved_premises=[{"name": "Demo.unrelated"}],
    )
    result = KernelResult(
        formal_artifact_id="artifact", passed=False, status="kernel_rejected", command=["lean"],
        expected_toolchain=TOOLCHAIN, detected_version="4.30.0", exit_code=1,
        diagnostics=[LeanDiagnostic(severity="error", message="need distance_nonnegative for Nat goal")],
    )

    updated = pipeline._prepare_repair_context(context, spec, result, 1)

    assert recorded[0].round == 1
    assert "distance_nonnegative" in recorded[0].query
    assert recorded[0].selected[0].premise.name == "Demo.distance_nonnegative"
    assert updated.metadata["retrieval_round"] == 1
    assert updated.retrieved_premises[0]["name"] == "Demo.distance_nonnegative"


def test_repair_context_keeps_previous_useful_premise_within_total_budget(tmp_path: Path) -> None:
    _, _, index = _project(tmp_path)
    selector = PremiseSelector(index, limit=2)
    pipeline = object.__new__(RetrievalFormalPipeline)
    pipeline.premise_selector = selector
    pipeline.retrieval_memory = type("Memory", (), {"record": lambda self, selection: None})()
    pipeline._record_selection_graph = lambda selection: None
    spec = FormalizationSpec(
        proof_spec_id="proof", proof_artifact_id="artifact", conjecture_id="conjecture",
        conjecture_statement="statement", theorem_name="target",
        theorem_signature="theorem target (d : Nat) : 0 ≤ d", imports=["Demo.Premises"],
    )
    previous = next(item for item in index.premises if item.name == "Demo.distance_nonnegative").to_dict()
    context = FormalContext(
        problem="problem", generation=1, formal_spec=spec.to_dict(), proof_spec={}, proof_artifact={},
        proof_review={}, conjecture={}, retrieved_premises=[previous],
    )
    result = KernelResult(
        formal_artifact_id="artifact", passed=False, status="kernel_rejected", command=["lean"],
        expected_toolchain=TOOLCHAIN, detected_version="4.30.0", exit_code=1,
        diagnostics=[LeanDiagnostic(severity="error", message="try unrelated implication")],
    )

    updated = pipeline._prepare_repair_context(context, spec, result, 1)

    names = {item["name"] for item in updated.retrieved_premises}
    assert "Demo.distance_nonnegative" in names
    assert len(updated.retrieved_premises) <= selector.budget.max_results
    assert updated.metadata["retrieval_combined_context_chars"] <= selector.budget.max_context_chars


def test_incremental_repair_journal_may_be_empty_when_prior_round_already_found_everything(tmp_path: Path) -> None:
    _, _, index = _project(tmp_path)
    selector = PremiseSelector(index, limit=4)
    pipeline = object.__new__(RetrievalFormalPipeline)
    pipeline.premise_selector = selector
    recorded = []
    pipeline.retrieval_memory = type("Memory", (), {"record": lambda self, selection: recorded.append(selection)})()
    pipeline._record_selection_graph = lambda selection: None
    spec = FormalizationSpec(
        proof_spec_id="proof", proof_artifact_id="artifact", conjecture_id="conjecture",
        conjecture_statement="statement", theorem_name="target",
        theorem_signature="theorem target (d : Nat) : 0 ≤ d", imports=["Demo.Premises"],
    )
    previous = [item.to_dict() for item in index.premises]
    context = FormalContext(
        problem="problem", generation=1, formal_spec=spec.to_dict(), proof_spec={}, proof_artifact={},
        proof_review={}, conjecture={}, retrieved_premises=previous,
    )
    result = KernelResult(
        formal_artifact_id="artifact", passed=False, status="kernel_rejected", command=["lean"],
        expected_toolchain=TOOLCHAIN, detected_version="4.30.0", exit_code=1,
        diagnostics=[LeanDiagnostic(severity="error", message="Nat goal remains")],
    )

    updated = pipeline._prepare_repair_context(context, spec, result, 1)

    assert recorded[0].round == 1
    assert recorded[0].selected == []
    assert {item["name"] for item in updated.retrieved_premises} == {item.name for item in index.premises}
