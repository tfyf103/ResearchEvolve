from pathlib import Path

import pytest

from research_evolve.formal_project import LeanProjectLock


def test_unlocked_dependency_lock_cannot_enter_certification_path(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.30.0\n", encoding="utf-8")
    (root / "lakefile.toml").write_text(
        'name = "demo"\nversion = "0.1.0"\n\n[[lean_lib]]\nname = "Demo"\n\n[[require]]\nname = "external"\ngit = "https://example.invalid/external"\nrev = "deadbeef"\n',
        encoding="utf-8",
    )
    (root / "Demo.lean").write_text("theorem demo_true : True := True.intro\n", encoding="utf-8")

    development_lock = LeanProjectLock.capture(root, allow_unlocked_dependencies=True)
    assert development_lock.manifest is None
    assert development_lock.dependencies == []

    with pytest.raises(ValueError, match="has no lake-manifest.json"):
        development_lock.verify_project(root)
