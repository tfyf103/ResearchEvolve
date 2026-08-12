from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .formal import FormalArtifact, FormalMemory, FormalizationSpec
from .formal_project import LeanProjectEnvironment, LeanProjectLock
from .formal_retrieval import PremiseIndex
from .project_kernel import ProjectCheckMemory, ProjectLeanKernel
from .reproducibility import stable_json_hash
from .semantic_bridge import SemanticAuditMemory, SemanticRegistry


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


@dataclass(frozen=True, slots=True)
class CertificateVerification:
    passed: bool
    structural_verified: bool
    replayed: bool
    replayed_theorems: int
    issues: tuple[str, ...]
    certificate_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "structural_verified": self.structural_verified,
            "replayed": self.replayed,
            "replayed_theorems": self.replayed_theorems,
            "issues": list(self.issues),
            "certificate_fingerprint": self.certificate_fingerprint,
        }


class ResearchCertificate:
    """Portable content-addressed evidence package with optional fresh Lean replay."""

    SCHEMA_VERSION = 1
    REQUIRED_RUN_FILES = (
        "manifest.json",
        "summary.json",
        "proof_manifest.json",
        "formal_manifest.json",
        "formal_summary.json",
    )

    @classmethod
    def export(
        cls,
        workspace: str | Path,
        output: str | Path,
        *,
        semantic_registry: str | Path,
        project_lock: str | Path,
        premise_index: str | Path,
    ) -> dict[str, Any]:
        workspace_path = Path(workspace)
        output_path = Path(output)
        if output_path.exists() and any(output_path.iterdir()):
            raise ValueError(f"certificate output directory must be absent or empty: {output_path}")
        output_path.mkdir(parents=True, exist_ok=True)
        for filename in cls.REQUIRED_RUN_FILES:
            source = workspace_path / filename
            if not source.is_file():
                raise ValueError(f"certificate requires run artifact: {source}")
            shutil.copy2(source, output_path / filename)

        registry = SemanticRegistry.read(semantic_registry)
        lock = LeanProjectLock.read(project_lock)
        index = PremiseIndex.read(premise_index)
        if registry.project_fingerprint != lock.fingerprint or index.project_fingerprint != lock.fingerprint:
            raise ValueError("certificate registry/index do not bind the supplied Lean project lock")
        if registry.premise_index_fingerprint != index.fingerprint:
            raise ValueError("certificate registry does not bind the supplied premise index")
        _write_json(output_path / "semantic_registry.json", registry.to_dict())
        _write_json(output_path / "lean_project_lock.json", json.loads(Path(project_lock).read_text(encoding="utf-8")))
        _write_json(output_path / "premise_index.json", json.loads(Path(premise_index).read_text(encoding="utf-8")))

        formal = FormalMemory(workspace_path / "formal.sqlite3")
        semantic = SemanticAuditMemory(workspace_path / "semantic_contracts.sqlite3")
        project = ProjectCheckMemory(workspace_path / "formal_project.sqlite3")
        try:
            _write_json(output_path / "formal_specs.json", formal.list_specs(100000))
            _write_json(output_path / "formal_artifacts.json", formal.list_artifacts(100000))
            _write_json(output_path / "kernel_runs.json", formal.list_kernel_runs(100000))
            _write_json(output_path / "semantic_contracts.json", semantic.list(100000))
            _write_json(output_path / "project_checks.json", project.list(100000))
        finally:
            formal.close()
            semantic.close()
            project.close()

        sources = workspace_path / "formal_sources"
        if sources.is_dir():
            for source in sorted(sources.rglob("*.lean")):
                relative = source.relative_to(sources)
                target = output_path / "formal_sources" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

        entries: list[dict[str, Any]] = []
        for path in sorted(item for item in output_path.rglob("*") if item.is_file()):
            relative = path.relative_to(output_path).as_posix()
            if relative == "certificate.json":
                continue
            entries.append({"path": relative, "sha256": _sha256(path), "size": path.stat().st_size})
        stable = {
            "schema_version": cls.SCHEMA_VERSION,
            "files": entries,
            "registry_fingerprint": registry.fingerprint,
            "project_fingerprint": lock.fingerprint,
            "premise_index_fingerprint": index.fingerprint,
            "formal_manifest_fingerprint": json.loads((output_path / "formal_manifest.json").read_text(encoding="utf-8")).get("fingerprint"),
        }
        manifest = {**stable, "fingerprint": stable_json_hash(stable)}
        _write_json(output_path / "certificate.json", manifest)
        return manifest

    @classmethod
    def verify(
        cls,
        certificate: str | Path,
        *,
        project_root: str | Path | None = None,
        lake_command: str = "lake",
        timeout_seconds: float = 300.0,
    ) -> CertificateVerification:
        root = Path(certificate)
        issues: list[str] = []
        manifest_path = root / "certificate.json"
        if not manifest_path.is_file():
            return CertificateVerification(False, False, False, 0, ("certificate.json is missing",), "")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return CertificateVerification(False, False, False, 0, (f"invalid certificate manifest: {exc}",), "")
        fingerprint = str(manifest.get("fingerprint", ""))
        entries = manifest.get("files", [])
        if manifest.get("schema_version") != cls.SCHEMA_VERSION or not isinstance(entries, list):
            issues.append("unsupported or malformed certificate manifest")
            entries = []
        expected_paths: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                issues.append("certificate contains malformed file entry")
                continue
            relative = str(entry.get("path", ""))
            pure = PurePosixPath(relative)
            if not relative or pure.is_absolute() or ".." in pure.parts or "\\" in relative:
                issues.append(f"unsafe certificate path: {relative!r}")
                continue
            expected_paths.add(relative)
            path = root.joinpath(*pure.parts)
            if not path.is_file():
                issues.append(f"certificate file is missing: {relative}")
                continue
            if path.stat().st_size != int(entry.get("size", -1)) or _sha256(path) != entry.get("sha256"):
                issues.append(f"certificate file hash/size mismatch: {relative}")
        actual_paths = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and path != manifest_path
        }
        if actual_paths != expected_paths:
            issues.append("certificate has missing or unmanifested files")
        stable = {key: manifest.get(key) for key in [
            "schema_version", "files", "registry_fingerprint", "project_fingerprint",
            "premise_index_fingerprint", "formal_manifest_fingerprint",
        ]}
        if stable_json_hash(stable) != fingerprint:
            issues.append("certificate root fingerprint mismatch")

        try:
            registry = SemanticRegistry.read(root / "semantic_registry.json")
            lock = LeanProjectLock.read(root / "lean_project_lock.json")
            index = PremiseIndex.read(root / "premise_index.json")
            if registry.fingerprint != manifest.get("registry_fingerprint"):
                issues.append("registry fingerprint differs from certificate manifest")
            research_manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            evaluator_hashes = {
                str(item.get("sha256", ""))
                for item in research_manifest.get("inputs", {}).get("evaluators", [])
                if isinstance(item, dict)
            }
            required_hashes = {symbol.evaluator_sha256 for symbol in registry.symbols}
            if not required_hashes.issubset(evaluator_hashes):
                issues.append("semantic registry evaluator implementation is absent from the research manifest")
            if lock.fingerprint != manifest.get("project_fingerprint") or index.project_fingerprint != lock.fingerprint:
                issues.append("project lock lineage mismatch")
            if index.fingerprint != manifest.get("premise_index_fingerprint") or registry.premise_index_fingerprint != index.fingerprint:
                issues.append("premise index lineage mismatch")
            semantic_contracts = json.loads((root / "semantic_contracts.json").read_text(encoding="utf-8"))
            if not semantic_contracts or any(not item.get("passed") or not item.get("audit", {}).get("passed") for item in semantic_contracts):
                issues.append("certificate lacks an accepted semantic audit")
            specs = json.loads((root / "formal_specs.json").read_text(encoding="utf-8"))
            artifacts = json.loads((root / "formal_artifacts.json").read_text(encoding="utf-8"))
            runs = json.loads((root / "kernel_runs.json").read_text(encoding="utf-8"))
            checks = json.loads((root / "project_checks.json").read_text(encoding="utf-8"))
            spec_by_id = {str(item.get("id")): item for item in specs}
            artifact_by_id = {str(item.get("id")): item for item in artifacts}
            verified_runs = [item for item in runs if item.get("passed") and item.get("status") == "formal_verified"]
            if not verified_runs:
                issues.append("certificate contains no formal_verified kernel run")
            for run in verified_runs:
                artifact = artifact_by_id.get(str(run.get("formal_artifact_id")))
                if artifact is None or str(artifact.get("formal_spec_id")) not in spec_by_id:
                    issues.append("formal proof lineage references missing spec/artifact")
                    continue
                source = str(artifact.get("source", ""))
                if hashlib.sha256(source.encode("utf-8")).hexdigest() != run.get("source_sha256"):
                    issues.append("kernel run source hash differs from certified artifact")
                matching_checks = [
                    check
                    for check in checks
                    if check.get("formal_artifact_id") == run.get("formal_artifact_id")
                    and check.get("passed") is True
                    and check.get("project_fingerprint") == lock.fingerprint
                ]
                if not matching_checks:
                    issues.append("formal_verified run lacks a passed project-bound check")
                elif not any(check.get("checker_command", [])[-2:] == ["--fresh", "ResearchEvolveGenerated"] for check in matching_checks):
                    issues.append("project-bound check lacks leanchecker --fresh evidence")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            issues.append(f"certificate lineage validation failed: {exc}")
            registry = lock = None  # type: ignore[assignment]
            spec_by_id = artifact_by_id = {}
            verified_runs = []

        structural = not issues
        replayed = project_root is not None
        replayed_count = 0
        if structural and project_root is not None:
            assert lock is not None
            try:
                environment = LeanProjectEnvironment.create(project_root, lock, lake_command=lake_command)
                kernel = ProjectLeanKernel(
                    environment,
                    timeout_seconds=timeout_seconds,
                    fresh_checker_timeout_seconds=timeout_seconds,
                )
                with tempfile.TemporaryDirectory(prefix="research-evolve-certificate-") as temporary:
                    replay_workspace = Path(temporary)
                    for run in verified_runs:
                        raw_artifact = dict(artifact_by_id[str(run["formal_artifact_id"])])
                        raw_spec = dict(spec_by_id[str(raw_artifact["formal_spec_id"])])
                        for key in ["status", "source", "source_sha256"]:
                            raw_artifact.pop(key, None)
                        raw_spec.pop("status", None)
                        spec = FormalizationSpec(**raw_spec)
                        artifact = FormalArtifact(**raw_artifact)
                        result, source = kernel.verify(spec, artifact, workspace=replay_workspace)
                        if not result.passed or result.status != "formal_verified":
                            issues.append(f"fresh Lean replay failed for {spec.theorem_name}: {result.gate_reason}")
                            break
                        if hashlib.sha256(source.encode("utf-8")).hexdigest() != run.get("source_sha256"):
                            issues.append(f"fresh Lean replay source changed for {spec.theorem_name}")
                            break
                        replayed_count += 1
            except (OSError, ValueError, TypeError) as exc:
                issues.append(f"fresh Lean replay could not run: {exc}")
        return CertificateVerification(
            passed=not issues,
            structural_verified=structural,
            replayed=replayed,
            replayed_theorems=replayed_count,
            issues=tuple(issues),
            certificate_fingerprint=fingerprint,
        )
