from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from .formal import FormalArtifact, FormalizationSpec, KernelResult


@dataclass(slots=True)
class FormalContext:
    problem: str
    generation: int
    formal_spec: dict[str, Any]
    proof_spec: dict[str, Any]
    proof_artifact: dict[str, Any]
    proof_review: dict[str, Any]
    conjecture: dict[str, Any]
    observations: list[dict[str, Any]] = field(default_factory=list)
    evidence_candidates: list[dict[str, Any]] = field(default_factory=list)
    previous_kernel_runs: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Formalizer(Protocol):
    @property
    def name(self) -> str: ...

    def formalize(self, context: FormalContext, spec: FormalizationSpec) -> FormalArtifact: ...


class FormalRepairer(Protocol):
    @property
    def name(self) -> str: ...

    def repair(
        self,
        context: FormalContext,
        spec: FormalizationSpec,
        artifact: FormalArtifact,
        kernel_result: KernelResult,
        attempt: int,
    ) -> FormalArtifact: ...


class _CommandFormalActor:
    def __init__(self, command: str | Sequence[str], timeout_seconds: float = 60.0, role: str = "formal-actor") -> None:
        if isinstance(command, str):
            parsed = shlex.split(command)
        else:
            parsed = [str(item) for item in command]
        if not parsed:
            raise ValueError(f"{role} command must not be empty")
        if timeout_seconds <= 0:
            raise ValueError(f"{role} timeout must be positive")
        self.command = parsed
        self.timeout_seconds = float(timeout_seconds)
        self.role = role
        self._identity = self._build_identity()

    def _build_identity(self) -> str:
        files: list[dict[str, str]] = []
        for argument in self.command[1:]:
            path = Path(argument)
            if path.is_file():
                files.append({"sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        identity_input = {"argv": self.command, "files": files}
        digest = hashlib.sha256(
            json.dumps(identity_input, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        return f"command:{self.role}:{Path(self.command[0]).name}:{digest}"

    @property
    def name(self) -> str:
        return self._identity

    def _invoke(self, request: dict[str, Any]) -> Any:
        completed = subprocess.run(
            self.command,
            input=json.dumps(request, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or f"exit={completed.returncode}"
            raise RuntimeError(f"{self.role} command failed: {detail}")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{self.role} returned invalid JSON: {exc}") from exc

    @staticmethod
    def _artifact_from_response(raw: Any, spec: FormalizationSpec, *, attempt: int, parent_id: str | None = None) -> FormalArtifact:
        if not isinstance(raw, dict):
            raise RuntimeError("formal actor response must be a JSON object")
        artifact = FormalArtifact(
            formal_spec_id=spec.id,
            proof_term=str(raw.get("proof_term", "")),
            helper_source=str(raw.get("helper_source", "")),
            attempt=attempt,
            parent_artifact_id=parent_id,
            metadata=dict(raw.get("metadata", {})),
        )
        artifact.validate()
        return artifact


class CommandFormalizer(_CommandFormalActor):
    def __init__(self, command: str | Sequence[str], timeout_seconds: float = 60.0) -> None:
        super().__init__(command, timeout_seconds, role="formalizer")

    def formalize(self, context: FormalContext, spec: FormalizationSpec) -> FormalArtifact:
        request = {
            "schema_version": 1,
            "action": "formalize_lean4",
            "context": context.to_dict(),
            "formal_spec": spec.to_dict(),
            "response_contract": {
                "proof_term": "Lean proof term/body only; do not repeat or modify theorem_signature",
                "helper_source": "optional helper declarations only; may be empty",
                "metadata": {},
            },
            "integrity_policy": (
                "The theorem signature, theorem name, imports, and toolchain are frozen. "
                "Do not emit sorry/admit/axiom/unsafe/extern/opaque. Empirical evidence is not a proof."
            ),
        }
        raw = self._invoke(request)
        return self._artifact_from_response(raw, spec, attempt=0)


class CommandFormalRepairer(_CommandFormalActor):
    def __init__(self, command: str | Sequence[str], timeout_seconds: float = 60.0) -> None:
        super().__init__(command, timeout_seconds, role="formal-repairer")

    def repair(
        self,
        context: FormalContext,
        spec: FormalizationSpec,
        artifact: FormalArtifact,
        kernel_result: KernelResult,
        attempt: int,
    ) -> FormalArtifact:
        request = {
            "schema_version": 1,
            "action": "repair_lean4",
            "context": context.to_dict(),
            "formal_spec": spec.to_dict(),
            "previous_artifact": artifact.to_dict(),
            "kernel_result": kernel_result.to_dict(),
            "attempt": attempt,
            "response_contract": {
                "proof_term": "replacement Lean proof term/body only",
                "helper_source": "replacement optional helper declarations",
                "metadata": {},
            },
            "integrity_policy": (
                "Repair only the proof term/helper declarations. The frozen theorem signature/imports/toolchain cannot change. "
                "Do not use sorry/admit/axiom/unsafe/extern/opaque."
            ),
        }
        raw = self._invoke(request)
        return self._artifact_from_response(raw, spec, attempt=attempt, parent_id=artifact.id)
