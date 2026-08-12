from __future__ import annotations

import json
import sqlite3
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .formal import FormalArtifact, FormalizationSpec, KernelResult, LeanDiagnostic
from .formal_project import LeanProjectEnvironment
from .lean_kernel import LeanKernel


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ProjectCheckResult:
    formal_spec_id: str
    formal_artifact_id: str
    project_fingerprint: str
    passed: bool
    build_command: list[str] = field(default_factory=list)
    build_exit_code: int | None = None
    compile_command: list[str] = field(default_factory=list)
    compile_exit_code: int | None = None
    checker_command: list[str] = field(default_factory=list)
    checker_exit_code: int | None = None
    build_stdout: str = ""
    build_stderr: str = ""
    compile_stdout: str = ""
    compile_stderr: str = ""
    checker_stdout: str = ""
    checker_stderr: str = ""
    gate_reason: str = ""
    id: str = field(default_factory=lambda: f"project-check-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProjectCheckMemory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS project_checks (
                id TEXT PRIMARY KEY,
                formal_spec_id TEXT NOT NULL,
                formal_artifact_id TEXT NOT NULL,
                project_fingerprint TEXT NOT NULL,
                passed INTEGER NOT NULL,
                gate_reason TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_project_checks_spec ON project_checks(formal_spec_id);
            CREATE INDEX IF NOT EXISTS idx_project_checks_artifact ON project_checks(formal_artifact_id);
            """
        )
        self.conn.commit()

    def record(self, result: ProjectCheckResult) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO project_checks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.id,
                result.formal_spec_id,
                result.formal_artifact_id,
                result.project_fingerprint,
                int(result.passed),
                result.gate_reason,
                json.dumps(result.to_dict(), sort_keys=True),
                result.created_at,
            ),
        )
        self.conn.commit()

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT payload FROM project_checks ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def close(self) -> None:
        self.conn.close()


class ProjectLeanKernel(LeanKernel):
    """v0.7 frozen Lake-project kernel gate with built-in leanchecker --fresh replay."""

    GENERATED_MODULE = "ResearchEvolveGenerated"

    def __init__(
        self,
        environment: LeanProjectEnvironment,
        *,
        timeout_seconds: float = 60.0,
    ) -> None:
        super().__init__([*environment.lake_command, "env", "lean"], timeout_seconds=timeout_seconds)
        self.environment = environment

    @property
    def name(self) -> str:
        return f"lean-project-kernel:{self.environment.fingerprint[:16]}:leanchecker-fresh"

    @staticmethod
    def _combined(*parts: str) -> str:
        return "\n".join(part for part in parts if part)

    def _record(self, workspace: str | Path, check: ProjectCheckResult) -> None:
        memory = ProjectCheckMemory(Path(workspace) / "formal_project.sqlite3")
        try:
            memory.record(check)
        finally:
            memory.close()

    def _environment_result(
        self,
        spec: FormalizationSpec,
        artifact: FormalArtifact,
        source: str,
        reason: str,
        message: str,
    ) -> tuple[KernelResult, str]:
        digest = __import__("hashlib").sha256(source.encode("utf-8")).hexdigest()
        return (
            KernelResult(
                formal_artifact_id=artifact.id,
                passed=False,
                status="environment_error",
                command=[*self.environment.lake_command, "env", "lean"],
                expected_toolchain=spec.toolchain,
                detected_version=None,
                exit_code=None,
                diagnostics=[LeanDiagnostic(severity="error", message=message)],
                gate_reason=reason,
                source_sha256=digest,
            ),
            source,
        )

    def verify(
        self,
        spec: FormalizationSpec,
        artifact: FormalArtifact,
        *,
        workspace: str | Path,
    ) -> tuple[KernelResult, str]:
        source = artifact.build_source(spec)
        digest = __import__("hashlib").sha256(source.encode("utf-8")).hexdigest()

        if artifact.helper_source.strip():
            return self._gate_failure(
                spec,
                artifact,
                source,
                "untrusted-top-level-helper",
                "v0.7 project verification forbids model-supplied top-level helper_source",
            )
        forbidden = self._forbidden_token(source)
        if forbidden is not None:
            return self._gate_failure(
                spec,
                artifact,
                source,
                f"forbidden-token:{forbidden}",
                f"forbidden Lean escape hatch/metaprogramming token: {forbidden}",
            )

        expected_fingerprint = str(spec.metadata.get("project_fingerprint", "")).strip()
        if not expected_fingerprint:
            return self._environment_result(
                spec,
                artifact,
                source,
                "missing-project-fingerprint",
                "v0.7 ProjectLeanKernel requires project_fingerprint in the frozen formal contract",
            )
        if expected_fingerprint != self.environment.fingerprint:
            return self._environment_result(
                spec,
                artifact,
                source,
                "project-fingerprint-mismatch",
                f"frozen formal contract expects project {expected_fingerprint}, configured project is {self.environment.fingerprint}",
            )
        if self.environment.lock.toolchain.strip() != spec.toolchain.strip():
            return self._environment_result(
                spec,
                artifact,
                source,
                "project-toolchain-mismatch",
                "formal contract toolchain does not match frozen Lean project toolchain",
            )

        expected_version = self._expected_version(spec.toolchain)
        if expected_version is None:
            return self._environment_result(
                spec,
                artifact,
                source,
                "unparseable-toolchain",
                "could not parse frozen Lean toolchain version",
            )

        check = ProjectCheckResult(
            formal_spec_id=spec.id,
            formal_artifact_id=artifact.id,
            project_fingerprint=self.environment.fingerprint,
            passed=False,
        )
        try:
            with self.environment.materialize(workspace) as project:
                generated = project / f"{self.GENERATED_MODULE}.lean"
                generated.write_text(source, encoding="utf-8")

                version_command = [*self.environment.lake_command, "env", "lean", "--version"]
                version = subprocess.run(
                    version_command,
                    cwd=project,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                version_text = self._combined(version.stdout, version.stderr)
                version_match = self._VERSION_RE.search(version_text)
                detected = version_match.group("version") if version_match else None
                if version.returncode != 0 or detected != expected_version:
                    check.gate_reason = "toolchain-version-mismatch"
                    check.compile_command = version_command
                    check.compile_exit_code = version.returncode
                    check.compile_stdout = version.stdout
                    check.compile_stderr = version.stderr
                    self._record(workspace, check)
                    return self._environment_result(
                        spec,
                        artifact,
                        source,
                        "toolchain-version-mismatch",
                        f"expected Lean {expected_version}, detected {detected}",
                    )

                build_command = [*self.environment.lake_command, "build", *self.environment.build_targets]
                build = subprocess.run(
                    build_command,
                    cwd=project,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                check.build_command = build_command
                check.build_exit_code = build.returncode
                check.build_stdout = build.stdout
                check.build_stderr = build.stderr
                if build.returncode != 0:
                    check.gate_reason = "project-build-failed"
                    self._record(workspace, check)
                    return self._environment_result(
                        spec,
                        artifact,
                        source,
                        "project-build-failed",
                        "frozen Lake project failed to build before generated theorem checking",
                    )

                output_dir = project / ".lake" / "build" / "lib" / "lean"
                output_dir.mkdir(parents=True, exist_ok=True)
                output = output_dir / f"{self.GENERATED_MODULE}.olean"
                compile_command = [
                    *self.environment.lake_command,
                    "env",
                    "lean",
                    "-o",
                    str(output.relative_to(project)),
                    generated.name,
                ]
                compiled = subprocess.run(
                    compile_command,
                    cwd=project,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                check.compile_command = compile_command
                check.compile_exit_code = compiled.returncode
                check.compile_stdout = compiled.stdout
                check.compile_stderr = compiled.stderr
                combined = self._combined(compiled.stdout, compiled.stderr)
                diagnostics = self._parse_diagnostics(combined)
                has_error = any(item.severity == "error" for item in diagnostics)
                if compiled.returncode != 0 or has_error:
                    check.gate_reason = "lean-exit-nonzero" if compiled.returncode != 0 else "lean-reported-error"
                    self._record(workspace, check)
                    result = KernelResult(
                        formal_artifact_id=artifact.id,
                        passed=False,
                        status="kernel_rejected",
                        command=compile_command,
                        expected_toolchain=spec.toolchain,
                        detected_version=detected,
                        exit_code=compiled.returncode,
                        stdout=compiled.stdout,
                        stderr=compiled.stderr,
                        diagnostics=diagnostics,
                        gate_reason=check.gate_reason,
                        source_sha256=digest,
                    )
                    return result, source

                axioms, axiom_error = self._parse_axioms(combined)
                if axiom_error is not None or axioms is None:
                    check.gate_reason = axiom_error or "axiom-audit-unrecognized"
                    self._record(workspace, check)
                    return self._gate_failure(
                        spec,
                        artifact,
                        source,
                        check.gate_reason,
                        "Lean succeeded but ResearchEvolve could not audit #print axioms output",
                        detected_version=detected,
                        exit_code=compiled.returncode,
                        stdout=compiled.stdout,
                        stderr=compiled.stderr,
                    )
                disallowed = [axiom for axiom in axioms if axiom not in spec.allowed_axioms]
                if disallowed:
                    check.gate_reason = "disallowed-axioms"
                    self._record(workspace, check)
                    return self._gate_failure(
                        spec,
                        artifact,
                        source,
                        "disallowed-axioms",
                        f"Lean theorem depends on disallowed axioms: {disallowed}",
                        detected_version=detected,
                        exit_code=compiled.returncode,
                        stdout=compiled.stdout,
                        stderr=compiled.stderr,
                        axioms=axioms,
                    )

                checker_command = [
                    *self.environment.lake_command,
                    "env",
                    "leanchecker",
                    "--fresh",
                    self.GENERATED_MODULE,
                ]
                checker = subprocess.run(
                    checker_command,
                    cwd=project,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                check.checker_command = checker_command
                check.checker_exit_code = checker.returncode
                check.checker_stdout = checker.stdout
                check.checker_stderr = checker.stderr
                if checker.returncode != 0:
                    check.gate_reason = "fresh-checker-failed"
                    self._record(workspace, check)
                    result = KernelResult(
                        formal_artifact_id=artifact.id,
                        passed=False,
                        status="kernel_rejected",
                        command=checker_command,
                        expected_toolchain=spec.toolchain,
                        detected_version=detected,
                        exit_code=checker.returncode,
                        stdout=self._combined(compiled.stdout, checker.stdout),
                        stderr=self._combined(compiled.stderr, checker.stderr),
                        diagnostics=self._parse_diagnostics(self._combined(checker.stdout, checker.stderr)),
                        axioms=axioms,
                        gate_reason="fresh-checker-failed",
                        source_sha256=digest,
                    )
                    return result, source

                check.passed = True
                check.gate_reason = ""
                self._record(workspace, check)
                result = KernelResult(
                    formal_artifact_id=artifact.id,
                    passed=True,
                    status="formal_verified",
                    command=checker_command,
                    expected_toolchain=spec.toolchain,
                    detected_version=detected,
                    exit_code=checker.returncode,
                    stdout=self._combined(
                        "[lake build]",
                        build.stdout,
                        "[lean compile + axiom audit]",
                        compiled.stdout,
                        "[leanchecker --fresh]",
                        checker.stdout,
                    ),
                    stderr=self._combined(build.stderr, compiled.stderr, checker.stderr),
                    diagnostics=diagnostics,
                    axioms=axioms,
                    gate_reason="",
                    source_sha256=digest,
                )
                return result, source
        except FileNotFoundError as exc:
            check.gate_reason = "project-command-not-found"
            self._record(workspace, check)
            return self._environment_result(spec, artifact, source, "project-command-not-found", str(exc))
        except subprocess.TimeoutExpired as exc:
            check.gate_reason = "project-check-timeout"
            self._record(workspace, check)
            result = KernelResult(
                formal_artifact_id=artifact.id,
                passed=False,
                status="kernel_rejected",
                command=[*self.environment.lake_command],
                expected_toolchain=spec.toolchain,
                detected_version=expected_version,
                exit_code=None,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                diagnostics=[LeanDiagnostic(severity="error", message="project build/kernel/fresh checker timed out")],
                gate_reason="project-check-timeout",
                source_sha256=digest,
            )
            return result, source
        except ValueError as exc:
            check.gate_reason = "project-lock-validation-failed"
            self._record(workspace, check)
            return self._environment_result(spec, artifact, source, "project-lock-validation-failed", str(exc))
