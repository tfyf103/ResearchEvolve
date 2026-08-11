from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(slots=True)
class EvaluationResult:
    valid: bool
    score: float | None
    metrics: dict[str, float] = field(default_factory=dict)
    behavior: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, require_score: bool = True) -> "EvaluationResult":
        if "valid" not in data:
            raise ValueError("evaluator result must contain 'valid'")
        score = data.get("score")
        if require_score and data["valid"] and score is None:
            raise ValueError("valid evaluator result must contain 'score'")
        return cls(
            valid=bool(data["valid"]),
            score=None if score is None else float(score),
            metrics={k: float(v) for k, v in data.get("metrics", {}).items()},
            behavior=dict(data.get("behavior", {})),
            diagnostics=dict(data.get("diagnostics", {})),
        )


@dataclass(slots=True, frozen=True)
class EvaluationStage:
    name: str
    path: Path


class HiddenEvaluator:
    """Run one evaluator as a separate process over a narrow JSON protocol.

    The process boundary is an integration contract, not a hardened security
    sandbox. In production, keep private evaluators in a separate grader
    container/VM that the candidate-generating agent cannot inspect.
    """

    def __init__(
        self,
        evaluator_path: str | Path,
        timeout_seconds: float = 30.0,
        *,
        require_score: bool = True,
    ) -> None:
        self.evaluator_path = Path(evaluator_path)
        self.timeout_seconds = timeout_seconds
        self.require_score = require_score
        if not self.evaluator_path.is_file():
            raise FileNotFoundError(self.evaluator_path)

    def evaluate(self, payload: dict[str, Any]) -> EvaluationResult:
        try:
            completed = subprocess.run(
                [sys.executable, str(self.evaluator_path)],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return EvaluationResult(
                valid=False,
                score=None,
                diagnostics={"evaluator_timeout_seconds": self.timeout_seconds},
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
            return EvaluationResult.from_dict(raw, require_score=self.require_score)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return EvaluationResult(
                valid=False,
                score=None,
                diagnostics={"protocol_error": str(exc), "stdout": completed.stdout[-2000:]},
            )


class EvaluatorCascade:
    """Run cheap evaluators first and stop as soon as a candidate is rejected.

    Intermediate stages may omit ``score``. The last stage must return a score
    for a valid candidate. Metrics and behavior dictionaries are merged from
    left to right, so expensive stages can refine earlier estimates.
    """

    def __init__(self, evaluator_paths: Iterable[str | Path], timeout_seconds: float = 30.0) -> None:
        paths = [Path(path) for path in evaluator_paths]
        if not paths:
            raise ValueError("at least one evaluator stage is required")
        self.stages = [EvaluationStage(path.stem, path) for path in paths]
        self.timeout_seconds = timeout_seconds

    def evaluate(self, payload: dict[str, Any]) -> EvaluationResult:
        metrics: dict[str, float] = {}
        behavior: dict[str, Any] = {}
        stage_reports: list[dict[str, Any]] = []
        score: float | None = None

        for index, stage in enumerate(self.stages):
            evaluator = HiddenEvaluator(
                stage.path,
                self.timeout_seconds,
                require_score=index == len(self.stages) - 1,
            )
            result = evaluator.evaluate(payload)
            metrics.update(result.metrics)
            behavior.update(result.behavior)
            if result.score is not None:
                score = result.score
            stage_reports.append(
                {
                    "name": stage.name,
                    "valid": result.valid,
                    "score": result.score,
                    "diagnostics": result.diagnostics,
                }
            )
            if not result.valid:
                return EvaluationResult(
                    valid=False,
                    score=None,
                    metrics=metrics,
                    behavior=behavior,
                    diagnostics={"cascade": stage_reports, "rejected_at": stage.name},
                )

        if score is None:
            return EvaluationResult(
                valid=False,
                score=None,
                metrics=metrics,
                behavior=behavior,
                diagnostics={"cascade": stage_reports, "protocol_error": "final stage did not produce a score"},
            )
        return EvaluationResult(
            valid=True,
            score=score,
            metrics=metrics,
            behavior=behavior,
            diagnostics={"cascade": stage_reports},
        )
