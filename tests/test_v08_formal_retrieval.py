from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_evolve.formal_corpus import FormalCorpus
from research_evolve.formal_project import LeanProjectLock
from research_evolve.formal_search import FormalSearchPolicy
from research_evolve.goal_retrieval import GoalPremiseSelector


TOOLCHAIN = "leanprover/lean4:v4.30.0"


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "Root").mkdir(parents=True)
    (root / "lean-toolchain").write_text(TOOLCHAIN + "\n", encoding="utf-8")
    (root / "lakefile.toml").write_text(
        'name = "v08-test"\nversion = "0.1.0"\ndefaultTargets = ["Root"]\n'
        '[[lean_lib]]\nname = "Root"\n'
        '[[require]]\nname = "dep"\ngit = "https://example.invalid/dep"\nrev = "deadbeef"\n',
        encoding="utf-8",
    )
    (root / "Root.lean").write_text("import Root.Interface\n", encoding="utf-8")
    (root / "Root" / "Interface.lean").write_text("import DepLib.Premises\n", encoding="utf-8")
    (root / "lake-manifest.json").write_text(
        json.dumps({"version":"1.2.0","packagesDir":".lake/packages","packages":[{"name":"dep","scope":"","type":"git","url":"https://example.invalid/dep","rev":"deadbeef"}]}),
        encoding="utf-8",
    )
    dep = root / ".lake" / "packages" / "dep" / "DepLib"
    dep.mkdir(parents=True)
    (root / ".lake" / "packages" / "dep" / "lakefile.toml").write_text('name = "dep"\nversion = "0.1.0"\n', encoding="utf-8")
    (dep / "Premises.lean").write_text(
        "namespace DepLib\n\ntheorem distance_nonnegative (d : Nat) : 0 ≤ d := Nat.zero_le d\n\ntheorem unrelated_reflexive (d : Nat) : d = d := rfl\n\nend DepLib\n",
        encoding="utf-8",
    )
    return root


def test_formal_corpus_indexes_frozen_dependency_and_import_closure(tmp_path: Path) -> None:
    root = _project(tmp_path)
    lock = LeanProjectLock.capture(root)
    corpus_path = tmp_path / "corpus.sqlite3"
    info = FormalCorpus.build(root, lock, corpus_path)
    assert info.project_fingerprint == lock.fingerprint
    assert info.premises >= 2
    with FormalCorpus(corpus_path) as corpus:
        reachable = corpus.reachable_modules(["Root"])
        assert reachable["Root"] == 0
        assert reachable["Root.Interface"] == 1
        assert reachable["DepLib.Premises"] == 2
        selector = GoalPremiseSelector(corpus, limit=4)
        selection = selector.select(formal_spec_id="formal-spec", query="prove distance nonnegative Nat", root_imports=["Root"])
        names = [item.premise.name for item in selection.selected]
        assert "DepLib.distance_nonnegative" in names
        distance = next(item for item in selection.selected if item.premise.name == "DepLib.distance_nonnegative")
        assert distance.module_distance == 2
        assert distance.premise.source_kind == "dependency"
        assert distance.premise.package == "dep"


def test_goal_selector_never_widens_beyond_frozen_import_closure(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "Hidden.lean").write_text("namespace Hidden\ntheorem distance_nonnegative_super_match (d : Nat) : 0 ≤ d := Nat.zero_le d\nend Hidden\n", encoding="utf-8")
    lock = LeanProjectLock.capture(root)
    corpus_path = tmp_path / "corpus.sqlite3"
    FormalCorpus.build(root, lock, corpus_path)
    with FormalCorpus(corpus_path) as corpus:
        selector = GoalPremiseSelector(corpus, limit=8)
        selection = selector.select(formal_spec_id="formal-spec", query="distance nonnegative super match", root_imports=["Root"])
        names = {item.premise.name for item in selection.selected}
        assert "Hidden.distance_nonnegative_super_match" not in names
        assert "DepLib.distance_nonnegative" in names


def test_goal_selector_uses_lean_diagnostics_as_query_signal(tmp_path: Path) -> None:
    root = _project(tmp_path)
    lock = LeanProjectLock.capture(root)
    corpus_path = tmp_path / "corpus.sqlite3"
    FormalCorpus.build(root, lock, corpus_path)
    with FormalCorpus(corpus_path) as corpus:
        selector = GoalPremiseSelector(corpus, limit=4)
        selection = selector.select(formal_spec_id="formal-spec", query="prove theorem", root_imports=["Root"], diagnostics="unknown constant distance_nonnegative")
        assert selection.diagnostics_included
        assert any(item.premise.name == "DepLib.distance_nonnegative" for item in selection.selected)


def test_corpus_build_rejects_tampered_dependency_bytes(tmp_path: Path) -> None:
    root = _project(tmp_path)
    lock = LeanProjectLock.capture(root)
    source = root / ".lake" / "packages" / "dep" / "DepLib" / "Premises.lean"
    source.write_text(source.read_text(encoding="utf-8") + "\n-- tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no longer matches frozen project lock"):
        FormalCorpus.build(root, lock, tmp_path / "corpus.sqlite3")


def test_formal_search_policy_validation() -> None:
    FormalSearchPolicy(beam_width=2, branching_factor=3, max_rounds=2, max_kernel_attempts=7).validate()
    with pytest.raises(ValueError, match="beam_width"):
        FormalSearchPolicy(beam_width=0).validate()
    with pytest.raises(ValueError, match="max_kernel_attempts"):
        FormalSearchPolicy(max_kernel_attempts=0).validate()
