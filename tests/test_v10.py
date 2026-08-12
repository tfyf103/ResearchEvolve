from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_evolve.candidates import Candidate
from research_evolve.conjectures import Predicate, ValueRef
from research_evolve.reproducibility import stable_json_hash
from research_evolve.formal_pipeline import FormalPipeline
from research_evolve.formal import FormalArtifact, FormalMemory, FormalizationSpec, KernelResult
from research_evolve.project_kernel import ProjectCheckMemory, ProjectCheckResult
from research_evolve.certificate import ResearchCertificate
from research_evolve.lean_kernel import LeanKernel
from research_evolve.semantic_bridge import (
    CertifiedSemanticBridge,
    MathExpr,
    PredicateIRCompiler,
    SemanticAuditMemory,
    SemanticAuditor,
    SemanticContractCompiler,
    SemanticRegistry,
    UnsupportedSemantics,
)


def _registry(*, second: bool = False) -> SemanticRegistry:
    symbols = [
        {
            "source": "metrics",
            "key": "distance_to_42",
            "math_type": "Nat",
            "lean_name": "d",
            "imports": ["FormalProject42.Premises"],
            "semantic_version": "distance-v1",
            "meaning": "Absolute integer distance from the evaluated candidate to 42.",
            "test_values": [0, 1, 2, 42],
            "evaluator_sha256": "c" * 64,
        }
    ]
    if second:
        symbols.append(
            {
                "source": "metrics",
                "key": "upper_bound",
                "math_type": "Nat",
                "lean_name": "u",
                "imports": ["FormalProject42.Premises"],
                "semantic_version": "upper-v1",
                "meaning": "A certified natural-number upper bound.",
                "test_values": [0, 1, 8],
                "evaluator_sha256": "c" * 64,
            }
        )
    return SemanticRegistry.from_dict(
        {
            "schema_version": 1,
            "backend": "lean4",
            "toolchain": "leanprover/lean4:v4.30.0",
            "project_fingerprint": "a" * 64,
            "premise_index_fingerprint": "b" * 64,
            "symbols": symbols,
        }
    )


def _predicate(operator: str = "ge") -> Predicate:
    return Predicate(
        left=ValueRef(source="metrics", key="distance_to_42"),
        operator=operator,  # type: ignore[arg-type]
        right_constant=0,
    )


def test_typed_ir_is_scoped_and_compiles_direction_exactly() -> None:
    expression = PredicateIRCompiler(_registry()).compile(_predicate("ge"))
    expression.validate()
    assert expression.to_lean() == "∀ (d : Nat), d ≥ 0"
    binders, body = expression.binders_and_body()
    assert binders == [("d", "Nat")]
    assert body.evaluate({"d": 0}) is True
    assert body.evaluate({"d": 4}) is True

    with pytest.raises(ValueError, match="unbound or mistyped"):
        MathExpr(kind="var", type="Nat", name="free").validate()


def test_contract_compiler_needs_no_model_authored_signature_and_is_deterministic() -> None:
    compiler = SemanticContractCompiler(_registry())
    first = compiler.compile("conjecture-1", "Distance is non-negative.", _predicate())
    second = compiler.compile("conjecture-1", "Distance is non-negative.", _predicate())
    third = compiler.compile("a-different-lineage-id", "Distance is non-negative.", _predicate())
    assert first == second
    assert first["theorem_signature"] == third["theorem_signature"]
    assert first["theorem_signature"].endswith("(d : Nat) : d ≥ 0")
    assert first["metadata"]["registry_fingerprint"] == _registry().fingerprint
    assert first["metadata"]["ir_fingerprint"] == stable_json_hash(first["semantic_ir"])


