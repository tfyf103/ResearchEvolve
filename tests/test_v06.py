from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_evolve.candidates import Candidate, CandidateDB
from research_evolve.conjectures import Conjecture, ConjectureMemory, Predicate, ValueRef
from research_evolve.formal import FormalArtifact, FormalMemory, FormalizationSpec
from research_evolve.formal_agents import FormalContext
from research_evolve.formal_pipeline import FormalPipeline
from research_evolve.graph import ResearchGraph, ResearchNode
from research_evolve.lean_kernel import LeanKernel
from research_evolve.proofs import LemmaSpec, ProofArtifact, ProofMemory, ProofPlan, ProofReview, ProofSpec


CONJECTURE_STATEMENT = "Synthetic distance is always non-negative."
EXPECTED_TOOLCHAIN = "leanprover/lean4:v4.30.0"


def _write_fake_lean(path: Path, version: str = "4.30.0") -> None:
    path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import os, pathlib, sys",
                f"if os.environ.get('ELAN_TOOLCHAIN') != {EXPECTED_TOOLCHAIN!r}:",
                "    print('missing frozen ELAN_TOOLCHAIN', file=sys.stderr)",
                "    raise SystemExit(2)",
                "if '--version' in sys.argv:",
                f"    print('Lean (version {version}, fake-test-kernel)')",
                "    raise SystemExit(0)",
                "source = pathlib.Path(sys.argv[-1]).read_text(encoding='utf-8')",
                "if 'Nat.zero_le' in source:",
                "    print(\"'metric_nonnegative' does not depend on any axioms\")",
                "    raise SystemExit(0)",
                "print(f'{sys.argv[-1]}:1:0: error: synthetic type mismatch', file=sys.stderr)",
                "raise SystemExit(1)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _prepare_workspace(
    tmp_path: Path,
    *,
    include_contract: bool = True,
    preamble: str = "",
    contract_predicate_operator: str = "ge",
) -> tuple[Path, str, str]:
    workspace = tmp_path / "run"
    workspace.mkdir()
    contract = {
        "conjecture_statement": CONJECTURE_STATEMENT,
        "conjecture_predicate": {
            "left": {"source": "metrics", "key": "distance"},
            "operator": contract_predicate_operator,
            "right_constant": 0,
        },
        "backend": "lean4",
        "toolchain": EXPECTED_TOOLCHAIN,
        "theorem_name": "metric_nonnegative",
        "theorem_signature": "theorem metric_nonnegative (d : Nat) : 0 ≤ d",
        "imports": [],
        "preamble": preamble,
        "metadata": {"test": True},
    }
    metadata = {"formal_contracts": [contract] if include_contract else []}
    (workspace / "manifest.json").write_text(
        json.dumps(
            {
                "fingerprint": "source-run-fingerprint",
                "inputs": {
                    "spec": {
                        "problem": "Show a synthetic distance metric is non-negative.",
                        "domain": "generic",
                        "constraints": [],
                        "conjecture": {"min_evidence": 2},
                        "metadata": metadata,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (workspace / "summary.json").write_text(json.dumps({"generation_completed": 1}), encoding="utf-8")
    (workspace / "checkpoint.json").write_text(json.dumps({"generation": 1}), encoding="utf-8")
    (workspace / "proof_manifest.json").write_text(
        json.dumps(
            {
                "fingerprint": "proof-manifest-fingerprint",
                "inputs": {
                    "source_run_fingerprint": "source-run-fingerprint",
                    "source_generation": 1,
                },
            }
        ),
        encoding="utf-8",
    )

    db = CandidateDB(workspace / "candidates.sqlite3")
    c1 = Candidate(payload={"x": 1}, generation=1, valid=True, score=-1.0, metrics={"distance": 1.0})
    c2 = Candidate(payload={"x": 2}, generation=1, valid=True, score=-2.0, metrics={"distance": 2.0})
    db.upsert(c1)
    db.upsert(c2)
    db.close()

    conjectures = ConjectureMemory(workspace / "conjectures.sqlite3")
    conjecture = Conjecture(
        statement=CONJECTURE_STATEMENT,
        predicate=Predicate(left=ValueRef("metrics", "distance"), operator="ge", right_constant=0),
        evidence_candidate_ids=[c1.id, c2.id],
        rationale="Observed non-negative values.",
        confidence=0.9,
    )
    conjectures.record_conjecture(conjecture, generation=1)
    conjectures.record_test(conjecture.id, c1.id, 1, True, "test")
    conjectures.record_test(conjecture.id, c2.id, 1, True, "test")
    assert conjectures.refresh_status(conjecture.id, min_evidence=2) == "empirically_supported"
    conjectures.close()

    proofs = ProofMemory(workspace / "proofs.sqlite3")
    proof_spec = ProofSpec.from_conjecture(conjecture, generation=1)
    plan = ProofPlan(
        proof_spec_id=proof_spec.id,
        strategy="Use the definition of Nat order.",
        lemmas=[LemmaSpec(label="nonnegative", statement="0 ≤ d")],
    )
    artifact = ProofArtifact(
        proof_spec_id=proof_spec.id,
        proof_plan_id=plan.id,
        lemma_arguments={"nonnegative": "Nat values are non-negative."},
        final_argument="Therefore the target holds.",
    )
    review = ProofReview(
        proof_artifact_id=artifact.id,
        verifier="independent-test-verifier",
        decision="verified",
        confidence=0.99,
    )
    proofs.record_spec(proof_spec)
    proofs.record_plan(plan, generation=1)
    proofs.record_artifact(artifact, generation=1)
    assert proofs.record_review(review, generation=1, min_confidence=0.7) == "verified_natural_language"
    proofs.close()

    graph = ResearchGraph(workspace / "research_graph.sqlite3")
    graph.add_node(
        ResearchNode(
            id=conjecture.id,
            type="conjecture",
            statement=conjecture.statement,
            status="empirically_supported",
            payload={"generation": 1},
        )
    )
    graph.add_node(
        ResearchNode(
            id=proof_spec.id,
            type="proof_spec",
            statement=proof_spec.statement,
            status="verified_natural_language",
            payload={**proof_spec.to_dict(), "generation": 1},
        )
    )
    graph.close()
    return workspace, conjecture.id, proof_spec.id


class BadThenRepairFormalizer:
    name = "formalizer-test-v1"

    def formalize(self, context: FormalContext, spec: FormalizationSpec) -> FormalArtifact:
        return FormalArtifact(formal_spec_id=spec.id, proof_term="by exact 0")


class Repairer:
    name = "repairer-test-v1"

    def repair(self, context, spec, artifact, kernel_result, attempt):
        assert not kernel_result.passed
        return FormalArtifact(
            formal_spec_id=spec.id,
            proof_term="by exact Nat.zero_le _",
            attempt=attempt,
            parent_artifact_id=artifact.id,
        )


class GoodFormalizer:
    name = "formalizer-good-v1"

    def formalize(self, context: FormalContext, spec: FormalizationSpec) -> FormalArtifact:
        return FormalArtifact(formal_spec_id=spec.id, proof_term="by exact Nat.zero_le _")


def _spec(**overrides) -> FormalizationSpec:
    data = {
        "proof_spec_id": "proof-spec",
        "proof_artifact_id": "proof-artifact",
        "conjecture_id": "conjecture",
        "conjecture_statement": "statement",
        "theorem_name": "metric_nonnegative",
        "theorem_signature": "theorem metric_nonnegative (d : Nat) : 0 ≤ d",
        "toolchain": EXPECTED_TOOLCHAIN,
    }
    data.update(overrides)
    return FormalizationSpec(**data)


def test_formal_spec_rejects_actor_owned_proof_body() -> None:
    spec = _spec(theorem_signature="theorem metric_nonnegative (d : Nat) : 0 ≤ d := by exact Nat.zero_le _")
    with pytest.raises(ValueError, match="declaration head"):
        spec.validate()


def test_kernel_rejects_sorry_without_running_lean(tmp_path: Path) -> None:
    spec = _spec()
    artifact = FormalArtifact(formal_spec_id=spec.id, proof_term="by sorry")
    result, _ = LeanKernel("definitely-not-a-real-lean-command").verify(spec, artifact, workspace=tmp_path)
    assert result.status == "kernel_rejected"
    assert result.gate_reason == "forbidden-token:sorry"


def test_kernel_rejects_untrusted_top_level_helper(tmp_path: Path) -> None:
    spec = _spec()
    artifact = FormalArtifact(
        formal_spec_id=spec.id,
        proof_term="by exact Nat.zero_le _",
        helper_source="theorem helper : True := by trivial",
    )
    result, _ = LeanKernel("definitely-not-a-real-lean-command").verify(spec, artifact, workspace=tmp_path)
    assert result.status == "kernel_rejected"
    assert result.gate_reason == "untrusted-top-level-helper"


def test_kernel_requires_frozen_toolchain_version(tmp_path: Path) -> None:
    fake = tmp_path / "fake_lean.py"
    _write_fake_lean(fake, version="4.29.0")
    spec = _spec()
    artifact = FormalArtifact(formal_spec_id=spec.id, proof_term="by exact Nat.zero_le _")
    result, _ = LeanKernel(["python", str(fake)]).verify(spec, artifact, workspace=tmp_path)
    assert result.status == "environment_error"
    assert result.gate_reason == "toolchain-version-mismatch"


def test_kernel_forces_toolchain_env_in_temp_workdir(tmp_path: Path) -> None:
    fake = tmp_path / "fake_lean.py"
    _write_fake_lean(fake)
    spec = _spec()
    artifact = FormalArtifact(formal_spec_id=spec.id, proof_term="by exact Nat.zero_le _")
    result, _ = LeanKernel(["python", str(fake)]).verify(spec, artifact, workspace=tmp_path)
    assert result.status == "formal_verified"


def test_formal_pipeline_repairs_then_kernel_verifies(tmp_path: Path) -> None:
    workspace, _, proof_spec_id = _prepare_workspace(tmp_path)
    fake = tmp_path / "fake_lean.py"
    _write_fake_lean(fake)
    kernel = LeanKernel(["python", str(fake)])
    with FormalPipeline(workspace, BadThenRepairFormalizer(), kernel, Repairer(), max_repairs=2) as pipeline:
        summary = pipeline.run()
    assert summary.attempted_formalizations == 1
    assert summary.formal_verified == 1

    memory = FormalMemory(workspace / "formal.sqlite3")
    specs = memory.list_specs(10)
    artifacts = memory.list_artifacts(10)
    runs = memory.list_kernel_runs(10)
    memory.close()
    assert specs[0]["proof_spec_id"] == proof_spec_id
    assert specs[0]["status"] == "formal_verified"
    assert specs[0]["metadata"]["frozen_conjecture_predicate"]["operator"] == "ge"
    assert len(artifacts) == 2
    assert any(item["status"] == "formal_verified" for item in artifacts)
    assert len(runs) == 2
    assert any(item["passed"] for item in runs)
    assert (workspace / "formal_manifest.json").exists()
    assert (workspace / "formal_summary.json").exists()
    assert len(list((workspace / "formal_sources").glob("*.lean"))) == 2

    graph = ResearchGraph(workspace / "research_graph.sqlite3")
    exported = graph.export()
    graph.close()
    node_types = {node["type"] for node in exported["nodes"]}
    relations = {edge["relation"] for edge in exported["edges"]}
    assert {"formalization_spec", "formal_artifact", "lean_kernel_result"}.issubset(node_types)
    assert {"formalizes_proof", "checks_formal_artifact", "formally_verifies"}.issubset(relations)


def test_frozen_preamble_is_carried_into_formal_source(tmp_path: Path) -> None:
    workspace, _, _ = _prepare_workspace(tmp_path, preamble="def frozenDistance (d : Nat) : Nat := d")
    fake = tmp_path / "fake_lean.py"
    _write_fake_lean(fake)
    with FormalPipeline(workspace, GoodFormalizer(), LeanKernel(["python", str(fake)]), max_repairs=0) as pipeline:
        summary = pipeline.run()
    assert summary.formal_verified == 1
    memory = FormalMemory(workspace / "formal.sqlite3")
    artifacts = memory.list_artifacts(10)
    specs = memory.list_specs(10)
    memory.close()
    assert specs[0]["preamble"].startswith("def frozenDistance")
    assert "def frozenDistance" in artifacts[0]["source"]


def test_missing_contract_does_not_let_formalizer_choose_target(tmp_path: Path) -> None:
    workspace, _, _ = _prepare_workspace(tmp_path, include_contract=False)
    fake = tmp_path / "fake_lean.py"
    _write_fake_lean(fake)
    with FormalPipeline(workspace, GoodFormalizer(), LeanKernel(["python", str(fake)])) as pipeline:
        summary = pipeline.run()
    assert summary.missing_contract == 1
    assert summary.attempted_formalizations == 0
    assert summary.formal_verified == 0


def test_same_statement_different_predicate_does_not_reuse_contract(tmp_path: Path) -> None:
    workspace, _, _ = _prepare_workspace(tmp_path, contract_predicate_operator="gt")
    fake = tmp_path / "fake_lean.py"
    _write_fake_lean(fake)
    with FormalPipeline(workspace, GoodFormalizer(), LeanKernel(["python", str(fake)])) as pipeline:
        summary = pipeline.run()
    assert summary.missing_contract == 1
    assert summary.attempted_formalizations == 0


def test_stale_natural_language_proof_invalidates_formal_lineage(tmp_path: Path) -> None:
    workspace, _, proof_spec_id = _prepare_workspace(tmp_path)
    fake = tmp_path / "fake_lean.py"
    _write_fake_lean(fake)
    kernel = LeanKernel(["python", str(fake)])
    with FormalPipeline(workspace, GoodFormalizer(), kernel, max_repairs=0) as pipeline:
        assert pipeline.run().formal_verified == 1

    proofs = ProofMemory(workspace / "proofs.sqlite3")
    proofs.mark_spec_invalid(proof_spec_id)
    proofs.close()

    with FormalPipeline(workspace, GoodFormalizer(), kernel, max_repairs=0) as pipeline:
        summary = pipeline.run()
    assert summary.invalidated_stale >= 1

    memory = FormalMemory(workspace / "formal.sqlite3")
    specs = memory.list_specs(10)
    artifacts = memory.list_artifacts(10)
    runs = memory.list_kernel_runs(10)
    memory.close()
    assert all(item["status"] == "invalidated" for item in specs)
    assert all(item["status"] == "invalidated" for item in artifacts)
    assert all(item["status"] == "invalidated" for item in runs)
    assert all(not item["passed"] for item in runs)
