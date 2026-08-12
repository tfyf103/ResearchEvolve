from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from research_evolve.formal import FormalArtifact, FormalizationSpec
from research_evolve.formal_project import LeanProjectEnvironment, LeanProjectLock
from research_evolve.formal_retrieval import PremiseIndex, PremiseSelector
from research_evolve.project_kernel import ProjectCheckMemory, ProjectLeanKernel


TOOLCHAIN = "leanprover/lean4:v4.30.0"


def _project(tmp_path: Path, *, with_dependency: bool = False) -> Path:
    root = tmp_path / "lean-project"
    (root / "Demo").mkdir(parents=True)
    (root / "lean-toolchain").write_text(TOOLCHAIN + "\n", encoding="utf-8")
    dependency = '\n[[require]]\nname = "external"\ngit = "https://example.invalid/external"\nrev = "deadbeef"\n' if with_dependency else ""
    (root / "lakefile.toml").write_text(
        'name = "demo"\nversion = "0.1.0"\ndefaultTargets = ["Demo"]\n\n[[lean_lib]]\nname = "Demo"\n' + dependency,
        encoding="utf-8",
    )
    (root / "Demo.lean").write_text("import Demo.Premises\n", encoding="utf-8")
    (root / "Demo" / "Premises.lean").write_text(
        "namespace Demo\n\ntheorem distance_nonnegative (d : Nat) : 0 ≤ d := Nat.zero_le d\n\ntheorem reflexive (d : Nat) : d = d := rfl\n\nend Demo\n",
        encoding="utf-8",
    )
    return root


def _fake_lake(path: Path, *, checker_fails: bool = False) -> None:
    path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import pathlib, sys",
                "args = sys.argv[1:]",
                "if args[:3] == ['env', 'lean', '--version']:",
                "    print('Lean (version 4.30.0, fake-project-kernel)')",
                "    raise SystemExit(0)",
                "if args and args[0] == 'build':",
                "    raise SystemExit(0)",
                "if args[:2] == ['env', 'lean'] and '-o' in args:",
                "    source = pathlib.Path(args[-1]).read_text(encoding='utf-8')",
                "    if 'by exact 0' in source:",
                "        print(f'{args[-1]}:1:0: error: synthetic type mismatch', file=sys.stderr)",
                "        raise SystemExit(1)",
                "    output = pathlib.Path(args[args.index('-o') + 1])",
                "    output.parent.mkdir(parents=True, exist_ok=True)",
                "    output.write_text('fake olean', encoding='utf-8')",
                "    print(\"'distance_nonnegative' does not depend on any axioms\")",
                "    raise SystemExit(0)",
                "if args[:3] == ['env', 'leanchecker', '--fresh']:",
                "    target = pathlib.Path('.lake/build/lib/lean') / f'{args[3]}.olean'",
                "    if not target.is_file():",
                "        print('missing generated olean', file=sys.stderr)",
                "        raise SystemExit(2)",
                f"    raise SystemExit({1 if checker_fails else 0})",
                "print(f'unexpected fake lake invocation: {args}', file=sys.stderr)",
                "raise SystemExit(3)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _spec(project_fingerprint: str) -> FormalizationSpec:
    return FormalizationSpec(
        proof_spec_id="proof-spec",
        proof_artifact_id="proof-artifact",
        conjecture_id="conjecture",
        conjecture_statement="Distance is non-negative.",
        theorem_name="distance_nonnegative",
        theorem_signature="theorem distance_nonnegative (d : Nat) : 0 ≤ d",
        imports=["Demo.Premises"],
        toolchain=TOOLCHAIN,
        metadata={"project_fingerprint": project_fingerprint},
    )


def test_project_lock_roundtrip_and_mutation_detection(tmp_path: Path) -> None:
    root = _project(tmp_path)
    lock = LeanProjectLock.capture(root)
    path = tmp_path / "project-lock.json"
    lock.write(path)
    loaded = LeanProjectLock.read(path)
    assert loaded.fingerprint == lock.fingerprint
    loaded.verify_project(root)

    source = root / "Demo" / "Premises.lean"
    source.write_text(source.read_text(encoding="utf-8") + "\n-- changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no longer matches frozen project lock"):
        loaded.verify_project(root)


