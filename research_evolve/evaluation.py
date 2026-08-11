from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class EvaluationResult:
    valid: bool
    score: float | None
    metrics: dict[str, float] = field(default_factory=dict)
    behavior: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvaluationResult":
        if "valid" not in data:
            raise ValueError("evaluator result must contain 'valid'")
        score = data.get("score")
        if data["valid"] and score is None:
            raise ValueError("valid evaluator result must contain 'score'")
        return cls(
            valid=bool(data["valid"]),
            score=None if score is None else float(score),
            metrics={k: float(v) for k, v in data.get("metrics", {}).items()},
            behavior=dict(data.get("behavior", {})),
            diagnostics=dict(data.get("diagnostics", {})),
        )


class HiddenEvaluator:
    """Run an evaluator as a separate process over a narrow JSON protocol.

    v0.1 provides a process boundary and a deployment contract, not a hardened
    security sandbox. For untrusted agents, mount the evaluator in a separate
    container/VM that the agent cannot read and expose only this protocol.
    """

    def __init__(self, evaluator_path: str | Path, timeout_seconds: float = 30.0) -> None:
        self.evaluator_path = Path(evaluator_path)
        self.timeout_seconds = timeout_seconds
        if not self.evaluator_path.is_file():
            raise FileNotFoundError(self.evaluator_path)

    def evaluate(self, payload: dict[str, Any]) -> EvaluationResult:
        completed = subprocess.run(
            [sys.executable, str(self.evaluator_path)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            return EvaluationResult(
                valid=False,
                score=None,
                diagnostics={
                    "evaluator_error": completed.stderr.strip() or f"exit={completed.returncode}",
                },
            )
        try:
            raw = json.loads(completed.stdout)
            return EvaluationResult.from_dict(raw)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return EvaluationResult(
                valid=False,
                score=None,
                diagnostics={"protocol_error": str(exc), "stdout": completed.stdout[-2000:]},
            )
