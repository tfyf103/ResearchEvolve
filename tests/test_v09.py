from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from research_evolve.formal import FormalizationSpec
from research_evolve.formal_agents import FormalContext
from research_evolve.formal_search import (
    FrozenLeanProofWorker,
    InteractiveProofSearchBudget,
    LeanGoal,
    LeanProofState,
    ProofSearchExhausted,
    ProofSearchFormalizer,
    TacticCandidate,
    TacticTransition,
)


def _spec(spec_id: str = "formal-spec-v09") -> FormalizationSpec:
    return FormalizationSpec(
        proof_spec_id="proof-spec-1", proof_artifact_id="proof-artifact-1", conjecture_id="conjecture-1",
        conjecture_statement="P implies P.", theorem_name="same",
        theorem_signature="theorem same (P : Prop) (h : P) : P", imports=["Mathlib"],
        toolchain="leanprover/lean4:v4.30.0", id=spec_id,
    )


def _context(spec: FormalizationSpec) -> FormalContext:
    return FormalContext("test", 1, spec.to_dict(), {}, {}, {}, {}, metadata={})


class FakeWorker:
    name = "fake-worker-v1"

    def __init__(self, transitions: dict[tuple[str, str], list[str] | None], initial: list[str] | None = None) -> None:
        self.transitions = transitions
        self.initial = initial if initial is not None else ["P"]
        self.initial_calls = 0
        self.calls: list[tuple[str, str]] = []

    def session(self, workspace: str | Path) -> Any:
        return nullcontext()

    def initial_state(self, spec: FormalizationSpec) -> TacticTransition:
        self.initial_calls += 1
        return TacticTransition("succeeded", LeanProofState(spec.id, [LeanGoal(goal) for goal in self.initial]))

    def apply_tactic(self, spec: FormalizationSpec, state: LeanProofState, candidate: TacticCandidate) -> TacticTransition:
        key = (state.goals[0].target, candidate.tactic)
        self.calls.append(key)
        if key not in self.transitions:
            return TacticTransition("failed", diagnostics="unknown tactic")
        targets = self.transitions[key]
        if targets is None:
            return TacticTransition("failed", diagnostics="Lean rejected tactic")
        child = LeanProofState(
            spec.id, [LeanGoal(goal) for goal in targets], [*state.tactic_history, candidate.tactic],
            state.depth + 1, state.state_id,
        )
        return TacticTransition("succeeded", child)


class FakeGenerator:
    name = "fake-generator-v1"

    def __init__(self, candidates: dict[str, list[tuple[str, float]]]) -> None:
        self.candidates = candidates

    def generate(self, context: FormalContext, spec: FormalizationSpec, state: LeanProofState,
                 retrieved_premises: list[dict[str, Any]], limit: int) -> list[TacticCandidate]:
        return [TacticCandidate(tactic, confidence) for tactic, confidence in self.candidates.get(state.goals[0].target, [])][:limit]


def test_best_first_search_isolates_failures_and_reconstructs_multi_step_proof(tmp_path: Path) -> None:
    spec = _spec()
    worker = FakeWorker({("P", "bad"): None, ("P", "intro-step"): ["Q", "R"], ("Q", "finish"): []})
    generator = FakeGenerator({"P": [("bad", 0.9), ("intro-step", 0.8)], "Q": [("finish", 1.0)]})
    formalizer = ProofSearchFormalizer(tmp_path, worker, generator)
    artifact = formalizer.formalize(_context(spec), spec)
    formalizer.close()

    assert artifact.proof_term == "by\n  intro-step\n  finish"
    assert artifact.helper_source == ""
    assert artifact.metadata["proof_search"]["tactic_failures"] == 1
    assert worker.calls == [("P", "bad"), ("P", "intro-step"), ("Q", "finish")]


