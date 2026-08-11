from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .conjectures import Conjecture

ProofStatus = Literal[
    "planned",
    "drafted",
    "verified_natural_language",
    "rejected",
    "inconclusive",
    "invalid",
]
ReviewDecision = Literal["verified", "rejected", "inconclusive"]
IssueSeverity = Literal["error", "warning", "note"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ProofSpec:
    """Frozen target contract for one proof attempt."""

    conjecture_id: str
    statement: str
    predicate: dict[str, Any]
    assumptions: list[str] = field(default_factory=list)
    evidence_candidate_ids: list[str] = field(default_factory=list)
    generation: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"proof-spec-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utcnow)

    @classmethod
    def from_conjecture(
        cls,
        conjecture: Conjecture,
        *,
        generation: int,
        assumptions: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ProofSpec":
        return cls(
            conjecture_id=conjecture.id,
            statement=conjecture.statement,
            predicate=conjecture.predicate.to_dict(),
            assumptions=list(assumptions or []),
            evidence_candidate_ids=list(conjecture.evidence_candidate_ids),
            generation=generation,
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LemmaSpec:
    label: str
    statement: str
    depends_on: list[str] = field(default_factory=list)
    role: str = "supporting"
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"lemma-{uuid.uuid4().hex}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LemmaSpec":
        return cls(
            label=str(data.get("label", "")).strip(),
            statement=str(data.get("statement", "")).strip(),
            depends_on=[str(item) for item in data.get("depends_on", [])],
            role=str(data.get("role", "supporting")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class ProofPlan:
    proof_spec_id: str
    strategy: str
    lemmas: list[LemmaSpec]
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"proof-plan-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utcnow)

    def validate(self, max_lemmas: int = 32) -> None:
        if not self.strategy.strip():
            raise ValueError("proof plan requires a non-empty strategy")
        if not self.lemmas:
            raise ValueError("proof plan requires at least one lemma")
        if len(self.lemmas) > max_lemmas:
            raise ValueError(f"proof plan exceeds max_lemmas={max_lemmas}")

        labels = [lemma.label for lemma in self.lemmas]
        if any(not label for label in labels):
            raise ValueError("every lemma requires a non-empty label")
        if len(set(labels)) != len(labels):
            raise ValueError("lemma labels must be unique within a proof plan")
        if any(not lemma.statement for lemma in self.lemmas):
            raise ValueError("every lemma requires a non-empty statement")

        available = set(labels)
        for lemma in self.lemmas:
            unknown = [label for label in lemma.depends_on if label not in available]
            if unknown:
                raise ValueError(f"lemma {lemma.label!r} depends on unknown labels: {unknown}")
            if lemma.label in lemma.depends_on:
                raise ValueError(f"lemma {lemma.label!r} cannot depend on itself")

        graph = {lemma.label: list(lemma.depends_on) for lemma in self.lemmas}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(label: str) -> None:
            if label in visited:
                return
            if label in visiting:
                raise ValueError("proof plan lemma dependency graph contains a cycle")
            visiting.add(label)
            for dependency in graph[label]:
                visit(dependency)
            visiting.remove(label)
            visited.add(label)

        for label in labels:
            visit(label)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProofArtifact:
    proof_spec_id: str
    proof_plan_id: str
    lemma_arguments: dict[str, str]
    final_argument: str
    assumptions_used: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"proof-artifact-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utcnow)

    def validate(self, plan: ProofPlan, max_lemmas: int = 32) -> None:
        plan.validate(max_lemmas=max_lemmas)
        expected = {lemma.label for lemma in plan.lemmas}
        supplied = set(self.lemma_arguments)
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected)
        if missing:
            raise ValueError(f"proof artifact is missing lemma arguments: {missing}")
        if extra:
            raise ValueError(f"proof artifact contains unknown lemma labels: {extra}")
        empty = sorted(label for label, text in self.lemma_arguments.items() if not str(text).strip())
        if empty:
            raise ValueError(f"proof artifact has empty lemma arguments: {empty}")
        if not self.final_argument.strip():
            raise ValueError("proof artifact requires a non-empty final_argument")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class VerificationIssue:
    severity: IssueSeverity
    code: str
    message: str
    lemma_label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.severity not in {"error", "warning", "note"}:
            raise ValueError(f"unsupported verification issue severity: {self.severity!r}")
        if not self.code.strip() or not self.message.strip():
            raise ValueError("verification issue requires code and message")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VerificationIssue":
        issue = cls(
            severity=str(data.get("severity", "error")),  # type: ignore[arg-type]
            code=str(data.get("code", "unspecified")),
            message=str(data.get("message", "")),
            lemma_label=None if data.get("lemma_label") is None else str(data.get("lemma_label")),
            metadata=dict(data.get("metadata", {})),
        )
        issue.validate()
        return issue


@dataclass(slots=True)
class ProofReview:
    proof_artifact_id: str
    verifier: str
    decision: ReviewDecision
    issues: list[VerificationIssue] = field(default_factory=list)
    confidence: float = 0.5
    adversarial_notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"proof-review-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utcnow)

    def validate(self) -> None:
        if self.decision not in {"verified", "rejected", "inconclusive"}:
            raise ValueError(f"unsupported proof review decision: {self.decision!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("proof review confidence must be in [0, 1]")
        for issue in self.issues:
            issue.validate()

    def gated_status(self, min_confidence: float = 0.7) -> ProofStatus:
        self.validate()
        if self.decision == "rejected" or any(issue.severity == "error" for issue in self.issues):
            return "rejected"
        if self.decision == "verified" and self.confidence >= min_confidence:
            return "verified_natural_language"
        return "inconclusive"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "proof_artifact_id": self.proof_artifact_id,
            "verifier": self.verifier,
            "decision": self.decision,
            "issues": [issue.to_dict() for issue in self.issues],
            "confidence": self.confidence,
            "adversarial_notes": self.adversarial_notes,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


class ProofMemory:
    """SQLite journal for proof targets, plans, artifacts, and independent reviews."""

    def __init__(self, path: str | Path = ".researchevolve/proofs.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS proof_specs (
                id TEXT PRIMARY KEY,
                conjecture_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                statement TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_proof_specs_conjecture ON proof_specs(conjecture_id);
            CREATE INDEX IF NOT EXISTS idx_proof_specs_generation ON proof_specs(generation);

            CREATE TABLE IF NOT EXISTS proof_plans (
                id TEXT PRIMARY KEY,
                proof_spec_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS proof_artifacts (
                id TEXT PRIMARY KEY,
                proof_spec_id TEXT NOT NULL,
                proof_plan_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS proof_reviews (
                id TEXT PRIMARY KEY,
                proof_artifact_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                payload TEXT NOT NULL,
                gated_status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def record_spec(self, spec: ProofSpec, status: ProofStatus = "planned") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO proof_specs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                spec.id,
                spec.conjecture_id,
                spec.generation,
                spec.statement,
                json.dumps(spec.to_dict(), sort_keys=True),
                status,
                spec.created_at,
            ),
        )
        self.conn.commit()

    def record_plan(self, plan: ProofPlan, generation: int, status: ProofStatus = "planned") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO proof_plans VALUES (?, ?, ?, ?, ?, ?)",
            (
                plan.id,
                plan.proof_spec_id,
                generation,
                json.dumps(plan.to_dict(), sort_keys=True),
                status,
                plan.created_at,
            ),
        )
        self.conn.commit()

    def record_artifact(self, artifact: ProofArtifact, generation: int, status: ProofStatus = "drafted") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO proof_artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                artifact.id,
                artifact.proof_spec_id,
                artifact.proof_plan_id,
                generation,
                json.dumps(artifact.to_dict(), sort_keys=True),
                status,
                artifact.created_at,
            ),
        )
        self.conn.commit()

    def record_review(self, review: ProofReview, generation: int, min_confidence: float = 0.7) -> ProofStatus:
        status = review.gated_status(min_confidence=min_confidence)
        self.conn.execute(
            "INSERT OR REPLACE INTO proof_reviews VALUES (?, ?, ?, ?, ?, ?)",
            (
                review.id,
                review.proof_artifact_id,
                generation,
                json.dumps(review.to_dict(), sort_keys=True),
                status,
                review.created_at,
            ),
        )
        row = self.conn.execute(
            "SELECT proof_spec_id, proof_plan_id FROM proof_artifacts WHERE id = ?",
            (review.proof_artifact_id,),
        ).fetchone()
        self.conn.execute("UPDATE proof_artifacts SET status = ? WHERE id = ?", (status, review.proof_artifact_id))
        if row is not None:
            self.conn.execute("UPDATE proof_specs SET status = ? WHERE id = ?", (status, row["proof_spec_id"]))
            self.conn.execute("UPDATE proof_plans SET status = ? WHERE id = ?", (status, row["proof_plan_id"]))
        self.conn.commit()
        return status

    def mark_spec_invalid(self, proof_spec_id: str) -> None:
        artifact_rows = self.conn.execute(
            "SELECT id FROM proof_artifacts WHERE proof_spec_id = ?",
            (proof_spec_id,),
        ).fetchall()
        artifact_ids = [str(row["id"]) for row in artifact_rows]
        self.conn.execute("UPDATE proof_specs SET status = 'invalid' WHERE id = ?", (proof_spec_id,))
        self.conn.execute("UPDATE proof_plans SET status = 'invalid' WHERE proof_spec_id = ?", (proof_spec_id,))
        self.conn.execute("UPDATE proof_artifacts SET status = 'invalid' WHERE proof_spec_id = ?", (proof_spec_id,))
        if artifact_ids:
            placeholders = ",".join("?" for _ in artifact_ids)
            self.conn.execute(
                f"UPDATE proof_reviews SET gated_status = 'invalid' WHERE proof_artifact_id IN ({placeholders})",
                tuple(artifact_ids),
            )
        self.conn.commit()

    def invalidate_conjecture_proofs(self, conjecture_id: str, reason: str) -> int:
        rows = self.conn.execute(
            "SELECT id, payload FROM proof_specs WHERE conjecture_id = ? AND status != 'invalid'",
            (conjecture_id,),
        ).fetchall()
        proof_spec_ids: list[str] = []
        for row in rows:
            payload = json.loads(row["payload"])
            metadata = dict(payload.get("metadata", {}))
            metadata["invalidation_reason"] = reason
            metadata["invalidated_at"] = _utcnow()
            payload["metadata"] = metadata
            self.conn.execute(
                "UPDATE proof_specs SET status = 'invalid', payload = ? WHERE id = ?",
                (json.dumps(payload, sort_keys=True), row["id"]),
            )
            proof_spec_ids.append(str(row["id"]))

        artifact_ids: list[str] = []
        for proof_spec_id in proof_spec_ids:
            artifact_rows = self.conn.execute(
                "SELECT id FROM proof_artifacts WHERE proof_spec_id = ?",
                (proof_spec_id,),
            ).fetchall()
            artifact_ids.extend(str(row["id"]) for row in artifact_rows)
            self.conn.execute("UPDATE proof_plans SET status = 'invalid' WHERE proof_spec_id = ?", (proof_spec_id,))
            self.conn.execute("UPDATE proof_artifacts SET status = 'invalid' WHERE proof_spec_id = ?", (proof_spec_id,))
        if artifact_ids:
            placeholders = ",".join("?" for _ in artifact_ids)
            self.conn.execute(
                f"UPDATE proof_reviews SET gated_status = 'invalid' WHERE proof_artifact_id IN ({placeholders})",
                tuple(artifact_ids),
            )
        self.conn.commit()
        return len(proof_spec_ids)

    def has_verified_for_conjecture(self, conjecture_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM proof_specs WHERE conjecture_id = ? AND status = 'verified_natural_language' LIMIT 1",
            (conjecture_id,),
        ).fetchone()
        return row is not None

    def list_specs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM proof_specs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._decode_payload_row(row) for row in rows]

    def list_plans(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM proof_plans ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._decode_payload_row(row) for row in rows]

    def list_artifacts(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM proof_artifacts ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._decode_payload_row(row) for row in rows]

    def list_reviews(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM proof_reviews ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload"])
            payload["generation"] = row["generation"]
            payload["gated_status"] = row["gated_status"]
            output.append(payload)
        return output

    @staticmethod
    def _decode_payload_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(row["payload"])
        payload["generation"] = row["generation"]
        payload["status"] = row["status"]
        return payload

    def stats(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for table, key in [
            ("proof_specs", "specs"),
            ("proof_plans", "plans"),
            ("proof_artifacts", "artifacts"),
            ("proof_reviews", "reviews"),
        ]:
            row = self.conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            result[key] = int(row["count"]) if row is not None else 0
        for status in ["verified_natural_language", "rejected", "inconclusive", "invalid"]:
            row = self.conn.execute(
                "SELECT COUNT(*) AS count FROM proof_specs WHERE status = ?",
                (status,),
            ).fetchone()
            result[status] = int(row["count"]) if row is not None else 0
        return result

    def prune_after_generation(self, generation: int) -> dict[str, int]:
        removed: dict[str, int] = {}
        for table in ["proof_reviews", "proof_artifacts", "proof_plans", "proof_specs"]:
            row = self.conn.execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE generation > ?",
                (generation,),
            ).fetchone()
            removed[table] = int(row["count"]) if row is not None else 0
            self.conn.execute(f"DELETE FROM {table} WHERE generation > ?", (generation,))
        self.conn.commit()
        return removed

    def close(self) -> None:
        self.conn.close()
