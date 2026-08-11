from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class Candidate:
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    parent_ids: list[str] = field(default_factory=list)
    mutation_level: str = "seed"
    generation: int = 0
    valid: bool | None = None
    score: float | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    behavior: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CandidateDB:
    """SQLite-backed append/update store for candidate lineage and scores."""

    def __init__(self, path: str | Path = ".researchevolve/candidates.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candidates (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                parent_ids TEXT NOT NULL,
                mutation_level TEXT NOT NULL,
                generation INTEGER NOT NULL,
                valid INTEGER,
                score REAL,
                metrics TEXT NOT NULL,
                behavior TEXT NOT NULL,
                diagnostics TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_score ON candidates(score)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_generation ON candidates(generation)")
        self.conn.commit()

    def upsert(self, candidate: Candidate) -> None:
        self.conn.execute(
            """
            INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                payload=excluded.payload,
                parent_ids=excluded.parent_ids,
                mutation_level=excluded.mutation_level,
                generation=excluded.generation,
                valid=excluded.valid,
                score=excluded.score,
                metrics=excluded.metrics,
                behavior=excluded.behavior,
                diagnostics=excluded.diagnostics
            """,
            (
                candidate.id,
                json.dumps(candidate.payload, sort_keys=True),
                json.dumps(candidate.parent_ids),
                candidate.mutation_level,
                candidate.generation,
                None if candidate.valid is None else int(candidate.valid),
                candidate.score,
                json.dumps(candidate.metrics, sort_keys=True),
                json.dumps(candidate.behavior, sort_keys=True),
                json.dumps(candidate.diagnostics, sort_keys=True),
                candidate.created_at,
            ),
        )
        self.conn.commit()

    def get(self, candidate_id: str) -> Candidate | None:
        row = self.conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        return self._decode(row) if row else None

    def best(self, limit: int = 10) -> list[Candidate]:
        rows = self.conn.execute(
            "SELECT * FROM candidates WHERE valid = 1 AND score IS NOT NULL ORDER BY score DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._decode(row) for row in rows]

    def all(self) -> list[Candidate]:
        rows = self.conn.execute("SELECT * FROM candidates ORDER BY created_at").fetchall()
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row: sqlite3.Row) -> Candidate:
        return Candidate(
            id=row["id"],
            payload=json.loads(row["payload"]),
            parent_ids=json.loads(row["parent_ids"]),
            mutation_level=row["mutation_level"],
            generation=row["generation"],
            valid=None if row["valid"] is None else bool(row["valid"]),
            score=row["score"],
            metrics=json.loads(row["metrics"]),
            behavior=json.loads(row["behavior"]),
            diagnostics=json.loads(row["diagnostics"]),
            created_at=row["created_at"],
        )

    def close(self) -> None:
        self.conn.close()
