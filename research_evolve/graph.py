from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
