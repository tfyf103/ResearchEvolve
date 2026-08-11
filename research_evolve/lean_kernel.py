from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

from .formal import FormalArtifact, FormalizationSpec, KernelResult, LeanDiagnostic


class LeanKernel:
    """Thin Lean compiler/kernel gate for v0.6.

    `formal_verified` requires a frozen target, conservative source gate, exact
    toolchain match, successful Lean elaboration/kernel checking, and an audited
    `#print axioms` result containing only the FormalizationSpec allowlist.
    """

    _FORBIDDEN_WORDS = (
        "sorry",
        "admit",
        "axiom",
        "unsafe",
        "extern",
        "opaque",
        "run_tac",
        "elab",
        "macro",
        "syntax",
    )
    _FORBIDDEN_FRAGMENTS = ("#eval", "#run")
    _DIAGNOSTIC_RE = re.compile(
        r"^(?P<file>.*?):(?P<line>\d+):(?P<column>\d+):\s*(?P<severity>error|warning|info):\s*(?P<message>.*)$"
    )
    _VERSION_RE = re.compile(r"Lean\s*\(version\s+(?P<version>\d+\.\d+\.\d+)")
    _AXIOM_RE = re.compile(r"depends on axioms:\s*\[(?P<axioms>[^\]]*)\]")

    def __init__(
        self,
        command: str | Sequence[str] = "lean",
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        if isinstance(command, str):
            parsed = shlex.split(command)
        else:
            parsed = [str(item) for item in command]
        if not parsed:
            raise ValueError("Lean command must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("Lean timeout must be positive")
        self.command = parsed
        self.timeout_seconds = float(timeout_seconds)

    @property
    def name(self) -> str:
        digest = hashlib.sha256("\0".join(self.command).encode("utf-8")).hexdigest()[:16]
        return f"lean-kernel:{Path(self.command[0]).name}:{digest}"

    @staticmethod
    def _expected_version(toolchain: str) -> str | None:
        match = re.search(r"(?:^|:)v?(\d+\.\d+\.\d+)$", toolchain.strip())
        return match.group(1) if match else None

    @classmethod
    def _forbidden_token(cls, source: str) -> str | None:
        for token in cls._FORBIDDEN_WORDS:
            if re.search(rf"\b{re.escape(token)}\b", source):
                return token
        for fragment in cls._FORBIDDEN_FRAGMENTS:
            if fragment in source:
                return fragment
        return None

    @classmethod
    def _parse_diagnostics(cls, text: str) -> list[LeanDiagnostic]:
        diagnostics: list[LeanDiagnostic] = []
        for raw in text.splitlines():
            match = cls._DIAGNOSTIC_RE.match(raw.strip())
            if match:
                diagnostics.append(
                    LeanDiagnostic(
                        severity=match.group("severity"),  # type: ignore[arg-type]
                        message=match.group("message").strip(),
                        line=int(match.group("line")),
                        column=int(match.group("column")),
                        raw=raw,
                    )
                )
            elif "error:" in raw.lower():
                diagnostics.append(LeanDiagnostic(severity="error", message=raw.strip(), raw=raw))
            elif "warning:" in raw.lower():
                diagnostics.append(LeanDiagnostic(severity="warning", message=raw.strip(), raw=raw))
        return diagnostics

    @classmethod
    def _parse_axioms(cls, text: str) -> tuple[list[str] | None, str | None]:
        if "does not depend on any axioms" in text:
            return [], None
        match = cls._AXIOM_RE.search(text)
        if match is None:
            return None, "axiom-audit-unrecognized"
        raw = match.group("axioms").strip()
        if not raw:
            return [], None
        return [item.strip() for item in raw.split(",") if item.strip()], None

    def _detect_version(self) -> tuple[str | None, str, str, int | None, str | None]:
        try:
            completed = subprocess.run(
                [*self.command, "--version"],
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            return None, "", str(exc), None, "lean-command-not-found"
        except subprocess.TimeoutExpired as exc:
            return None, exc.stdout or "", exc.stderr or "", None, "lean-version-timeout"
        text = f"{completed.stdout}\n{completed.stderr}"
        match = self._VERSION_RE.search(text)
        version = match.group("version") if match else None
        if completed.returncode != 0:
            return version, completed.stdout, completed.stderr, completed.returncode, "lean-version-command-failed"
        if version is None:
            return None, completed.stdout, completed.stderr, completed.returncode, "lean-version-unrecognized"
        return version, completed.stdout, completed.stderr, completed.returncode, None

    def _gate_failure(
        self,
        spec: FormalizationSpec,
        artifact: FormalArtifact,
        source: str,
        reason: str,
        message: str,
        *,
        detected_version: str | None = None,
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
        axioms: list[str] | None = None,
    ) -> tuple[KernelResult, str]:
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        return (
            KernelResult(
                formal_artifact_id=artifact.id,
                passed=False,
                status="kernel_rejected",
                command=list(self.command),
                expected_toolchain=spec.toolchain,
                detected_version=detected_version,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                diagnostics=[LeanDiagnostic(severity="error", message=message)],
                axioms=list(axioms or []),
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
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()

        if artifact.helper_source.strip():
            return self._gate_failure(
                spec,
                artifact,
                source,
                "untrusted-top-level-helper",
                "v0.6 forbids model-supplied top-level helper_source; use local proof steps inside proof_term",
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

        expected = self._expected_version(spec.toolchain)
        if expected is None:
            result = KernelResult(
                formal_artifact_id=artifact.id,
                passed=False,
                status="environment_error",
                command=list(self.command),
                expected_toolchain=spec.toolchain,
                detected_version=None,
                exit_code=None,
                diagnostics=[LeanDiagnostic(severity="error", message="could not parse frozen Lean toolchain version")],
                gate_reason="unparseable-toolchain",
                source_sha256=digest,
            )
            return result, source

        detected, version_stdout, version_stderr, version_code, version_error = self._detect_version()
        if version_error is not None or detected != expected:
            message = (
                f"Lean version mismatch: expected {expected}, detected {detected}"
                if version_error is None
                else f"Lean environment check failed: {version_error}"
            )
            result = KernelResult(
                formal_artifact_id=artifact.id,
                passed=False,
                status="environment_error",
                command=list(self.command),
                expected_toolchain=spec.toolchain,
                detected_version=detected,
                exit_code=version_code,
                stdout=version_stdout,
                stderr=version_stderr,
                diagnostics=[LeanDiagnostic(severity="error", message=message)],
                gate_reason="toolchain-version-mismatch" if version_error is None else version_error,
                source_sha256=digest,
            )
            return result, source

        root = Path(workspace)
        root.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix="researchevolve-lean-", dir=root) as temp_dir:
                source_path = Path(temp_dir) / "Main.lean"
                source_path.write_text(source, encoding="utf-8")
                completed = subprocess.run(
                    [*self.command, str(source_path)],
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                    cwd=temp_dir,
                )
        except FileNotFoundError as exc:
            result = KernelResult(
                formal_artifact_id=artifact.id,
                passed=False,
                status="environment_error",
                command=list(self.command),
                expected_toolchain=spec.toolchain,
                detected_version=detected,
                exit_code=None,
                stderr=str(exc),
                diagnostics=[LeanDiagnostic(severity="error", message=str(exc))],
                gate_reason="lean-command-not-found",
                source_sha256=digest,
            )
            return result, source
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            result = KernelResult(
                formal_artifact_id=artifact.id,
                passed=False,
                status="kernel_rejected",
                command=list(self.command),
                expected_toolchain=spec.toolchain,
                detected_version=detected,
                exit_code=None,
                stdout=stdout,
                stderr=stderr,
                diagnostics=self._parse_diagnostics(f"{stdout}\n{stderr}"),
                gate_reason="lean-kernel-timeout",
                source_sha256=digest,
            )
            return result, source

        combined = f"{completed.stdout}\n{completed.stderr}"
        diagnostics = self._parse_diagnostics(combined)
        has_error = any(item.severity == "error" for item in diagnostics)
        if completed.returncode != 0 or has_error:
            reason = "lean-exit-nonzero" if completed.returncode != 0 else "lean-reported-error"
            result = KernelResult(
                formal_artifact_id=artifact.id,
                passed=False,
                status="kernel_rejected",
                command=list(self.command),
                expected_toolchain=spec.toolchain,
                detected_version=detected,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                diagnostics=diagnostics,
                gate_reason=reason,
                source_sha256=digest,
            )
            return result, source

        axioms, axiom_error = self._parse_axioms(combined)
        if axiom_error is not None or axioms is None:
            return self._gate_failure(
                spec,
                artifact,
                source,
                axiom_error or "axiom-audit-unrecognized",
                "Lean succeeded but ResearchEvolve could not audit #print axioms output",
                detected_version=detected,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )

        disallowed = [axiom for axiom in axioms if axiom not in spec.allowed_axioms]
        if disallowed:
            return self._gate_failure(
                spec,
                artifact,
                source,
                "disallowed-axioms",
                f"Lean theorem depends on disallowed axioms: {disallowed}",
                detected_version=detected,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                axioms=axioms,
            )

        result = KernelResult(
            formal_artifact_id=artifact.id,
            passed=True,
            status="formal_verified",
            command=list(self.command),
            expected_toolchain=spec.toolchain,
            detected_version=detected,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            diagnostics=diagnostics,
            axioms=axioms,
            gate_reason="",
            source_sha256=digest,
        )
        return result, source
