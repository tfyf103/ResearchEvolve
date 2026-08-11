from __future__ import annotations

import json
import operator
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from numbers import Real
from pathlib import Path
from typing import Any, Iterable, Literal

from .candidates import Candidate

PredicateOperator = Literal["lt", "le", "gt", "ge", "eq", "ne"]
ValueSource = Literal["score", "payload", "metrics", "behavior"]
ConjectureStatus = Literal["proposed", "empirically_supported", "refuted", "invalid"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lookup_path(value: Any, path: str) -> Any:
    current = value
    if not path:
        return current
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


@dataclass(slots=True)
class ValueRef:
    """Safe reference to a candidate field; never executes arbitrary expressions."""

    source: ValueSource
    key: str = ""
    scale: float = 1.0
    offset: float = 0.0

    def validate(self) -> None:
        if self.source not in {"score", "payload", "metrics", "behavior"}:
            raise ValueError(f"unsupported value source: {self.source!r}")
        if self.source != "score" and not self.key:
            raise ValueError(f"{self.source} references require a key")

    def resolve(self, candidate: Candidate) -> Any | None:
        self.validate()
        if self.source == "score":
            value: Any = candidate.score
        else:
            container = getattr(candidate, self.source)
            try:
                value = _lookup_path(container, self.key)
            except KeyError:
                return None
        if value is None:
            return None
        if self.scale != 1.0 or self.offset != 0.0:
            if not isinstance(value, Real) or isinstance(value, bool):
                return None
            return float(value) * self.scale + self.offset
        return value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ValueRef":
        ref = cls(
            source=str(data.get("source", "")),  # type: ignore[arg-type]
            key=str(data.get("key", "")),
            scale=float(data.get("scale", 1.0)),
            offset=float(data.get("offset", 0.0)),
        )
        ref.validate()
        return ref


@dataclass(slots=True)
class Predicate:
    """A deliberately small, auditable predicate DSL for empirical conjectures."""

    left: ValueRef
    operator: PredicateOperator
    right_constant: Any = None
    right_ref: ValueRef | None = None

    def validate(self) -> None:
        self.left.validate()
        if self.operator not in {"lt", "le", "gt", "ge", "eq", "ne"}:
            raise ValueError(f"unsupported predicate operator: {self.operator!r}")
        if self.right_ref is not None:
            self.right_ref.validate()
        elif self.right_constant is None:
            raise ValueError("predicate requires right_constant or right_ref")

    def evaluate(self, candidate: Candidate) -> bool | None:
        self.validate()
        left = self.left.resolve(candidate)
        right = self.right_ref.resolve(candidate) if self.right_ref is not None else self.right_constant
        if left is None or right is None:
            return None
        functions = {
            "lt": operator.lt,
            "le": operator.le,
            "gt": operator.gt,
            "ge": operator.ge,
            "eq": operator.eq,
            "ne": operator.ne,
        }
        try:
            return bool(functions[self.operator](left, right))
        except TypeError:
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "left": self.left.to_dict(),
            "operator": self.operator,
            "right_constant": self.right_constant,
            "right_ref": None if self.right_ref is None else self.right_ref.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Predicate":
        predicate = cls(
            left=ValueRef.from_dict(dict(data.get("left") or {})),
            operator=str(data.get("operator", "")),  # type: ignore[arg-type]
            right_constant=data.get("right_constant"),
            right_ref=ValueRef.from_dict(dict(data["right_ref"])) if data.get("right_ref") is not None else None,
        )
        predicate.validate()
        return predicate


@dataclass(slots=True)
class Observation:
    kind: str
    statement: str
    generation: int
    evidence_candidate_ids: list[str]
    data: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    id: str = field(default_factory=lambda: f"observation-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Conjecture:
    statement: str
    predicate: Predicate
    observation_ids: list[str] = field(default_factory=list)
    evidence_candidate_ids: list[str] = field(default_factory=list)
    parent_conjecture_ids: list[str] = field(default_factory=list)
    rationale: str = ""
    confidence: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"conjecture-{uuid.uuid4().hex}")
    status: ConjectureStatus = "proposed"
    created_at: str = field(default_factory=_utcnow)

    def validate(self) -> None:
        if not self.statement.strip():
            raise ValueError("conjecture statement must not be empty")
        self.predicate.validate()
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("conjecture confidence must be in [0, 1]")
        if self.status not in {"proposed", "empirically_supported", "refuted", "invalid"}:
            raise ValueError(f"unsupported conjecture status: {self.status!r}")
        if len(set(self.parent_conjecture_ids)) != len(self.parent_conjecture_ids):
            raise ValueError("parent_conjecture_ids must be distinct")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "predicate": self.predicate.to_dict(),
            "observation_ids": list(self.observation_ids),
            "evidence_candidate_ids": list(self.evidence_candidate_ids),
            "parent_conjecture_ids": list(self.parent_conjecture_ids),
            "rationale": self.rationale,
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Conjecture":
        conjecture = cls(
            id=str(data.get("id") or f"conjecture-{uuid.uuid4().hex}"),
            statement=str(data.get("statement", "")),
            predicate=Predicate.from_dict(dict(data.get("predicate") or {})),
            observation_ids=[str(item) for item in data.get("observation_ids", [])],
            evidence_candidate_ids=[str(item) for item in data.get("evidence_candidate_ids", [])],
            parent_conjecture_ids=[str(item) for item in data.get("parent_conjecture_ids", [])],
            rationale=str(data.get("rationale", "")),
            confidence=float(data.get("confidence", 0.5)),
            metadata=dict(data.get("metadata", {})),
            status="proposed",
            created_at=str(data.get("created_at", _utcnow())),
        )
        conjecture.validate()
        return conjecture


@dataclass(slots=True)
class Counterexample:
    conjecture_id: str
    candidate_id: str
    generation: int
    source: str
    payload: dict[str, Any]
    metrics: dict[str, float]
    score: float | None
    id: str = field(default_factory=lambda: f"counterexample-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ObservationExtractor:
    """Deterministically summarize evaluated candidate data into reusable observations."""

    def extract(self, candidates: Iterable[Candidate], generation: int, limit: int = 12) -> list[Observation]:
        valid = [candidate for candidate in candidates if candidate.valid]
        if not valid or limit < 1:
            return []
        evidence = [candidate.id for candidate in valid[:50]]
        confidence = min(0.99, 0.5 + 0.04 * len(valid))
        observations: list[Observation] = []

        scores = [float(candidate.score) for candidate in valid if candidate.score is not None]
        if scores:
            observations.append(
                Observation(
                    kind="score_range",
                    statement=f"Observed canonical score range [{min(scores):.6g}, {max(scores):.6g}] across {len(scores)} valid candidates.",
                    generation=generation,
                    evidence_candidate_ids=evidence,
                    data={"minimum": min(scores), "maximum": max(scores), "count": len(scores)},
                    confidence=confidence,
                )
            )

        metric_keys = sorted({key for candidate in valid for key in candidate.metrics})
        for key in metric_keys:
            values = [candidate.metrics[key] for candidate in valid if key in candidate.metrics]
            numeric = [float(value) for value in values if isinstance(value, Real) and not isinstance(value, bool)]
            if not numeric:
                continue
            observations.append(
                Observation(
                    kind="metric_range",
                    statement=f"Metric {key} was observed in [{min(numeric):.6g}, {max(numeric):.6g}] across {len(numeric)} candidates.",
                    generation=generation,
                    evidence_candidate_ids=evidence,
                    data={"metric": key, "minimum": min(numeric), "maximum": max(numeric), "count": len(numeric)},
                    confidence=confidence,
                )
            )
            if len(observations) >= limit:
                return observations[:limit]

        behavior_keys = sorted({key for candidate in valid for key in candidate.behavior})
        for key in behavior_keys:
            values = []
            for candidate in valid:
                if key not in candidate.behavior:
                    continue
                value = candidate.behavior[key]
                encoded = json.dumps(value, sort_keys=True, ensure_ascii=False)
                if encoded not in values:
                    values.append(encoded)
            if not values:
                continue
            observations.append(
                Observation(
                    kind="behavior_values",
                    statement=f"Behavior dimension {key} exhibited {len(values)} distinct observed values.",
                    generation=generation,
                    evidence_candidate_ids=evidence,
                    data={"behavior": key, "values": [json.loads(value) for value in values[:20]]},
                    confidence=confidence,
                )
            )
            if len(observations) >= limit:
                break
        return observations[:limit]


class ConjectureMemory:
    """Persistent observation/conjecture/test/counterexample journal."""

    def __init__(self, path: str | Path = ".researchevolve/conjectures.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY,
                generation INTEGER NOT NULL,
                kind TEXT NOT NULL,
                statement TEXT NOT NULL,
                evidence_candidate_ids TEXT NOT NULL,
                data TEXT NOT NULL,
                confidence REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conjectures (
                id TEXT PRIMARY KEY,
                generation INTEGER NOT NULL,
                statement TEXT NOT NULL,
                predicate TEXT NOT NULL,
                observation_ids TEXT NOT NULL,
                evidence_candidate_ids TEXT NOT NULL,
                parent_conjecture_ids TEXT NOT NULL,
                rationale TEXT NOT NULL,
                confidence REAL NOT NULL,
                metadata TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conjecture_tests (
                conjecture_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                supported INTEGER NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(conjecture_id, candidate_id)
            );
            CREATE TABLE IF NOT EXISTS counterexamples (
                id TEXT PRIMARY KEY,
                conjecture_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                source TEXT NOT NULL,
                payload TEXT NOT NULL,
                metrics TEXT NOT NULL,
                score REAL,
                created_at TEXT NOT NULL,
                UNIQUE(conjecture_id, candidate_id)
            );
            CREATE INDEX IF NOT EXISTS idx_observations_generation ON observations(generation);
            CREATE INDEX IF NOT EXISTS idx_conjectures_generation ON conjectures(generation);
            CREATE INDEX IF NOT EXISTS idx_tests_conjecture ON conjecture_tests(conjecture_id);
            CREATE INDEX IF NOT EXISTS idx_counterexamples_conjecture ON counterexamples(conjecture_id);
            """
        )
        self.conn.commit()

    def record_observation(self, observation: Observation) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                observation.id,
                observation.generation,
                observation.kind,
                observation.statement,
                json.dumps(observation.evidence_candidate_ids),
                json.dumps(observation.data, sort_keys=True),
                observation.confidence,
                observation.created_at,
            ),
        )
        self.conn.commit()

    def record_conjecture(self, conjecture: Conjecture, generation: int) -> None:
        conjecture.validate()
        now = _utcnow()
        self.conn.execute(
            """
            INSERT OR REPLACE INTO conjectures
            (id, generation, statement, predicate, observation_ids, evidence_candidate_ids,
             parent_conjecture_ids, rationale, confidence, metadata, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conjecture.id,
                generation,
                conjecture.statement,
                json.dumps(conjecture.predicate.to_dict(), sort_keys=True),
                json.dumps(conjecture.observation_ids),
                json.dumps(conjecture.evidence_candidate_ids),
                json.dumps(conjecture.parent_conjecture_ids),
                conjecture.rationale,
                conjecture.confidence,
                json.dumps(conjecture.metadata, sort_keys=True),
                conjecture.status,
                conjecture.created_at,
                now,
            ),
        )
        self.conn.commit()

    def mark_invalid(self, conjecture_id: str, reason: str) -> None:
        row = self.conn.execute("SELECT metadata FROM conjectures WHERE id = ?", (conjecture_id,)).fetchone()
        if row is None:
            return
        metadata = json.loads(row["metadata"])
        metadata["invalid_reason"] = reason
        self.conn.execute(
            "UPDATE conjectures SET status = 'invalid', metadata = ?, updated_at = ? WHERE id = ?",
            (json.dumps(metadata, sort_keys=True), _utcnow(), conjecture_id),
        )
        self.conn.commit()

    def record_test(self, conjecture_id: str, candidate_id: str, generation: int, supported: bool, source: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO conjecture_tests VALUES (?, ?, ?, ?, ?, ?)",
            (conjecture_id, candidate_id, generation, int(supported), source, _utcnow()),
        )
        self.conn.commit()

    def record_counterexample(self, counterexample: Counterexample) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO counterexamples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                counterexample.id,
                counterexample.conjecture_id,
                counterexample.candidate_id,
                counterexample.generation,
                counterexample.source,
                json.dumps(counterexample.payload, sort_keys=True),
                json.dumps(counterexample.metrics, sort_keys=True),
                counterexample.score,
                counterexample.created_at,
            ),
        )
        self.conn.commit()

    def refresh_status(self, conjecture_id: str, min_evidence: int = 3) -> str:
        row = self.conn.execute("SELECT status FROM conjectures WHERE id = ?", (conjecture_id,)).fetchone()
        if row is None:
            raise KeyError(conjecture_id)
        if row["status"] == "invalid":
            return "invalid"
        counts = self.conn.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN supported = 0 THEN 1 ELSE 0 END) AS failures FROM conjecture_tests WHERE conjecture_id = ?",
            (conjecture_id,),
        ).fetchone()
        total = int(counts["total"] or 0)
        failures = int(counts["failures"] or 0)
        status = "refuted" if failures else "empirically_supported" if total >= min_evidence else "proposed"
        self.conn.execute(
            "UPDATE conjectures SET status = ?, updated_at = ? WHERE id = ?",
            (status, _utcnow(), conjecture_id),
        )
        self.conn.commit()
        return status

    def recent_observations(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM observations ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._decode_observation(row) for row in rows]

    def recent_conjectures(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM conjectures ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._decode_conjecture(row) for row in rows]

    def list_counterexamples(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM counterexamples ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [
            {
                "id": row["id"],
                "conjecture_id": row["conjecture_id"],
                "candidate_id": row["candidate_id"],
                "generation": row["generation"],
                "source": row["source"],
                "payload": json.loads(row["payload"]),
                "metrics": json.loads(row["metrics"]),
                "score": row["score"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def stats(self) -> dict[str, int]:
        observations = int(self.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
        conjectures = int(self.conn.execute("SELECT COUNT(*) FROM conjectures").fetchone()[0])
        refuted = int(self.conn.execute("SELECT COUNT(*) FROM conjectures WHERE status = 'refuted'").fetchone()[0])
        supported = int(self.conn.execute("SELECT COUNT(*) FROM conjectures WHERE status = 'empirically_supported'").fetchone()[0])
        counterexamples = int(self.conn.execute("SELECT COUNT(*) FROM counterexamples").fetchone()[0])
        return {
            "observations": observations,
            "conjectures": conjectures,
            "refuted": refuted,
            "empirically_supported": supported,
            "counterexamples": counterexamples,
        }

    def prune_after_generation(self, generation: int, min_evidence: int = 3) -> None:
        affected = [
            row[0]
            for row in self.conn.execute(
                "SELECT DISTINCT conjecture_id FROM conjecture_tests WHERE generation > ?",
                (generation,),
            ).fetchall()
        ]
        self.conn.execute("DELETE FROM counterexamples WHERE generation > ?", (generation,))
        self.conn.execute("DELETE FROM conjecture_tests WHERE generation > ?", (generation,))
        self.conn.execute("DELETE FROM conjectures WHERE generation > ?", (generation,))
        self.conn.execute("DELETE FROM observations WHERE generation > ?", (generation,))
        self.conn.commit()
        for conjecture_id in affected:
            if self.conn.execute("SELECT 1 FROM conjectures WHERE id = ?", (conjecture_id,)).fetchone() is not None:
                self.refresh_status(conjecture_id, min_evidence=min_evidence)

    @staticmethod
    def _decode_observation(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "generation": row["generation"],
            "kind": row["kind"],
            "statement": row["statement"],
            "evidence_candidate_ids": json.loads(row["evidence_candidate_ids"]),
            "data": json.loads(row["data"]),
            "confidence": row["confidence"],
            "created_at": row["created_at"],
        }

    def _decode_conjecture(self, row: sqlite3.Row) -> dict[str, Any]:
        counts = self.conn.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN supported = 1 THEN 1 ELSE 0 END) AS supports FROM conjecture_tests WHERE conjecture_id = ?",
            (row["id"],),
        ).fetchone()
        return {
            "id": row["id"],
            "generation": row["generation"],
            "statement": row["statement"],
            "predicate": json.loads(row["predicate"]),
            "observation_ids": json.loads(row["observation_ids"]),
            "evidence_candidate_ids": json.loads(row["evidence_candidate_ids"]),
            "parent_conjecture_ids": json.loads(row["parent_conjecture_ids"]),
            "rationale": row["rationale"],
            "confidence": row["confidence"],
            "metadata": json.loads(row["metadata"]),
            "status": row["status"],
            "tests": int(counts["total"] or 0),
            "supports": int(counts["supports"] or 0),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def close(self) -> None:
        self.conn.close()
