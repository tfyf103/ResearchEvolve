from __future__ import annotations

import math
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .formal_corpus import CorpusPremise, FormalCorpus, tokenize


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True, frozen=True)
class GoalScoredPremise:
    premise: CorpusPremise
    score: float
    matched_tokens: tuple[str, ...]
    module_distance: int
    lexical_score: float
    name_bonus: float
    locality_bonus: float

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.premise.to_dict(),
            "score": self.score,
            "matched_tokens": list(self.matched_tokens),
            "module_distance": self.module_distance,
            "score_components": {
                "lexical": self.lexical_score,
                "name_bonus": self.name_bonus,
                "locality_bonus": self.locality_bonus,
            },
        }


@dataclass(slots=True)
class GoalPremiseSelection:
    formal_spec_id: str
    query: str
    root_imports: list[str]
    reachable_modules: int
    corpus_fingerprint: str
    selected: list[GoalScoredPremise]
    diagnostics_included: bool = False
    id: str = field(default_factory=lambda: f"goal-premise-selection-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "formal_spec_id": self.formal_spec_id,
            "query": self.query,
            "root_imports": list(self.root_imports),
            "reachable_modules": self.reachable_modules,
            "corpus_fingerprint": self.corpus_fingerprint,
            "diagnostics_included": self.diagnostics_included,
            "selected": [item.to_dict() for item in self.selected],
            "created_at": self.created_at,
        }


class GoalPremiseSelector:
    """Deterministic goal/diagnostic-conditioned retrieval over frozen import closure."""

    def __init__(self, corpus: FormalCorpus, *, limit: int = 16, candidate_limit: int = 512) -> None:
        if limit < 1 or candidate_limit < limit:
            raise ValueError("goal premise selection limits are invalid")
        self.corpus = corpus
        self.limit = int(limit)
        self.candidate_limit = int(candidate_limit)

    @property
    def name(self) -> str:
        return (
            f"goal-premise-selector:{self.corpus.fingerprint[:16]}:"
            f"limit={self.limit}:candidates={self.candidate_limit}"
        )

    def select(
        self,
        *,
        formal_spec_id: str,
        query: str,
        root_imports: Sequence[str],
        diagnostics: str = "",
    ) -> GoalPremiseSelection:
        roots = [str(item) for item in root_imports if str(item)]
        reachable = self.corpus.reachable_modules(roots)
        combined_query = query if not diagnostics.strip() else f"{query}\n{diagnostics}"
        query_tokens = set(tokenize(combined_query))
        candidates = self.corpus.candidate_premises(
            query_tokens,
            reachable,
            candidate_limit=self.candidate_limit,
        )
        total_docs = max(1, self.corpus.info.premises)
        scores: list[GoalScoredPremise] = []
        for premise in candidates:
            premise_tokens = set(tokenize(f"{premise.name} {premise.statement}"))
            matched = sorted(query_tokens & premise_tokens)
            if not matched:
                continue
            lexical = sum(
                math.log((total_docs + 1) / (self.corpus.token_document_frequency(token) + 1)) + 1.0
                for token in matched
            )
            name_tokens = set(tokenize(premise.name))
            name_bonus = 0.9 * len(query_tokens & name_tokens)
            distance = int(reachable.get(premise.module, 10_000))
            locality_bonus = 1.5 / (1.0 + distance)
            if premise.source_kind == "project":
                locality_bonus += 0.15
            score = lexical + name_bonus + locality_bonus
            scores.append(
                GoalScoredPremise(
                    premise=premise,
                    score=round(score, 6),
                    matched_tokens=tuple(matched),
                    module_distance=distance,
                    lexical_score=round(lexical, 6),
                    name_bonus=round(name_bonus, 6),
                    locality_bonus=round(locality_bonus, 6),
                )
            )
        scores.sort(key=lambda item: (-item.score, item.module_distance, item.premise.name, item.premise.module))
        return GoalPremiseSelection(
            formal_spec_id=formal_spec_id,
            query=combined_query,
            root_imports=roots,
            reachable_modules=len(reachable),
            corpus_fingerprint=self.corpus.fingerprint,
            selected=scores[: self.limit],
            diagnostics_included=bool(diagnostics.strip()),
        )


class GoalRetrievalMemory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS goal_premise_selections (
                id TEXT PRIMARY KEY,
                formal_spec_id TEXT NOT NULL,
                corpus_fingerprint TEXT NOT NULL,
                diagnostics_included INTEGER NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_goal_selection_spec
                ON goal_premise_selections(formal_spec_id);
            """
        )
        self.conn.commit()

    def record(self, selection: GoalPremiseSelection) -> None:
        import json
        self.conn.execute(
            "INSERT OR REPLACE INTO goal_premise_selections VALUES (?, ?, ?, ?, ?, ?)",
            (
                selection.id,
                selection.formal_spec_id,
                selection.corpus_fingerprint,
                int(selection.diagnostics_included),
                json.dumps(selection.to_dict(), sort_keys=True),
                selection.created_at,
            ),
        )
        self.conn.commit()

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        import json
        rows = self.conn.execute(
            "SELECT payload FROM goal_premise_selections ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def close(self) -> None:
        self.conn.close()
