from __future__ import annotations

import sys
from pathlib import Path

from research_evolve.formal import FormalArtifact, FormalizationSpec
from research_evolve.formal_project import LeanProjectEnvironment, LeanProjectLock
from research_evolve.project_kernel import ProjectCheckMemory, ProjectLeanKernel


TOOLCHAIN = "leanprover/lean4:v4.30.0"


def test_fresh_checker_timeout_is_environment_error_and_audited(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "lean-toolchain").write_text(TOOLCHAIN + "\n", encoding="utf-8")
    (root / "lakefile.toml").write_text(
        'name = "demo"\nversion = "0.1.0"\ndefaultTargets = ["Demo"]\n\n[[lean_lib]]\nname = "Demo"\n',
        encoding="utf-8",
    )
    (root / "Demo.lean").write_text(
        "theorem distance_nonnegative (d : Nat) : 0 ≤ d := Nat.zero_le d\n",
        encoding="utf-8",
    )
    lock = LeanProjectLock.capture(root)

    fake_lake = tmp_path / "fake_lake.py"
    fake_lake.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import pathlib, sys, time",
                "args = sys.argv[1:]",
                "if args[:3] == ['env', 'lean', '--version']:",
                "    print('Lean (version 4.30.0, fake-project-kernel)')",
                "    raise SystemExit(0)",
                "if args and args[0] == 'build':",
                "    raise SystemExit(0)",
                "if args[:2] == ['env', 'lean'] and '-o' in args:",
                "    output = pathlib.Path(args[args.index('-o') + 1])",
                "    output.parent.mkdir(parents=True, exist_ok=True)",
                "    output.write_text('fake olean', encoding='utf-8')",
                "    print(\"'distance_nonnegative' does not depend on any axioms\")",
                "    raise SystemExit(0)",
                "if args[:3] == ['env', 'leanchecker', '--fresh']:",
                "    time.sleep(1.0)",
                "    raise SystemExit(0)",
                "raise SystemExit(3)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    environment = LeanProjectEnvironment.create(
        root,
        lock,
        lake_command=[sys.executable, str(fake_lake)],
    )
    kernel = ProjectLeanKernel(
        environment,
        timeout_seconds=1.0,
        fresh_checker_timeout_seconds=0.05,
    )
    spec = FormalizationSpec(
        proof_spec_id="proof-spec",
        proof_artifact_id="proof-artifact",
        conjecture_id="conjecture",
        conjecture_statement="Distance is non-negative.",
        theorem_name="distance_nonnegative",
        theorem_signature="theorem distance_nonnegative (d : Nat) : 0 ≤ d",
        toolchain=TOOLCHAIN,
        metadata={"project_fingerprint": lock.fingerprint},
    )
    artifact = FormalArtifact(
        formal_spec_id=spec.id,
        proof_term="by exact Nat.zero_le d",
    )
    workspace = tmp_path / "workspace"

    result, _ = kernel.verify(spec, artifact, workspace=workspace)

    assert result.status == "environment_error"
    assert result.gate_reason == "fresh-checker-timeout"
    assert result.passed is False
    assert result.command[-2:] == ["--fresh", "ResearchEvolveGenerated"]

    memory = ProjectCheckMemory(workspace / "formal_project.sqlite3")
    checks = memory.list(10)
    memory.close()
    assert checks[0]["passed"] is False
    assert checks[0]["gate_reason"] == "fresh-checker-timeout"
    assert checks[0]["checker_command"][-2:] == ["--fresh", "ResearchEvolveGenerated"]
    assert checks[0]["checker_exit_code"] is None
