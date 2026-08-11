from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from .proofs import (
    LemmaSpec,
    ProofArtifact,
    ProofPlan,
    ProofReview,
    ProofSpec,
    VerificationIssue,
)


@dataclass(slots=True)
class ProofContext:
    problem: str
    generation: int
    proof_spec: dict[str, Any]
    conjecture: dict[str, Any]
    evidence_candidates: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    prior_proofs: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProofPlanner(Protocol):
    @property
    def name(self) -> str: ...

    def plan(self, context: ProofContext, proof_spec: ProofSpec) -> ProofPlan: ...


class Prover(Protocol):
    @property
    def name(self) -> str: ...

    def prove(self, context: ProofContext, proof_spec: ProofSpec, plan: ProofPlan) -> ProofArtifact: ...


class ProofVerifier(Protocol):
    @property
    def name(self) -> str: ...

    def verify(
        self,
        context: ProofContext,
        proof_spec: ProofSpec,
        plan: ProofPlan,
        artifact: ProofArtifact,
    ) -> ProofReview: ...


class _CommandActor:
    def __init__(self, command: str | Sequence[str], timeout_seconds: float = 60.0, role: str = "actor") -> None:
        if isinstance(command, str):
            parsed = shlex.split(command)
        else:
            parsed = [str(item) for item in command]
        if not parsed:
            raise ValueError(f"{role} command must not be empty")
        self.command = parsed
        self.timeout_seconds = float(timeout_seconds)
        self.role = role
        self._implementation_fingerprint = self._build_implementation_fingerprint()
        self._identity = f"command:{self.role}:{Path(self.command[0]).name}:{self._implementation_fingerprint}"

    def _build_implementation_fingerprint(self) -> str:
        files: list[dict[str, str]] = []
        for argument in self.command[1:]:
            path = Path(argument)
            if path.is_file():
                files.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        identity_input = {"argv": self.command, "files": files}
        return hashlib.sha256(
            json.dumps(identity_input, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]

    @property
    def name(self) -> str:
        return self._identity

    @property
    def independence_key(self) -> str:
        """Role-independent implementation identity used to prevent self-verification."""

        return f"command:{Path(self.command[0]).name}:{self._implementation_fingerprint}"

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


class CommandProofPlanner(_CommandActor):
    def __init__(self, command: str | Sequence[str], timeout_seconds: float = 60.0) -> None:
        super().__init__(command, timeout_seconds, role="proof-planner")

    def plan(self, context: ProofContext, proof_spec: ProofSpec) -> ProofPlan:
        request = {
            "schema_version": 1,
            "action": "plan_proof",
            "context": context.to_dict(),
            "proof_spec": proof_spec.to_dict(),
            "response_contract": {
                "strategy": "proof strategy in natural language",
                "lemmas": [
                    {
                        "label": "unique short label",
                        "statement": "lemma statement",
                        "depends_on": ["earlier lemma labels"],
                        "role": "supporting | bridge | final",
                        "metadata": {},
                    }
                ],
                "metadata": {},
            },
            "integrity_policy": (
                "Do not change the target statement or assumptions. Decompose only the supplied ProofSpec."
            ),
        }
        raw = self._invoke(request)
        if not isinstance(raw, dict):
            raise RuntimeError("proof planner response must be a JSON object")
        lemmas_raw = raw.get("lemmas")
        if not isinstance(lemmas_raw, list):
            raise RuntimeError("proof planner response requires a lemmas list")
        return ProofPlan(
            proof_spec_id=proof_spec.id,
            strategy=str(raw.get("strategy", "")),
            lemmas=[LemmaSpec.from_dict(item) for item in lemmas_raw if isinstance(item, dict)],
            metadata=dict(raw.get("metadata", {})),
        )


class CommandProver(_CommandActor):
    def __init__(self, command: str | Sequence[str], timeout_seconds: float = 60.0) -> None:
        super().__init__(command, timeout_seconds, role="prover")

    def prove(self, context: ProofContext, proof_spec: ProofSpec, plan: ProofPlan) -> ProofArtifact:
        request = {
            "schema_version": 1,
            "action": "prove",
            "context": context.to_dict(),
            "proof_spec": proof_spec.to_dict(),
            "proof_plan": plan.to_dict(),
            "response_contract": {
                "lemma_arguments": {"lemma_label": "complete argument for that lemma"},
                "final_argument": "argument combining lemmas into the target statement",
                "assumptions_used": [],
                "metadata": {},
            },
            "integrity_policy": (
                "Prove exactly the supplied ProofSpec. Do not weaken the target, add hidden assumptions, "
                "or claim that finite experiments constitute a proof."
            ),
        }
        raw = self._invoke(request)
        if not isinstance(raw, dict):
            raise RuntimeError("prover response must be a JSON object")
        lemma_arguments = raw.get("lemma_arguments")
        if not isinstance(lemma_arguments, dict):
            raise RuntimeError("prover response requires lemma_arguments object")
        return ProofArtifact(
            proof_spec_id=proof_spec.id,
            proof_plan_id=plan.id,
            lemma_arguments={str(key): str(value) for key, value in lemma_arguments.items()},
            final_argument=str(raw.get("final_argument", "")),
            assumptions_used=[str(item) for item in raw.get("assumptions_used", [])],
            metadata=dict(raw.get("metadata", {})),
        )


class CommandProofVerifier(_CommandActor):
    def __init__(self, command: str | Sequence[str], timeout_seconds: float = 60.0) -> None:
        super().__init__(command, timeout_seconds, role="proof-verifier")

    def verify(
        self,
        context: ProofContext,
        proof_spec: ProofSpec,
        plan: ProofPlan,
        artifact: ProofArtifact,
    ) -> ProofReview:
        request = {
            "schema_version": 1,
            "action": "adversarial_verify",
            "context": context.to_dict(),
            "proof_spec": proof_spec.to_dict(),
            "proof_plan": plan.to_dict(),
            "proof_artifact": artifact.to_dict(),
            "response_contract": {
                "decision": "verified | rejected | inconclusive",
                "issues": [
                    {
                        "severity": "error | warning | note",
                        "code": "short machine-readable code",
                        "message": "specific verification finding",
                        "lemma_label": "optional lemma label or null",
                        "metadata": {},
                    }
                ],
                "confidence": "0..1",
                "adversarial_notes": "attempts to find gaps, hidden assumptions, circularity or counterexamples",
                "metadata": {},
            },
            "verification_policy": (
                "Act independently and adversarially. Check statement integrity, every lemma dependency, "
                "hidden assumptions, circular reasoning, quantifier/scope changes, and whether the final "
                "argument actually establishes the exact ProofSpec. A natural-language verification is not formal proof."
            ),
        }
        raw = self._invoke(request)
        if not isinstance(raw, dict):
            raise RuntimeError("proof verifier response must be a JSON object")
        issues_raw = raw.get("issues", [])
        if not isinstance(issues_raw, list):
            raise RuntimeError("proof verifier issues must be a list")
        issues = [VerificationIssue.from_dict(item) for item in issues_raw if isinstance(item, dict)]
        review = ProofReview(
            proof_artifact_id=artifact.id,
            verifier=self.name,
            decision=str(raw.get("decision", "inconclusive")),  # type: ignore[arg-type]
            issues=issues,
            confidence=float(raw.get("confidence", 0.5)),
            adversarial_notes=str(raw.get("adversarial_notes", "")),
            metadata=dict(raw.get("metadata", {})),
        )
        review.validate()
        return review