@pytest.mark.parametrize("field", ["theorem_signature", "theorem_name", "semantic_ir", "imports", "toolchain"])
def test_independent_auditor_rejects_contract_tampering(field: str) -> None:
    registry = _registry()
    predicate = _predicate()
    contract = SemanticContractCompiler(registry).compile("c1", "Distance is non-negative.", predicate)
    if field == "theorem_signature":
        contract[field] = str(contract[field]).replace("≥", "≤")
    elif field == "theorem_name":
        contract[field] = "weakened"
    elif field == "semantic_ir":
        contract[field] = {"kind": "const", "type": "Bool", "value": True}
    elif field == "imports":
        contract[field] = []
    else:
        contract[field] = "leanprover/lean4:v4.29.0"
    result = SemanticAuditor(registry).audit(contract, "c1", "Distance is non-negative.", predicate)
    assert not result.passed
    assert result.issues


def test_bridge_differentially_audits_boundary_and_real_candidate_vectors(tmp_path: Path) -> None:
    registry = _registry()
    memory = SemanticAuditMemory(tmp_path / "semantic.sqlite3")
    bridge = CertifiedSemanticBridge(registry, memory)
    candidate = Candidate(payload={"x": 40}, metrics={"distance_to_42": 2.0}, valid=True)
    contract = bridge.compile_and_audit(
        "c1",
        "Distance is non-negative.",
        _predicate(),
        [candidate],
    )
    assert contract["status"] == "certified_formal_contract"
    assert contract["metadata"]["semantic_audit_checked_vectors"] == 5
    records = memory.list()
    assert records[0]["passed"] is True
    assert records[0]["audit"]["checked_vectors"] == 5
    bridge.close()


def test_unknown_or_transformed_semantics_fail_closed() -> None:
    compiler = PredicateIRCompiler(_registry())
    with pytest.raises(UnsupportedSemantics, match="unregistered"):
        compiler.compile(
            Predicate(
                left=ValueRef(source="metrics", key="not_registered"),
                operator="ge",
                right_constant=0,
            )
        )
    with pytest.raises(UnsupportedSemantics, match="scaled/offset"):
        compiler.compile(
            Predicate(
                left=ValueRef(source="metrics", key="distance_to_42", scale=2.0),
                operator="ge",
                right_constant=0,
            )
        )


def test_two_symbol_predicate_has_deterministic_distinct_quantifiers() -> None:
    predicate = Predicate(
        left=ValueRef(source="metrics", key="distance_to_42"),
        operator="le",
        right_ref=ValueRef(source="metrics", key="upper_bound"),
    )
    expression = PredicateIRCompiler(_registry(second=True)).compile(predicate)
    assert expression.to_lean() == "∀ (d : Nat) (u : Nat), d ≤ u"
    expression.validate()


def test_registry_fingerprint_changes_on_semantic_meaning_change() -> None:
    original = _registry().to_dict()
    changed = json.loads(json.dumps(original))
    changed["symbols"][0]["meaning"] = "A different mathematical meaning."
    assert SemanticRegistry.from_dict(changed).fingerprint != _registry().fingerprint


def test_registry_type_violation_in_real_candidate_fails_audit(tmp_path: Path) -> None:
    bridge = CertifiedSemanticBridge(_registry(), SemanticAuditMemory(tmp_path / "semantic.sqlite3"))
    candidate = Candidate(payload={"x": 1.5}, metrics={"distance_to_42": 40.5}, valid=True)
    with pytest.raises(Exception, match="violates registry type"):
        bridge.compile_and_audit("c1", "Distance is non-negative.", _predicate(), [candidate])
    bridge.close()


def test_pipeline_generates_certified_contract_when_manual_contract_is_absent(tmp_path: Path) -> None:
    from test_v06 import GoodFormalizer, _prepare_workspace, _write_fake_lean

    workspace, _, _ = _prepare_workspace(tmp_path, include_contract=False)
    manifest_path = workspace / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["inputs"]["evaluators"] = [{"sha256": "c" * 64}]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    fake = tmp_path / "fake_lean.py"
    _write_fake_lean(fake)
    registry_data = _registry().to_dict()
    registry_data["symbols"][0]["key"] = "distance"
    registry = SemanticRegistry.from_dict(registry_data)
    bridge = CertifiedSemanticBridge(registry, SemanticAuditMemory(workspace / "semantic_contracts.sqlite3"))
    with FormalPipeline(
        workspace,
        GoodFormalizer(),
        LeanKernel(["python", str(fake)]),
        semantic_bridge=bridge,
    ) as pipeline:
        summary = pipeline.run()
    assert summary.certified_contracts == 1
    assert summary.formal_verified == 1
    assert summary.missing_contract == 0
    records = SemanticAuditMemory(workspace / "semantic_contracts.sqlite3")
    try:
        assert records.list()[0]["audit"]["passed"] is True
    finally:
        records.close()