def test_project_with_dependencies_requires_manifest(tmp_path: Path) -> None:
    root = _project(tmp_path, with_dependency=True)
    with pytest.raises(ValueError, match="has no lake-manifest.json"):
        LeanProjectLock.capture(root)


def test_premise_index_and_selector_are_project_bound(tmp_path: Path) -> None:
    root = _project(tmp_path)
    lock = LeanProjectLock.capture(root)
    index = PremiseIndex.build_from_project(root, lock)
    assert index.project_fingerprint == lock.fingerprint
    names = {item.name for item in index.premises}
    assert "Demo.distance_nonnegative" in names

    selector = PremiseSelector(index, limit=4)
    selection = selector.select(
        formal_spec_id="formal-spec",
        query="prove distance nonnegative Nat zero",
        allowed_modules=["Demo.Premises"],
    )
    selected_names = [item.premise.name for item in selection.selected]
    assert "Demo.distance_nonnegative" in selected_names

    filtered = selector.select(
        formal_spec_id="formal-spec",
        query="distance nonnegative",
        allowed_modules=["Other.Module"],
    )
    assert filtered.selected == []


def test_project_kernel_requires_contract_project_fingerprint(tmp_path: Path) -> None:
    root = _project(tmp_path)
    lock = LeanProjectLock.capture(root)
    fake = tmp_path / "fake_lake.py"
    _fake_lake(fake)
    environment = LeanProjectEnvironment.create(root, lock, lake_command=[sys.executable, str(fake)])
    kernel = ProjectLeanKernel(environment)
    spec = _spec("0" * 64)
    artifact = FormalArtifact(formal_spec_id=spec.id, proof_term="by exact Demo.distance_nonnegative d")
    result, _ = kernel.verify(spec, artifact, workspace=tmp_path / "workspace")
    assert result.status == "environment_error"
    assert result.gate_reason == "project-fingerprint-mismatch"


def test_project_kernel_runs_build_compile_and_fresh_checker(tmp_path: Path) -> None:
    root = _project(tmp_path)
    lock = LeanProjectLock.capture(root)
    fake = tmp_path / "fake_lake.py"
    _fake_lake(fake)
    environment = LeanProjectEnvironment.create(root, lock, lake_command=[sys.executable, str(fake)], build_targets=["Demo"])
    kernel = ProjectLeanKernel(environment)
    spec = _spec(lock.fingerprint)
    artifact = FormalArtifact(formal_spec_id=spec.id, proof_term="by exact Demo.distance_nonnegative d")
    workspace = tmp_path / "workspace"
    result, source = kernel.verify(spec, artifact, workspace=workspace)
    assert "import Demo.Premises" in source
    assert result.status == "formal_verified"
    assert result.passed

    memory = ProjectCheckMemory(workspace / "formal_project.sqlite3")
    checks = memory.list(10)
    memory.close()
    assert checks[0]["passed"] is True
    assert checks[0]["build_command"][-1] == "Demo"
    assert checks[0]["checker_command"][-2:] == ["--fresh", "ResearchEvolveGenerated"]


def test_project_kernel_fails_closed_when_fresh_checker_fails(tmp_path: Path) -> None:
    root = _project(tmp_path)
    lock = LeanProjectLock.capture(root)
    fake = tmp_path / "fake_lake.py"
    _fake_lake(fake, checker_fails=True)
    environment = LeanProjectEnvironment.create(root, lock, lake_command=[sys.executable, str(fake)])
    kernel = ProjectLeanKernel(environment)
    spec = _spec(lock.fingerprint)
    artifact = FormalArtifact(formal_spec_id=spec.id, proof_term="by exact Demo.distance_nonnegative d")
    result, _ = kernel.verify(spec, artifact, workspace=tmp_path / "workspace")
    assert result.status == "kernel_rejected"
    assert result.gate_reason == "fresh-checker-failed"
    assert not result.passed
