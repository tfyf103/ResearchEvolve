from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from .conjectures import Conjecture


@dataclass(slots=True)
class ConjectureContext:
    problem: str
    generation: int
    objectives: list[dict[str, Any]]
    constraints: list[dict[str, Any]]
    observations: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    conjectures: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Conjecturer(Protocol):
    @property
    def name(self) -> str: ...

    def propose(self, context: ConjectureContext, count: int) -> list[Conjecture]: ...


class CommandConjecturer:
    """External conjecture generator over a strict JSON stdin/stdout contract."""

    def __init__(self, command: str | Sequence[str], timeout_seconds: float = 60.0) -> None:
        if isinstance(command, str):
            parsed = shlex.split(command)
        else:
            parsed = [str(item) for item in command]
        if not parsed:
            raise ValueError("conjecturer command must not be empty")
        self.command = parsed
        self.timeout_seconds = float(timeout_seconds)
        self._identity = self._build_identity()

    def _build_identity(self) -> str:
        files: list[dict[str, str]] = []
        for argument in self.command[1:]:
            path = Path(argument)
            if not path.is_file():
                continue
            files.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        identity_input = {"argv": self.command, "files": files}
        digest = hashlib.sha256(
            json.dumps(identity_input, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        return f"command:{Path(self.command[0]).name}:{digest}"

    @property
    def name(self) -> str:
        return self._identity

    @staticmethod
    def _sanitize_external_conjecture(item: dict[str, Any]) -> dict[str, Any]:
        """Prevent an external model from controlling internal IDs, status, or ordering timestamps."""

        sanitized = dict(item)
        external_id = sanitized.pop("id", None)
        external_created_at = sanitized.pop("created_at", None)
        sanitized.pop("status", None)
        metadata = dict(sanitized.get("metadata", {}))
        if external_id is not None:
            metadata["external_conjecture_id"] = str(external_id)
        if external_created_at is not None:
            metadata["external_created_at"] = str(external_created_at)
        sanitized["metadata"] = metadata
        return sanitized

    def propose(self, context: ConjectureContext, count: int) -> list[Conjecture]:
        if count < 1:
            return []
        request = {
            "schema_version": 1,
            "count": count,
            "context": context.to_dict(),
            "response_contract": {
                "statement": "natural-language conjecture statement",
                "predicate": {
                    "left": {
                        "source": "score | payload | metrics | behavior",
                        "key": "top-level or dotted path; omit for score",
                        "scale": 1.0,
                        "offset": 0.0,
                    },
                    "operator": "lt | le | gt | ge | eq | ne",
                    "right_constant": "JSON scalar OR null when using right_ref",
                    "right_ref": "optional ValueRef object",
                },
                "observation_ids": [],
                "evidence_candidate_ids": [],
                "parent_conjecture_ids": [],
                "rationale": "why observations suggest the conjecture",
                "confidence": "0..1",
                "metadata": {},
            },
            "truth_policy": (
                "Do not claim proof. Emit only predicates testable against the supplied candidate schema. "
                "ResearchEvolve will independently search for counterexamples."
            ),
        }
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
            raise RuntimeError(f"conjecturer command failed: {detail}")
        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"conjecturer returned invalid JSON: {exc}") from exc

        items = raw.get("conjectures") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            raise RuntimeError("conjecturer response must be a list or {'conjectures': [...]} object")

        conjectures: list[Conjecture] = []
        for item in items[:count]:
            if not isinstance(item, dict):
                raise RuntimeError("each conjecturer item must be a JSON object")
            conjectures.append(Conjecture.from_dict(self._sanitize_external_conjecture(item)))
        return conjectures
