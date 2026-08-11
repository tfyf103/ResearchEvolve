from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

ProposalKind = Literal["semantic_mutation", "semantic_crossover"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class IdeaGenome:
    """Structured semantic description of a mathematical research idea."""

    representation: str = "unspecified"
    construction: str = "unspecified"
    mechanisms: list[str] = field(default_factory=list)
    invariants: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    traits: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    id: str = field(default_factory=lambda: f"idea-{uuid.uuid4().hex}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "IdeaGenome":
        data = dict(data or {})
        return cls(
            id=str(data.get("id") or f"idea-{uuid.uuid4().hex}"),
            representation=str(data.get("representation", "unspecified")),
            construction=str(data.get("construction", "unspecified")),
            mechanisms=[str(item) for item in data.get("mechanisms", [])],
            invariants=[str(item) for item in data.get("invariants", [])],
            assumptions=[str(item) for item in data.get("assumptions", [])],
            tags=[str(item) for item in data.get("tags", [])],
            traits=dict(data.get("traits", {})),
            notes=str(data.get("notes", "")),
        )


@dataclass(slots=True)
class SemanticPatch:
    """Restricted top-level payload edits proposed by an Explorer."""

    set_values: dict[str, Any] = field(default_factory=dict)
    delete_keys: list[str] = field(default_factory=list)
    append_values: dict[str, list[Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "set": copy.deepcopy(self.set_values),
            "delete": list(self.delete_keys),
            "append": copy.deepcopy(self.append_values),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SemanticPatch":
        data = dict(data or {})
        append_values: dict[str, list[Any]] = {}
        for key, value in dict(data.get("append", {})).items():
            if not isinstance(value, list):
                raise ValueError(f"patch.append[{key!r}] must be a list")
            append_values[str(key)] = copy.deepcopy(value)
        return cls(
            set_values=copy.deepcopy(dict(data.get("set", {}))),
            delete_keys=[str(key) for key in data.get("delete", [])],
            append_values=append_values,
        )


@dataclass(slots=True)
class ResearchProposal:
    kind: ProposalKind
    parent_ids: list[str]
    genome: IdeaGenome
    patch: SemanticPatch = field(default_factory=SemanticPatch)
    inherit_from_secondary: list[str] = field(default_factory=list)
    rationale: str = ""
    expected_effects: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"proposal-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utcnow)

    def validate(self) -> None:
        if self.kind == "semantic_mutation" and len(self.parent_ids) != 1:
            raise ValueError("semantic_mutation requires exactly one parent")
        if self.kind == "semantic_crossover" and len(self.parent_ids) != 2:
            raise ValueError("semantic_crossover requires exactly two parents")
        if len(set(self.parent_ids)) != len(self.parent_ids):
            raise ValueError("proposal parent_ids must be distinct")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("proposal confidence must be in [0, 1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "parent_ids": list(self.parent_ids),
            "genome": self.genome.to_dict(),
            "patch": self.patch.to_dict(),
            "inherit_from_secondary": list(self.inherit_from_secondary),
            "rationale": self.rationale,
            "expected_effects": dict(self.expected_effects),
            "confidence": self.confidence,
            "metadata": copy.deepcopy(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchProposal":
        kind = str(data.get("kind", ""))
        if kind not in {"semantic_mutation", "semantic_crossover"}:
            raise ValueError(f"unsupported proposal kind: {kind!r}")
        proposal = cls(
            id=str(data.get("id") or f"proposal-{uuid.uuid4().hex}"),
            kind=kind,  # type: ignore[arg-type]
            parent_ids=[str(item) for item in data.get("parent_ids", [])],
            genome=IdeaGenome.from_dict(data.get("genome")),
            patch=SemanticPatch.from_dict(data.get("patch")),
            inherit_from_secondary=[str(item) for item in data.get("inherit_from_secondary", [])],
            rationale=str(data.get("rationale", "")),
            expected_effects={str(k): str(v) for k, v in dict(data.get("expected_effects", {})).items()},
            confidence=float(data.get("confidence", 0.5)),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data.get("created_at", _utcnow())),
        )
        proposal.validate()
        return proposal


def apply_semantic_patch(payload: dict[str, Any], patch: SemanticPatch) -> dict[str, Any]:
    """Apply a deterministic, auditable top-level semantic patch."""

    result = copy.deepcopy(payload)
    for key in patch.delete_keys:
        result.pop(key, None)
    for key, value in patch.set_values.items():
        result[str(key)] = copy.deepcopy(value)
    for key, values in patch.append_values.items():
        current = result.get(key, [])
        if not isinstance(current, list):
            raise ValueError(f"cannot append to non-list payload field {key!r}")
        result[key] = copy.deepcopy(current) + copy.deepcopy(values)
    return result


def realize_proposal(proposal: ResearchProposal, parents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Turn a semantic mutation/crossover proposal into an evaluator-ready payload."""

    proposal.validate()
    missing = [parent_id for parent_id in proposal.parent_ids if parent_id not in parents]
    if missing:
        raise ValueError(f"proposal references unavailable parents: {missing}")

    primary = copy.deepcopy(parents[proposal.parent_ids[0]])
    if proposal.kind == "semantic_crossover":
        secondary = parents[proposal.parent_ids[1]]
        for key in proposal.inherit_from_secondary:
            if key in secondary:
                primary[key] = copy.deepcopy(secondary[key])
            else:
                primary.pop(key, None)
    return apply_semantic_patch(primary, proposal.patch)


class IdeaMemory:
    """Persistent proposal/Idea-Genome journal used as research feedback memory."""

    def __init__(self, path: str | Path = ".researchevolve/ideas.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ideas (
                id TEXT PRIMARY KEY,
                genome TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                generation INTEGER NOT NULL,
                parent_ids TEXT NOT NULL,
                idea_id TEXT NOT NULL,
                rationale TEXT NOT NULL,
                expected_effects TEXT NOT NULL,
                confidence REAL NOT NULL,
                metadata TEXT NOT NULL,
                status TEXT NOT NULL,
                candidate_id TEXT,
                valid INTEGER,
                score REAL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_proposals_generation ON proposals(generation);
            CREATE INDEX IF NOT EXISTS idx_proposals_candidate ON proposals(candidate_id);
            """
        )
        self.conn.commit()

    def record_proposal(self, proposal: ResearchProposal, generation: int) -> str:
        proposal.validate()
        self.conn.execute(
            "INSERT OR REPLACE INTO ideas VALUES (?, ?, ?)",
            (proposal.genome.id, json.dumps(proposal.genome.to_dict(), sort_keys=True), proposal.created_at),
        )
        self.conn.execute(
            """
            INSERT OR REPLACE INTO proposals
            (id, kind, generation, parent_ids, idea_id, rationale, expected_effects,
             confidence, metadata, status, candidate_id, valid, score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal.id,
                proposal.kind,
                generation,
                json.dumps(proposal.parent_ids),
                proposal.genome.id,
                proposal.rationale,
                json.dumps(proposal.expected_effects, sort_keys=True),
                proposal.confidence,
                json.dumps(proposal.metadata, sort_keys=True),
                "proposed",
                None,
                None,
                None,
                proposal.created_at,
            ),
        )
        self.conn.commit()
        return proposal.genome.id

    def record_outcome(self, proposal_id: str, candidate_id: str, valid: bool, score: float | None) -> None:
        status = "accepted" if valid else "rejected"
        self.conn.execute(
            "UPDATE proposals SET status = ?, candidate_id = ?, valid = ?, score = ? WHERE id = ?",
            (status, candidate_id, int(valid), score, proposal_id),
        )
        self.conn.commit()

    def mark_invalid(self, proposal_id: str, reason: str) -> None:
        row = self.conn.execute("SELECT metadata FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None:
            return
        metadata = json.loads(row["metadata"])
        metadata["realization_error"] = reason
        self.conn.execute(
            "UPDATE proposals SET status = 'invalid', metadata = ? WHERE id = ?",
            (json.dumps(metadata, sort_keys=True), proposal_id),
        )
        self.conn.commit()

    def genome_for_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT i.genome FROM proposals p JOIN ideas i ON i.id = p.idea_id
            WHERE p.candidate_id = ? ORDER BY p.created_at DESC LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
        return None if row is None else json.loads(row["genome"])

    def recent_feedback(self, limit: int = 12) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT p.id, p.kind, p.generation, p.parent_ids, p.rationale,
                   p.expected_effects, p.confidence, p.status, p.candidate_id,
                   p.valid, p.score, i.genome
            FROM proposals p JOIN ideas i ON i.id = p.idea_id
            ORDER BY p.created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._decode_feedback(row) for row in rows]

    def list_ideas(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM ideas ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": row["id"], "genome": json.loads(row["genome"]), "created_at": row["created_at"]} for row in rows]

    def list_proposals(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT p.*, i.genome FROM proposals p JOIN ideas i ON i.id = p.idea_id
            ORDER BY p.created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._decode_feedback(row) | {"metadata": json.loads(row["metadata"])} for row in rows]

    @staticmethod
    def _decode_feedback(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "proposal_id": row["id"],
            "kind": row["kind"],
            "generation": row["generation"],
            "parent_ids": json.loads(row["parent_ids"]),
            "rationale": row["rationale"],
            "expected_effects": json.loads(row["expected_effects"]),
            "confidence": row["confidence"],
            "status": row["status"],
            "candidate_id": row["candidate_id"],
            "valid": None if row["valid"] is None else bool(row["valid"]),
            "score": row["score"],
            "genome": json.loads(row["genome"]),
        }

    def close(self) -> None:
        self.conn.close()
