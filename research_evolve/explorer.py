from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from .ideas import ResearchProposal


@dataclass(slots=True)
class ResearchContext:
    """Compact research state exposed to an Explorer/LLM process."""

    problem: str
    generation: int
    objectives: list[dict[str, Any]]
    constraints: list[dict[str, Any]]
    candidates: list[dict[str, Any]] = field(default_factory=list)
    pareto: list[dict[str, Any]] = field(default_factory=list)
    feedback: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Explorer(Protocol):
    """Provider-neutral interface for LLM or heuristic research proposal generators."""

    @property
    def name(self) -> str: ...

    def propose(self, context: ResearchContext, count: int) -> list[ResearchProposal]: ...


class CommandExplorer:
    """Run an external Explorer over a strict JSON stdin/stdout protocol.

    The command can wrap OpenAI, Claude, Gemini, a local model, or a deterministic
    test agent. ResearchEvolve never gives it evaluator internals; it only receives
    summarized research state and must return structured semantic proposals.
    """

    def __init__(self, command: str | Sequence[str], timeout_seconds: float = 60.0) -> None:
        if isinstance(command, str):
            parsed = shlex.split(command)
        else:
            parsed = [str(item) for item in command]
        if not parsed:
            raise ValueError("explorer command must not be empty")
        self.command = parsed
        self.timeout_seconds = float(timeout_seconds)
        self._identity = self._build_identity()

    def _build_identity(self) -> str:
        # Hash argv and any directly referenced files, but never persist raw arguments
        # in the manifest because commands may contain sensitive provider parameters.
        files: list[dict[str, str]] = []
        for argument in self.command[1:]:
            path = Path(argument)
            if not path.is_file():
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            files.append({"path": str(path), "sha256": digest})
        identity_input = {"argv": self.command, "files": files}
        digest = hashlib.sha256(
            json.dumps(identity_input, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        return f"command:{Path(self.command[0]).name}:{digest}"

    @property
    def name(self) -> str:
        return self._identity

    @staticmethod
    def _sanitize_external_proposal(item: dict[str, Any]) -> dict[str, Any]:
        """Prevent an external model from choosing internal Research Graph IDs."""

        sanitized = dict(item)
        external_proposal_id = sanitized.pop("id", None)
        genome = dict(sanitized.get("genome") or {})
        external_idea_id = genome.pop("id", None)
        sanitized["genome"] = genome
        metadata = dict(sanitized.get("metadata", {}))
        if external_proposal_id is not None:
            metadata["external_proposal_id"] = str(external_proposal_id)
        if external_idea_id is not None:
            metadata["external_idea_id"] = str(external_idea_id)
        sanitized["metadata"] = metadata
        return sanitized

    def propose(self, context: ResearchContext, count: int) -> list[ResearchProposal]:
        if count < 1:
            return []
        request = {
            "schema_version": 1,
            "count": count,
            "context": context.to_dict(),
            "response_contract": {
                "kind": "semantic_mutation | semantic_crossover",
                "parent_ids": "one id for mutation; two ids for crossover",
                "patch": {"set": {}, "delete": [], "append": {}},
                "inherit_from_secondary": "top-level fields copied from parent 2 for crossover",
                "genome": {
                    "representation": "string",
                    "construction": "string",
                    "mechanisms": [],
                    "invariants": [],
                    "assumptions": [],
                    "tags": [],
                    "traits": {},
                    "notes": "string",
                },
                "rationale": "string",
                "expected_effects": {},
                "confidence": "0..1",
                "metadata": {},
            },
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
            raise RuntimeError(f"explorer command failed: {detail}")
        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"explorer returned invalid JSON: {exc}") from exc

        items = raw.get("proposals") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            raise RuntimeError("explorer response must be a list or {'proposals': [...]} object")

        proposals: list[ResearchProposal] = []
        for item in items[:count]:
            if not isinstance(item, dict):
                raise RuntimeError("each explorer proposal must be a JSON object")
            proposals.append(ResearchProposal.from_dict(self._sanitize_external_proposal(item)))
        return proposals