def _fake_certificate(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "run"
    workspace.mkdir()
    for filename in ResearchCertificate.REQUIRED_RUN_FILES:
        data = {"fingerprint": "formal-fp"} if filename == "formal_manifest.json" else {}
        if filename == "manifest.json":
            data = {"inputs": {"evaluators": [{"sha256": "c" * 64}]}}
        (workspace / filename).write_text(json.dumps(data), encoding="utf-8")
    project_root = Path(__file__).parents[1] / "examples" / "formal_project42" / "lean_project"
    lock_path = project_root.parent / "project-lock.json"
    index_path = project_root.parent / "premise-index.json"
    lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
    index_data = json.loads(index_path.read_text(encoding="utf-8"))
    registry_data = _registry().to_dict()
    registry_data["project_fingerprint"] = lock_data["fingerprint"]
    registry_data["premise_index_fingerprint"] = index_data["fingerprint"]
    registry = SemanticRegistry.from_dict(registry_data)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry.to_dict()), encoding="utf-8")

    formal = FormalMemory(workspace / "formal.sqlite3")
    spec = FormalizationSpec(
        proof_spec_id="p", proof_artifact_id="pa", conjecture_id="c", conjecture_statement="S",
        theorem_name="t", theorem_signature="theorem t (d : Nat) : d ≥ 0",
        imports=["FormalProject42.Premises"], toolchain="leanprover/lean4:v4.30.0",
        metadata={"project_fingerprint": lock_data["fingerprint"]},
    )
    artifact = FormalArtifact(formal_spec_id=spec.id, proof_term="by exact FormalProject42.distance_nonnegative d")
    source = artifact.build_source(spec)
    import hashlib
    result = KernelResult(
        formal_artifact_id=artifact.id, passed=True, status="formal_verified", command=["lean"],
        expected_toolchain=spec.toolchain, detected_version="4.30.0", exit_code=0,
        source_sha256=hashlib.sha256(source.encode()).hexdigest(),
    )
    formal.record_spec(spec, "formal_verified")
    formal.record_artifact(artifact, source, "formal_verified")
    formal.record_kernel_result(spec.id, result)
    formal.close()
    semantic = SemanticAuditMemory(workspace / "semantic_contracts.sqlite3")
    contract = SemanticContractCompiler(registry).compile("c", "S", _predicate())
    audit = SemanticAuditor(registry).audit(contract, "c", "S", _predicate())
    semantic.record("c", contract, audit)
    semantic.close()
    project = ProjectCheckMemory(workspace / "formal_project.sqlite3")
    project.record(ProjectCheckResult(
        formal_spec_id=spec.id, formal_artifact_id=artifact.id, project_fingerprint=lock_data["fingerprint"],
        passed=True, checker_command=["leanchecker", "--fresh", "ResearchEvolveGenerated"],
    ))
    project.close()

    output = tmp_path / "certificate"
    ResearchCertificate.export(
        workspace, output, semantic_registry=registry_path, project_lock=lock_path, premise_index=index_path,
    )
    return output, workspace


def test_certificate_structural_verification_and_tamper_detection(tmp_path: Path) -> None:
    certificate, _ = _fake_certificate(tmp_path)
    assert ResearchCertificate.verify(certificate).passed
    (certificate / "formal_specs.json").write_text("[]", encoding="utf-8")
    result = ResearchCertificate.verify(certificate)
    assert not result.passed
    assert any("hash/size mismatch" in issue for issue in result.issues)
