from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_evolve.candidates import Candidate, CandidateDB
from research_evolve.conjectures import Conjecture, ConjectureMemory, Predicate, ValueRef
from research_evolve.graph import ResearchGraph, ResearchNode
from research_evolve.proof_agents import (
    CommandProofVerifier,
    CommandProver,
    ProofContext,
)
from research_evolve.proof_pipeline import ProofPipeline
from research_evolve.proofs import (
    LemmaSpec,
    ProofArtifact,
    ProofPlan,
    ProofReview,
    ProofSpec,
    VerificationIssue,
)


def _prepare_workspace(tmp_path: Path, *, add_hidden_counterexample: bool = False) -> tuple[Path, str]:
    workspace = tmp_path / "run"
    workspace.mkdir()
    (workspace / "manifest.json").write_text(
        json.dumps(
            {
                "fingerprint": "source-run-fingerprint",
                "inputs": {
                    "spec": {
                        "problem": "Show a synthetic distance metric is non-negative.",
                        "domain": "generic",
                        "constraints": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (workspace / "summary.json").write_text(json.dumps({"generation_completed": 1}), encoding="utf-8")

    db = CandidateDB(workspace / "candidates.sqlite3")
    c1 = Candidate(payload={"x": 1}, generation=1, valid=True, score=-1.0, metrics={"distance": 1.0})
    c2 = Candidate(payload={"x": 2}, generation=1, valid=True, score=-2.0, metrics={"distance": 2.0})
    db.upsert(c1)
    db.upsert(c2)
    if add_hidden_counterexample:
        db.upsert(Candidate(payload={"x": 99}, generation=1, valid=True, score=0.0, metrics={"distance": -1.0}))
    db.close()

    memory = ConjectureMemory(workspace / "conjectures.sqlite3")
    conjecture = Conjecture(
        statement="Synthetic distance is always non-negative.",
        predicate=Predicate(left=ValueRef("metrics", "distance"), operator="ge", right_constant=0),
        evidence_candidate_ids=[c1.id, c2.id],
        rationale="Observed non-negative distances.",
        confidence=0.9,
    )
    memory.record_conjecture(conjecture, generation=1)
    memory.record_test(conjecture.id, c1.id, 1, True, "test")
    memory.record_test(conjecture.id, c2.id, 1, True, "test")
    assert memory.refresh_status(conjecture.id, min_evidence=2) == "empirically_supported"
    memory.close()

    graph = ResearchGraph(workspace / "research_graph.sqlite3")
    graph.add_node(ResearchNode(id=conjecture.id, type="conjecture", statement=conjecture.statement, status="empirically_supported", payload={"generation": 1}))
    graph.close()
    return workspace, conjecture.id


class DemoPlanner:
    name = "planner-v1"

    def plan(self, context: ProofContext, proof_spec: ProofSpec) -> ProofPlan:
        return ProofPlan(
            proof_spec_id=proof_spec.id,
            strategy="Reduce the target to a basic non-negativity lemma.",
            lemmas=[LemmaSpec(label="nonnegative", statement="distance >= 0")],
        )


class DemoProver:
    name = "prover-v1"

    def prove(self, context: ProofContext, proof_spec: ProofSpec, plan: ProofPlan) -> ProofArtifact:
        return ProofArtifact(
            proof_spec_id=proof_spec.id,
            proof_plan_id=plan.id,
            lemma_arguments={"nonnegative": "By definition this synthetic distance is non-negative."},
            final_argument="The lemma establishes exactly the target statement.",
        )


class HiddenAssumptionProver(DemoProver):
    name = "prover-hidden-assumption"

    def prove(self, context: ProofContext, proof_spec: ProofSpec, plan: ProofPlan) -> ProofArtifact:
        artifact = super().prove(context, proof_spec, plan)
        artifact.assumptions_used = ["an unstated compactness assumption"]
        return artifact


class DemoVerifier:
    name = "verifier-v1"

    def verify(self, context: ProofContext, proof_spec: ProofSpec, plan: ProofPlan, artifact: ProofArtifact) -> ProofReview:
        return ProofReview(
            proof_artifact_id=artifact.id,
            verifier=self.name,
            decision="verified",
            confidence=0.95,
            adversarial_notes="Checked target integrity and lemma coverage.",
        )


class SameIdentityVerifier(DemoVerifier):
    name = "prover-v1"


class ErrorReportingVerifier(DemoVerifier):
    name = "verifier-error"

    def verify(self, context: ProofContext, proof_spec: ProofSpec, plan: ProofPlan, artifact: ProofArtifact) -> ProofReview:
        return ProofReview(
            proof_artifact_id=artifact.id,
            verifier=self.name,
            decision="verified",
            confidence=0.99,
            issues=[VerificationIssue("error", "gap", "A logical gap remains.")],
        )


def test_proof_plan_rejects_cycles() -> None:
    plan = ProofPlan(
        proof_spec_id="spec",
        strategy="cycle",
        lemmas=[
            LemmaSpec(label="a", statement="A", depends_on=["b"]),
            LemmaSpec(label="b", statement="B", depends_on=["a"]),
        ],
    )
    with pytest.raises(ValueError, match="cycle"):
        plan.validate()


def test_artifact_requires_every_planned_lemma() -> None:
    plan = ProofPlan(
        proof_spec_id="spec",
        strategy="two steps",
        lemmas=[LemmaSpec(label="a", statement="A"), LemmaSpec(label="b", statement="B")],
    )
    artifact = ProofArtifact(
        proof_spec_id="spec",
        proof_plan_id=plan.id,
        lemma_arguments={"a": "proof A"},
        final_argument="finish",
    )
    with pytest.raises(ValueError, match="missing lemma"):
        artifact.validate(plan)


def test_verifier_gate_downgrades_claimed_verification_with_error() -> None:
    review = ProofReview(
        proof_artifact_id="artifact",
        verifier="independent",
        decision="verified",
        confidence=0.99,
        issues=[VerificationIssue("error", "hidden_assumption", "Unjustified assumption")],
    )
    assert review.gated_status(0.7) == "rejected"


def test_verifier_gate_requires_confidence_threshold() -> None:
    review = ProofReview(
        proof_artifact_id="artifact",
        verifier="independent",
        decision="verified",
        confidence=0.5,
    )
    assert review.gated_status(0.7) == "inconclusive"


def test_proof_pipeline_verifies_supported_conjecture(tmp_path: Path) -> None:
    workspace, conjecture_id = _prepare_workspace(tmp_path)
    with ProofPipeline(workspace, DemoPlanner(), DemoProver(), DemoVerifier()) as pipeline:
        summary = pipeline.run()
    assert summary.considered_conjectures == 1
    assert summary.attempted_proofs == 1
    assert summary.verified_natural_language == 1
    assert (workspace / "proofs.sqlite3").exists()
    assert (workspace / "proof_manifest.json").exists()

    from research_evolve.proofs import ProofMemory

    memory = ProofMemory(workspace / "proofs.sqlite3")
    specs = memory.list_specs(10)
    reviews = memory.list_reviews(10)
    memory.close()
    assert specs[0]["conjecture_id"] == conjecture_id
    assert specs[0]["status"] == "verified_natural_language"
    assert reviews[0]["gated_status"] == "verified_natural_language"

    graph = ResearchGraph(workspace / "research_graph.sqlite3")
    exported = graph.export()
    graph.close()
    node_types = {node["type"] for node in exported["nodes"]}
    relations = {edge["relation"] for edge in exported["edges"]}
    assert {"proof_spec", "proof_plan", "lemma", "proof_artifact", "proof_review"}.issubset(node_types)
    assert {"targets_conjecture", "decomposes_into", "claims_proof_of", "reviews"}.issubset(relations)


def test_proof_preflight_refutes_before_prover_runs(tmp_path: Path) -> None:
    workspace, conjecture_id = _prepare_workspace(tmp_path, add_hidden_counterexample=True)
    with ProofPipeline(workspace, DemoPlanner(), DemoProver(), DemoVerifier()) as pipeline:
        summary = pipeline.run()
    assert summary.refuted_before_proof == 1
    assert summary.attempted_proofs == 0

    memory = ConjectureMemory(workspace / "conjectures.sqlite3")
    record = next(item for item in memory.recent_conjectures(10) if item["id"] == conjecture_id)
    counterexamples = memory.list_counterexamples(10)
    memory.close()
    assert record["status"] == "refuted"
    assert counterexamples[0]["source"] == "proof_preflight"


def test_hidden_assumption_invalidates_artifact_before_verifier(tmp_path: Path) -> None:
    workspace, _ = _prepare_workspace(tmp_path)
    with ProofPipeline(workspace, DemoPlanner(), HiddenAssumptionProver(), DemoVerifier()) as pipeline:
        summary = pipeline.run()
    assert summary.invalid == 1
    assert summary.verified_natural_language == 0


def test_prover_and_verifier_must_be_independent(tmp_path: Path) -> None:
    workspace, _ = _prepare_workspace(tmp_path)
    with pytest.raises(ValueError, match="independent"):
        ProofPipeline(workspace, DemoPlanner(), DemoProver(), SameIdentityVerifier())


def test_command_actor_identity_is_role_independent(tmp_path: Path) -> None:
    script = tmp_path / "same.py"
    script.write_text("import json, sys\njson.load(sys.stdin)\nprint('{}')\n", encoding="utf-8")
    prover = CommandProver(["python", str(script)])
    verifier = CommandProofVerifier(["python", str(script)])
    assert prover.name == verifier.name


def test_error_reporting_verifier_is_deterministically_rejected(tmp_path: Path) -> None:
    workspace, _ = _prepare_workspace(tmp_path)
    with ProofPipeline(workspace, DemoPlanner(), DemoProver(), ErrorReportingVerifier()) as pipeline:
        summary = pipeline.run()
    assert summary.rejected == 1
    assert summary.verified_natural_language == 0
