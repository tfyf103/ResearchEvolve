from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ResearchNode:
    type: str
    statement: str
    status: str = "open"
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=_utcnow)


class ResearchGraph:
    """Persistent lineage graph for hypotheses, programs, experiments and results."""

    def __init__(self, path: str | Path = ".researchevolve/research_graph.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                statement TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS edges (
                source_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                target_id TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(source_id, relation, target_id)
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
            """
        )
        self.conn.commit()

    def add_node(self, node: ResearchNode) -> str:
        self.conn.execute(
            "INSERT OR REPLACE INTO nodes VALUES (?, ?, ?, ?, ?, ?)",
            (node.id, node.type, node.statement, node.status, json.dumps(node.payload, sort_keys=True), node.created_at),
        )
        self.conn.commit()
        return node.id

    def add_edge(self, source_id: str, relation: str, target_id: str, metadata: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO edges VALUES (?, ?, ?, ?, ?)",
            (source_id, relation, target_id, json.dumps(metadata or {}, sort_keys=True), _utcnow()),
        )
        self.conn.commit()

    def neighbors(self, node_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT e.relation, e.target_id, n.type, n.statement, n.status, n.payload
            FROM edges e JOIN nodes n ON n.id = e.target_id
            WHERE e.source_id = ?
            ORDER BY e.created_at
            """,
            (node_id,),
        ).fetchall()
        return [
            {
                "relation": row["relation"],
                "id": row["target_id"],
                "type": row["type"],
                "statement": row["statement"],
                "status": row["status"],
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    def prune_after_generation(self, generation: int, candidate_ids: Iterable[str] = ()) -> list[str]:
        """Remove graph artifacts newer than a restored checkpoint.

        Generation-bearing nodes are removed directly. Evaluation nodes linked to
        removed candidates and Idea nodes used only by removed proposals are also
        removed so the graph does not expose partial-generation ghost results.
        """

        removable = {str(candidate_id) for candidate_id in candidate_ids}
        rows = self.conn.execute("SELECT id, type, payload FROM nodes").fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError):
                continue
            node_generation = payload.get("generation") if isinstance(payload, dict) else None
            if isinstance(node_generation, int) and node_generation > generation:
                removable.add(str(row["id"]))

        if removable:
            placeholders = ",".join("?" for _ in removable)
            evaluation_rows = self.conn.execute(
                f"SELECT target_id FROM edges WHERE relation = 'evaluated_as' AND source_id IN ({placeholders})",
                tuple(removable),
            ).fetchall()
            removable.update(str(row["target_id"]) for row in evaluation_rows)

        proposal_ids = {
            node_id
            for node_id in removable
            if self.conn.execute("SELECT type FROM nodes WHERE id = ?", (node_id,)).fetchone() is not None
            and self.conn.execute("SELECT type FROM nodes WHERE id = ?", (node_id,)).fetchone()[0] == "proposal"
        }
        if proposal_ids:
            placeholders = ",".join("?" for _ in proposal_ids)
            idea_rows = self.conn.execute(
                f"SELECT DISTINCT source_id FROM edges WHERE relation = 'proposed_as' AND target_id IN ({placeholders})",
                tuple(proposal_ids),
            ).fetchall()
            for row in idea_rows:
                idea_id = str(row["source_id"])
                remaining = self.conn.execute(
                    "SELECT target_id FROM edges WHERE relation = 'proposed_as' AND source_id = ?",
                    (idea_id,),
                ).fetchall()
                if all(str(edge["target_id"]) in removable for edge in remaining):
                    removable.add(idea_id)

        if not removable:
            return []
        placeholders = ",".join("?" for _ in removable)
        params = tuple(removable)
        self.conn.execute(
            f"DELETE FROM edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
            params + params,
        )
        self.conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", params)
        self.conn.commit()
        return sorted(removable)

    def export(self) -> dict[str, list[dict[str, Any]]]:
        nodes = [dict(row) for row in self.conn.execute("SELECT * FROM nodes ORDER BY created_at")]
        edges = [dict(row) for row in self.conn.execute("SELECT * FROM edges ORDER BY created_at")]
        for node in nodes:
            node["payload"] = json.loads(node["payload"])
        for edge in edges:
            edge["metadata"] = json.loads(edge["metadata"])
        return {"nodes": nodes, "edges": edges}

    def close(self) -> None:
        self.conn.close()