def test_semantic_state_dedup_prevents_cycles(tmp_path: Path) -> None:
    spec = _spec()
    worker = FakeWorker({("P", "loop-a"): ["P"], ("P", "loop-b"): ["P"]})
    generator = FakeGenerator({"P": [("loop-a", 0.9), ("loop-b", 0.8)]})
    formalizer = ProofSearchFormalizer(tmp_path, worker, generator)
    with pytest.raises(ProofSearchExhausted) as captured:
        formalizer.formalize(_context(spec), spec)
    formalizer.close()
    assert captured.value.summary.duplicate_states == 2
    assert captured.value.summary.reason == "proof-search frontier exhausted"


def test_budget_exhaustion_is_distinct_and_checkpointed(tmp_path: Path) -> None:
    spec = _spec()
    worker = FakeWorker({("P", "next"): ["Q"]})
    generator = FakeGenerator({"P": [("next", 1.0)], "Q": [("finish", 1.0)]})
    budget = InteractiveProofSearchBudget(max_states=2, max_depth=4, max_tactics_per_state=2, max_lean_calls=8,
                                          max_model_calls=8, max_retrieval_calls=8, max_wall_seconds=30, beam_width=4)
    formalizer = ProofSearchFormalizer(tmp_path, worker, generator, budget=budget)
    with pytest.raises(ProofSearchExhausted) as captured:
        formalizer.formalize(_context(spec), spec)
    assert captured.value.summary.status == "search_exhausted"
    assert captured.value.summary.reason == "max_states exhausted"
    row = formalizer.memory.conn.execute("SELECT status FROM search_runs").fetchone()
    formalizer.close()
    assert row["status"] == "search_exhausted"


def test_resume_loads_exact_active_frontier_without_reinitializing(tmp_path: Path) -> None:
    spec = _spec()
    worker = FakeWorker({("Q", "finish"): []})
    generator = FakeGenerator({"Q": [("finish", 1.0)]})
    first = ProofSearchFormalizer(tmp_path, worker, generator, resume=True)
    summary, _ = first.memory.start(spec, first._config_fingerprint(spec), False)
    resumed_state = LeanProofState(spec.id, [LeanGoal("Q")], ["intro-step"], 1)
    first.memory.add_state(summary.run_id, resumed_state, "frontier", -1.0, 1)
    first.memory.save_summary(summary)
    first.close()

    resumed_spec = _spec("formal-spec-v09-recreated")
    second = ProofSearchFormalizer(tmp_path, worker, generator, resume=True)
    artifact = second.formalize(_context(resumed_spec), resumed_spec)
    second.close()
    assert worker.initial_calls == 0
    assert artifact.proof_term == "by\n  intro-step\n  finish"
    assert artifact.formal_spec_id == resumed_spec.id


@pytest.mark.parametrize("tactic", ["sorry", "run_tac doSomething", "theorem escape : True := by trivial", "simp\nexact h"])
def test_candidate_gate_rejects_boundary_crossing_tactics(tactic: str) -> None:
    with pytest.raises(ValueError):
        TacticCandidate(tactic, 0.5).validate()


def test_worker_refuses_false_closed_state_and_keeps_instrumentation_out_of_artifact(tmp_path: Path) -> None:
    spec = _spec()
    worker = FakeWorker({("P", "exact h"): []})
    formalizer = ProofSearchFormalizer(tmp_path, worker, FakeGenerator({"P": [("exact h", 1.0)]}))
    artifact = formalizer.formalize(_context(spec), spec)
    formalizer.close()
    assert "sorry" not in artifact.proof_term
    assert "all_goals" not in artifact.proof_term

    malformed = "SearchProbe.lean:1:1: warning: declaration uses 'sorry'"
    assert FrozenLeanProofWorker._parse_goals(malformed) == []


def test_lean_trace_parser_preserves_multiple_goals_and_local_context() -> None:
    output = (
        "SearchProbe.lean:8:3: info: case left\nP : Prop\nh : P\n⊢ P\n"
        "SearchProbe.lean:8:3: info: case right\nQ : Prop\n⊢ Q\n"
    )
    goals = FrozenLeanProofWorker._parse_goals(output)
    assert [(goal.case_name, goal.local_context, goal.target) for goal in goals] == [
        ("left", ("P : Prop", "h : P"), "P"),
        ("right", ("Q : Prop",), "Q"),
    ]

