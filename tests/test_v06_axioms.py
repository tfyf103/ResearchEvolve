from __future__ import annotations

from pathlib import Path

from research_evolve.formal import FormalArtifact, FormalizationSpec
from research_evolve.lean_kernel import LeanKernel


def _fake_lean(path: Path, axiom_message: str) -> None:
    path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import pathlib, sys",
                "if '--version' in sys.argv:",
                "    print('Lean (version 4.30.0, fake-test-kernel)')",
                "    raise SystemExit(0)",
                "pathlib.Path(sys.argv[-1]).read_text(encoding='utf-8')",
                f"print({axiom_message!r})",
                "raise SystemExit(0)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _spec() -> FormalizationSpec:
    return FormalizationSpec(
        proof_spec_id="proof-spec",
        proof_artifact_id="proof-artifact",
        conjecture_id="conjecture",
        conjecture_statement="statement",
        theorem_name="metric_nonnegative",
        theorem_signature="theorem metric_nonnegative (d : Nat) : 0 ≤ d",
        toolchain="leanprover/lean4:v4.30.0",
    )


def test_kernel_allows_only_standard_axioms_by_default(tmp_path: Path) -> None:
    fake = tmp_path / "lean.py"
    _fake_lean(
        fake,
        "'metric_nonnegative' depends on axioms: [propext, Classical.choice, Quot.sound]",
    )
    spec = _spec()
    artifact = FormalArtifact(formal_spec_id=spec.id, proof_term="by exact Nat.zero_le _")
    result, _ = LeanKernel(["python", str(fake)]).verify(spec, artifact, workspace=tmp_path)
    assert result.status == "formal_verified"
    assert result.axioms == ["propext", "Classical.choice", "Quot.sound"]


def test_kernel_rejects_custom_or_compiler_trust_axioms(tmp_path: Path) -> None:
    for index, axiom in enumerate(["Custom.bad", "Lean.trustCompiler", "sorryAx"]):
        fake = tmp_path / f"lean_{index}.py"
        _fake_lean(fake, f"'metric_nonnegative' depends on axioms: [{axiom}]")
        spec = _spec()
        artifact = FormalArtifact(formal_spec_id=spec.id, proof_term="by exact Nat.zero_le _")
        result, _ = LeanKernel(["python", str(fake)]).verify(spec, artifact, workspace=tmp_path)
        assert result.status == "kernel_rejected"
        assert result.gate_reason == "disallowed-axioms"
        assert axiom in result.axioms
